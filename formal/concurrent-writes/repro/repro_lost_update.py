"""Reproduce F11 against the real store: a write built on a stale or partial view.

Real: MultiProjectGraphStore.put_node / read_node, HTTPSessionManager.mark_seen,
driven the way the MCP handlers drive them (read, then mark the ids seen).
  A  two sessions read one node, then each adds a note: the second write
     drops the first session's note (the Lean trace, W1).
  B  one session has seen only the gist (preload, recall) and sends notes:
     the node's existing notes, which it never read, are gone.
Prints PASS once put_node refuses such writes and returns the current content.
"""
import os, sys, tempfile, time
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
import logging; logging.disable(logging.WARNING)
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager

sm = HTTPSessionManager()
store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None)
a = sm.register(None)["session_id"]
b = sm.register(None)["session_id"]


def read(sid, nid):
    """What the kg_read(ids=[...]) handler does: read, then mark seen."""
    at = time.time()
    node = store.read_node(nid, session_id=sid)["node"]
    sm.mark_seen(sid, [nid], via="read", **({"at": at, "full": True} if FIXED else {}))
    return node


def put(sid, nid, gist, notes):
    try:
        store.put_node("user", nid, gist, notes=notes, session_id=sid)
        return "written"
    except Exception as e:
        return f"refused ({type(e).__name__})"


import inspect
FIXED = "at" in inspect.signature(sm.mark_seen).parameters

store.put_node("user", "deploy-notes", "how deploys work", notes=["base"], session_id=b)
time.sleep(0.01)
na, nb = read(a, "deploy-notes"), read(b, "deploy-notes")
time.sleep(0.01)
ra = put(a, "deploy-notes", na["gist"], na["notes"] + ["from A"])
time.sleep(0.01)
rb = put(b, "deploy-notes", nb["gist"], nb["notes"] + ["from B"])
notes = store.graphs["user"]["nodes"]["deploy-notes"]["notes"]
ok_a = "from A" in notes
print(f"A  A: {ra}   B: {rb}   final notes: {notes}")
print("   " + ("ok: A's note survives" if ok_a else "BUG: A's acknowledged note is gone"))

store.put_node("user", "cache-policy", "cache headers per route", notes=["n1", "n2"], session_id=b)
time.sleep(0.01)
at = time.time()
sm.mark_seen(a, ["cache-policy"], via="preload", **({"at": at} if FIXED else {}))
rc = put(a, "cache-policy", "cache headers per route", ["one new note"])
notes2 = store.graphs["user"]["nodes"]["cache-policy"]["notes"]
ok_b = "n1" in notes2
print(f"B  gist-only view, then notes sent: {rc}   final notes: {notes2}")
print("   " + ("ok: unread notes kept" if ok_b else "BUG: notes the session never read are gone"))
store.shutdown()
print("RESULT:", "PASS" if ok_a and ok_b else "FAIL")
sys.exit(0 if ok_a and ok_b else 1)
