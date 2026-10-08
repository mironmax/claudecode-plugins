#!/usr/bin/env python3
"""Foreign-writes push and the write-time hub nudge's domain-vocabulary ceiling.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_foreign_writes.py

Background (kg-memory-bench, 2026-10-07): five parallel sessions never called
kg_sync and wrote one lesson as four nodes; the write-time hub nudge named an
unrelated node on most writes ('before', 'stone', 'black').

Covers:
  1. Another session's new node is pushed once, with its gist
  2. A session's own writes are never pushed back to it
  3. A node read after its last write is not pushed; a later update is
  4. More than three changes: newest three, plus a pointer to kg_sync
  5. kg_sync still lists what the push only summarised
  6. A term held by over a quarter of the graph names no hub
  7. A stoplisted word names no hub
  8. A case added to another session's lesson counts as that session's
     endorsement — once, and not for its own lessons or a gist-only edit
  9. Only relevant writes are pushed: none from a maintenance session, and
     user-level ones only from a session in the same project

Uses a temp KG_STORAGE_ROOT and temp projects under ~/.cache.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from mcp_http import foreign  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402
from mcp_http.store import GraphConfig, MultiProjectGraphStore  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


def main():
    project = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    other = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    try:
        session_manager = HTTPSessionManager()
        store = MultiProjectGraphStore(GraphConfig(), session_manager)
        a = session_manager.register(project)["session_id"]
        b = session_manager.register(project)["session_id"]
        time.sleep(0.01)

        print("push:")
        check("silent when nothing changed", foreign.notice(store, session_manager, b) is None)
        store.put_node(level="user", node_id="count-before-pass",
                       gist="Count exactly before passing", session_id=a)
        text = foreign.notice(store, session_manager, b)
        check("another session's new node is pushed with its gist",
              text is not None and "count-before-pass (new): Count exactly before passing" in text, text)
        check("pushed once", foreign.notice(store, session_manager, b) is None)

        store.put_node(level="user", node_id="own-lesson", gist="Mine", session_id=b)
        check("own writes are never pushed back", foreign.notice(store, session_manager, b) is None)

        store.put_node(level="user", node_id="read-already", gist="v1", session_id=a)
        session_manager.note_viewed(b, ["read-already"], at=time.time(), full=True)
        check("a node read after its last write is not pushed",
              foreign.notice(store, session_manager, b) is None)
        time.sleep(0.01)
        store.put_node(level="user", node_id="read-already", gist="v2 sharpened", session_id=a)
        text = foreign.notice(store, session_manager, b)
        check("a later update is pushed as updated",
              text is not None and "read-already (updated): v2 sharpened" in text, text)

        for i in range(5):
            store.put_node(level="project", node_id=f"burst-{i}", gist=f"Burst {i}", session_id=a)
            time.sleep(0.01)
        text = foreign.notice(store, session_manager, b) or ""
        check("newest three shown", all(f"burst-{i}" in text for i in (2, 3, 4))
              and "burst-0" not in text, text)
        check("the rest points at kg_sync", "+2 more — kg_sync(session_id)" in text, text)
        diff = store.get_sync_diff(b, session_manager.get_sync_ts(b))
        check("kg_sync still lists every change",
              all(f"burst-{i}" in diff["project"]["nodes"] for i in range(5)))

        print("hub nudge ceiling:")
        # 'stone' in 12 of 24 nodes: domain vocabulary, not an entity.
        for i in range(12):
            store.put_node(level="project", node_id=f"stone-shape-{i}" if i == 0 else f"shape-case-{i}",
                           gist=f"Case {i}: a weak stone shape lost the fight", session_id=a)
        for i in range(7):
            store.put_node(level="project", node_id=f"other-topic-{i}", gist=f"Topic {i} unrelated",
                           session_id=a)
        res = store.put_node(level="project", node_id="new-shape-lesson",
                             gist="Keep each stone connected when the opponent cuts", session_id=a)
        m = res.get("near_duplicate")
        check("terms in over a quarter of the graph name no hub",
              not (m and m.get("kind") == "mention"), m)
        for i in range(6):
            store.put_node(level="project", node_id=f"before-step-{i}" if i == 0 else f"step-note-{i}",
                           gist=f"Step {i} happens before release", session_id=a)
        res = store.put_node(level="project", node_id="release-gate",
                             gist="Run the gate before release", session_id=a)
        m = res.get("near_duplicate")
        check("a stoplisted word names no hub",
              not (m and m.get("kind") == "mention" and m.get("term") == "before"), m)

        print("note credit:")
        store.put_node(level="user", node_id="lesson-x", gist="Lesson X", notes=["case 1"], session_id=a)
        session_manager.note_viewed(b, ["lesson-x"], at=time.time(), full=True)
        res = store.put_node(level="user", node_id="lesson-x", gist="Lesson X",
                             notes=["case 1", "case 2"], session_id=b)
        node = store.graphs["user"]["nodes"]["lesson-x"]
        check("a case added to another session's lesson is credited",
              res.get("note_credited") and len(node.get("_useful_ts", [])) == 1, res.get("note_credited"))
        res = store.put_node(level="user", node_id="lesson-x", gist="Lesson X",
                             notes=["case 1", "case 2", "case 3"], session_id=b)
        check("once per node per session", not res.get("note_credited")
              and len(node.get("_useful_ts", [])) == 1)
        liked = store.mark_useful(["lesson-x"], b)
        check("a later kg_useful from that session is a duplicate", "lesson-x" in liked["rejected"], liked)
        session_manager.note_viewed(a, ["lesson-x"], at=time.time(), full=True)
        res = store.put_node(level="user", node_id="lesson-x", gist="Lesson X",
                             notes=["case 1", "case 2", "case 3", "case 4"], session_id=a)
        check("adding to a lesson last written by someone else counts for the writer too",
              res.get("note_credited"))
        store.put_node(level="user", node_id="own-lesson-y", gist="Y", notes=["c1"], session_id=a)
        res = store.put_node(level="user", node_id="own-lesson-y", gist="Y", notes=["c1", "c2"], session_id=a)
        check("a case added to one's own last write is not credited", not res.get("note_credited"))
        session_manager.note_viewed(b, ["own-lesson-y"], at=time.time(), full=True)
        res = store.put_node(level="user", node_id="own-lesson-y", gist="Y sharpened", notes=["c1", "c2"],
                             session_id=b)
        check("a gist-only edit is not credited", not res.get("note_credited"))

        print("relevance:")
        foreign.notice(store, session_manager, b)  # drain
        elsewhere = session_manager.register(other)["session_id"]
        store.put_node(level="user", node_id="other-project-lesson", gist="From elsewhere",
                       session_id=elsewhere)
        check("a user-level write from another project is not pushed",
              foreign.notice(store, session_manager, b) is None)
        chore = session_manager.register(project)["session_id"]
        store.mark_maintenance(chore)
        store.put_node(level="project", node_id="tidied-by-chore", gist="Reworded", session_id=chore)
        store.put_node(level="user", node_id="renamed-by-chore", gist="Reworded", session_id=chore)
        check("a maintenance session's writes are not pushed",
              foreign.notice(store, session_manager, b) is None)
        store.put_node(level="user", node_id="same-project-lesson", gist="From here", session_id=a)
        text = foreign.notice(store, session_manager, b) or ""
        check("a user-level write from the same project still is",
              "same-project-lesson" in text, text)
    finally:
        shutil.rmtree(project, ignore_errors=True)
        shutil.rmtree(other, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
