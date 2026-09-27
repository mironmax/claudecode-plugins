#!/usr/bin/env python3
"""Tests for writes built on a stale or partial view (F11, formal/FINDINGS.md).

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_concurrent_writes.py

An agent edits a node by read-modify-write, and kg_put_node replaces notes
and touches wholesale. put_node now refuses:
  - a write from a session that last saw the node before another session
    changed it (the lost update the Lean model finds in four steps);
  - a write that replaces stored notes or touches the session never read.
The refusal carries the current node and counts as a full read, so the
merged retry goes through.

Covers:
  1. two sessions read, both add a note: the second is refused, then merges
  2. what does not conflict: own writes, a change made before the view, a
     gist-only put, notes on a node that had none, a promotion by a read
  3. partial views: a gist-only view (preload, recall) cannot replace notes;
     a full read can; a node from before the stamp existed is covered too
  4. timing: a write landing during a render is newer than the view
  5. rename carries view times; editor writes are never refused but do
     count as another writer
  6. the MCP handler: the refusal text shows the node and the retry succeeds
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ.pop("KG_CHORES", None)

from core.constants import WRITTEN_FIELD  # noqa: E402
from core.exceptions import NodeConflictError  # noqa: E402
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
    sm = HTTPSessionManager()
    return MultiProjectGraphStore(GraphConfig(save_interval=9999), sm), sm


def tick():
    time.sleep(0.005)


def read(store, sm, sid, nid):
    """kg_read(ids=[...]): the view time is taken before the read."""
    at = time.time()
    node = store.read_node(nid, session_id=sid)["node"]
    sm.mark_seen(sid, [nid], via="read", at=at, full=True)
    return node


def glance(sm, sid, nid):
    """A gist-only view: preload, full read, recall, search."""
    sm.mark_seen(sid, [nid], via="preload", at=time.time())


def put(store, sid, nid, gist="a gist", notes=None, touches=None):
    try:
        store.put_node("user", nid, gist, notes=notes, touches=touches, session_id=sid)
        return None
    except NodeConflictError as e:
        return e


def notes_of(store, nid):
    return store.graphs["user"]["nodes"][nid].get("notes")


def test_lost_update():
    print("1. two sessions add a note")
    store, sm = fresh()
    a, b = sm.register(None)["session_id"], sm.register(None)["session_id"]
    put(store, b, "deploy", "how deploys work", notes=["base"])
    tick()
    na, nb = read(store, sm, a, "deploy"), read(store, sm, b, "deploy")
    tick()
    check("the first write goes through", put(store, a, "deploy", notes=na["notes"] + ["A"]) is None)
    tick()
    err = put(store, b, "deploy", notes=nb["notes"] + ["B"])
    check("the second, built on the older view, is refused", isinstance(err, NodeConflictError))
    check("the refusal says why and carries the current node",
          err is not None and "another session" in err.reason and err.node["notes"] == ["base", "A"],
          getattr(err, "node", None))
    check("nothing was lost", notes_of(store, "deploy") == ["base", "A"])
    sm.note_viewed(b, ["deploy"], at=time.time(), full=True)   # what the handler does
    tick()
    check("the merged retry goes through",
          put(store, b, "deploy", notes=err.node["notes"] + ["B"]) is None
          and notes_of(store, "deploy") == ["base", "A", "B"], notes_of(store, "deploy"))
    store.shutdown()


def test_no_false_conflicts():
    print("2. what does not conflict")
    store, sm = fresh()
    a, b = sm.register(None)["session_id"], sm.register(None)["session_id"]
    put(store, a, "own", notes=["1"])
    tick()
    check("a session's own consecutive writes", put(store, a, "own", notes=["1", "2"]) is None)
    put(store, b, "before", notes=["b"])
    tick()
    read(store, sm, a, "before")
    tick()
    check("a change another session made before this session's view",
          put(store, a, "before", notes=["b", "a"]) is None)
    put(store, b, "gisted", notes=["kept"])
    tick()
    glance(sm, a, "gisted")
    tick()
    check("a gist-only put leaves notes alone and goes through",
          put(store, a, "gisted", gist="sharper gist") is None
          and notes_of(store, "gisted") == ["kept"])
    put(store, b, "empty")
    tick()
    glance(sm, a, "empty")
    check("notes on a node that had none", put(store, a, "empty", notes=["first"]) is None)
    put(store, b, "archived", notes=["x"])
    tick()
    read(store, sm, a, "archived")
    tick()
    store.graphs["user"]["nodes"]["archived"]["_archived"] = True
    store.read_node("archived", session_id=b)     # promotion bumps the version, not the content stamp
    tick()
    check("another session's read promoting the node",
          put(store, a, "archived", notes=["x", "y"]) is None)
    store.shutdown()


def test_partial_views():
    print("3. partial views")
    store, sm = fresh()
    a, b = sm.register(None)["session_id"], sm.register(None)["session_id"]
    put(store, b, "policy", notes=["n1", "n2"], touches=["src/a.py"])
    tick()
    glance(sm, a, "policy")
    err = put(store, a, "policy", notes=["only mine"])
    check("a gist-only view cannot replace notes it never read",
          isinstance(err, NodeConflictError) and "notes" in err.reason, err)
    err = put(store, a, "policy", touches=["src/b.py"])
    check("nor touches", isinstance(err, NodeConflictError) and "touches" in err.reason, err)
    tick()
    read(store, sm, a, "policy")
    check("after a full read it can", put(store, a, "policy", notes=["n1", "n2", "n3"]) is None)
    put(store, b, "legacy", notes=["old"])
    del store.graphs["user"]["nodes"]["legacy"][WRITTEN_FIELD]    # written before the stamp existed
    glance(sm, a, "legacy")
    check("a node from before the stamp: unread notes are still protected",
          isinstance(put(store, a, "legacy", notes=["new"]), NodeConflictError))
    check("and a gist write still goes through", put(store, a, "legacy", gist="new gist") is None)
    fresh_sid = sm.register(None)["session_id"]
    check("a session that never saw the node cannot replace its notes blind",
          isinstance(put(store, fresh_sid, "policy", notes=["x"]), NodeConflictError))
    store.shutdown()


def test_render_window():
    print("4. a write during a render")
    store, sm = fresh()
    a, b = sm.register(None)["session_id"], sm.register(None)["session_id"]
    put(store, b, "window", notes=["v1"])
    tick()
    at = time.time()                                   # A starts rendering
    snapshot = store.read_node("window", session_id=a)["node"]
    tick()
    put(store, b, "window", notes=["v1", "v2"])        # lands before A marks the view
    tick()
    sm.mark_seen(a, ["window"], via="read", at=at, full=True)
    err = put(store, a, "window", notes=snapshot["notes"] + ["A"])
    check("the write is newer than the view, so A's stale write is refused",
          isinstance(err, NodeConflictError), notes_of(store, "window"))
    store.shutdown()


def test_rename_and_editor():
    print("5. rename and editor")
    store, sm = fresh()
    a, b = sm.register(None)["session_id"], sm.register(None)["session_id"]
    put(store, b, "old-name", notes=["x"])
    tick()
    read(store, sm, a, "old-name")
    store.rename_node("old-name", "new-name", session_id=b)
    check("rename carries the view times",
          sm.viewed_at(a, "new-name", full=True) is not None and sm.viewed_at(a, "old-name") is None)
    check("so the reader can still write under the new id",
          put(store, a, "new-name", notes=["x", "y"]) is None)
    tick()
    store.put_node("user", "new-name", "edited by hand", notes=["x", "y", "z"], session_id=None)
    check("an editor write (no session) is never refused", notes_of(store, "new-name")[-1] == "z")
    check("but counts as another writer for an agent that saw an older version",
          isinstance(put(store, a, "new-name", notes=["x", "y", "a"]), NodeConflictError))
    editor_sid = sm.register(None)["session_id"]
    store.put_node("user", "new-name", "edited by hand", notes=["z"], session_id=editor_sid,
                   guard=False)
    check("the editor's own session id (guard off) can replace notes it never marked read",
          notes_of(store, "new-name") == ["z"])
    store.shutdown()


def test_handler():
    print("6. the MCP handler")
    import mcp.types as types
    import mcp_streamable_server as srv
    store, sm = fresh()
    srv.store, srv.session_manager = store, sm
    handler = srv.create_mcp_server().get_request_handler("tools/call").handler
    ctx = SimpleNamespace(transport=SimpleNamespace(headers={}))

    def call(name, **arguments):
        params = types.CallToolRequestParams(name=name, arguments=arguments)
        return asyncio.run(handler(ctx, params)).content[0].text

    a, b = sm.register(None)["session_id"], sm.register(None)["session_id"]
    call("kg_put_node", session_id=b, level="user", id="shared", gist="g", notes=["base"])
    tick()
    call("kg_read", session_id=a, ids=["shared"])
    tick()
    call("kg_put_node", session_id=b, level="user", id="shared", gist="g", notes=["base", "B"])
    tick()
    text = call("kg_put_node", session_id=a, level="user", id="shared", gist="g",
                notes=["base", "A"])
    check("the refusal says NOT WRITTEN and shows the current notes",
          text.startswith("NOT WRITTEN") and "- B" in text, text[:200])
    tick()
    text = call("kg_put_node", session_id=a, level="user", id="shared", gist="g",
                notes=["base", "B", "A"])
    check("the merged retry is saved", "saved" in text and notes_of(store, "shared") == ["base", "B", "A"],
          text[:120])
    store.shutdown()


def main():
    test_lost_update()
    test_no_false_conflicts()
    test_partial_views()
    test_render_window()
    test_rename_and_editor()
    test_handler()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
