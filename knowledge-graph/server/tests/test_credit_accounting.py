#!/usr/bin/env python3
"""Usefulness accounting across renames, restarts and maintenance writes.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_credit_accounting.py

Found by the formal pass over credits (formal/credits/):
  1. A rename carries a session's vote: no second endorsement (or second
     maintenance credit) on the renamed node.
  2. A maintenance rename keeps the node's activity time; it used to reset
     the version's used_ts to 0, so recency fell to the epoch.
  3. A session flagged maintenance stays maintenance across a server restart
     and a crash: its reads still promote nothing, its credits stay credits.
  4. A maintenance tidy does not turn the author's next case on its own
     lesson into a note credit; another session's case still is one.
  5. A vote and its ledger entry survive a crash together: the session file
     is saved when the ledger changes, not 30 s later.
  6. An instance-of edge in the maintain graph credits no user lesson: graph
     loads never reconcile the maintain graph, so put_edge must not either.

Uses a temp KG_STORAGE_ROOT and a temp project under ~/.cache.
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


def fresh():
    for p in Path(_TMP_STORAGE).iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    sm = HTTPSessionManager()
    return MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None), sm


def boot():
    sm = HTTPSessionManager()
    return MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None), sm


def crash(store):
    """Stop the store without its final save, as a kill would."""
    store.running = False
    store._stop_event.set()
    store.saver_thread.join(timeout=5)


def recency(store, nid):
    return store.scorer._recency(nid, store.graphs["user"]["nodes"][nid],
                                 store._versions["user"], time.time())


def main():
    project = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    try:
        print("a rename carries the vote:")
        store, sm = fresh()
        work = sm.register(project)["session_id"]
        chore = sm.register(project)["session_id"]
        store.mark_maintenance(chore)
        store.put_node("user", "lesson-a", "a lesson", session_id=work)
        store.mark_useful(["lesson-a"], work)
        store.mark_useful(["lesson-a"], chore, credits=3)
        store.rename_node("lesson-a", "lesson-b", session_id=chore)
        res = store.mark_useful(["lesson-b"], work)
        check("no second endorsement after a rename", not res["accepted"], res)
        res = store.mark_useful(["lesson-b"], chore, credits=3)
        check("no second maintenance credit after a rename", not res["accepted"], res)
        check("the node holds one vote and one credit of 3",
              len(store.graphs["user"]["nodes"]["lesson-b"]["_useful_ts"]) == 4)
        store.shutdown()

        print("a maintenance rename keeps the activity time:")
        store, sm = fresh()
        work = sm.register(project)["session_id"]
        chore = sm.register(project)["session_id"]
        store.mark_maintenance(chore)
        store.put_node("user", "old-name", "a lesson", session_id=work)
        before = recency(store, "old-name")
        time.sleep(0.01)
        store.rename_node("old-name", "new-name", session_id=chore)
        check("recency unchanged", recency(store, "new-name") == before,
              (before, store._versions["user"].get("node:new-name")))
        check("the version still moves", store._versions["user"]["node:new-name"]["v"] == 2)
        store.rename_node("new-name", "newer-name", session_id=work)
        check("a working session's rename is activity", recency(store, "newer-name") > before)
        store.shutdown()

        for how in ("restart", "crash"):
            print(f"the maintenance flag survives a {how}:")
            store, sm = fresh()
            chore = sm.register(project)["session_id"]
            store.mark_maintenance(chore)
            store.put_node("user", "judged", "a lesson the pass judges")
            store.graphs["user"]["nodes"]["judged"]["_archived"] = True
            store.dirty["user"] = True
            store._write_through("user")
            if how == "restart":
                store.shutdown()
            else:
                crash(store)
            store, sm = boot()
            check("still maintenance", store.is_maintenance(chore))
            result = store.read_node("judged", level="user", session_id=chore)
            node = store.graphs["user"]["nodes"]["judged"]
            check("its read promotes nothing", not result["promoted"] and node.get("_archived"))
            check("its read stamps nothing", not node.get("_last_read_ts"))
            res = store.mark_useful(["judged"], chore, credits=2)
            check("its credit is a credit", res["accepted"] == ["judged"] and res["credits"] == 2, res)
            store.shutdown()

        print("a maintenance tidy is not authorship:")
        store, sm = fresh()
        author = sm.register(project)["session_id"]
        chore = sm.register(project)["session_id"]
        other = sm.register(project)["session_id"]
        store.mark_maintenance(chore)
        store.put_node("user", "own", "a lesson", notes=["case 1"], session_id=author)
        sm.note_viewed(chore, ["own"], at=time.time(), full=True)
        store.put_node("user", "own", "a lesson, tidied", notes=["case 1"], session_id=chore)
        sm.note_viewed(author, ["own"], at=time.time(), full=True)
        res = store.put_node("user", "own", "a lesson, tidied", notes=["case 1", "case 2"],
                             session_id=author)
        check("the author's own case is no credit", not res["note_credited"])
        sm.note_viewed(other, ["own"], at=time.time(), full=True)
        res = store.put_node("user", "own", "a lesson, tidied",
                             notes=["case 1", "case 2", "case 3"], session_id=other)
        check("another session's case still is", res["note_credited"])
        store.shutdown()

        print("a crash keeps vote and ledger together:")
        store, sm = fresh()
        work = sm.register(project)["session_id"]
        store.put_node("user", "voted", "a lesson", session_id=work)
        store.mark_useful(["voted"], work)
        crash(store)
        store, sm = boot()
        res = store.mark_useful(["voted"], work)
        check("no second vote after a crash", not res["accepted"], res)
        check("one stamp", len(store.graphs["user"]["nodes"]["voted"]["_useful_ts"]) == 1)
        store.shutdown()

        print("the maintain graph does not credit user lessons:")
        store, sm = fresh()
        store.put_node("user", "user-lesson", "a user lesson")
        time.sleep(0.01)
        store.put_node("maintain", "craft-case", "a craft lesson of the chore agent")
        store.put_edge("maintain", "craft-case", "user-lesson", "instance-of")
        check("no repeat credit from a maintain-level edge",
              not store.graphs["user"]["nodes"]["user-lesson"].get("_useful_ts"))
        store.put_node("maintain", "craft-later", "a later craft case")
        store.graphs["maintain"]["nodes"]["craft-later"]["_created_ts"] += 1
        store.put_edge("maintain", "craft-later", "craft-case", "instance-of")
        check("a repeat inside the maintain graph still credits",
              len(store.graphs["maintain"]["nodes"]["craft-case"].get("_useful_ts", [])) == 1)
        store.shutdown()
    finally:
        shutil.rmtree(project, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
