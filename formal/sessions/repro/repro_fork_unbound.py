"""Reproduce the Lean counterexample for a fork of a session no hook has bound yet.

Real: kg_read's own registration without a harness id (mcp_streamable_server.py:440),
GET /api/session_bootstrap transcript recovery (rest.py:128-147), kg_read's
seen-marking (mcp_streamable_server.py:467) and resolve_hook_session
(session_manager.py:227-251). The parent's transcript carries the
"Session: <id>" footer kg_read leaves, the marker the fork's copy inherits.
"""
import json, os, sys, tempfile
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()

import atexit, shutil
atexit.register(shutil.rmtree, os.environ["KG_STORAGE_ROOT"], True)
def _home_tmpdir():
    """Project roots must live under $HOME (safe_project_path); removed at exit."""
    d = tempfile.mkdtemp(dir=Path.home(), prefix="kg-formal-")
    atexit.register(shutil.rmtree, d, True)
    return d
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
import logging; logging.disable(logging.WARNING)
from fastapi.testclient import TestClient
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.websocket import ConnectionManager
from mcp_http.rest import create_rest_api

sm = HTTPSessionManager()
store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None)
api = TestClient(create_rest_api(store, sm, ConnectionManager(), "t"), headers={"host": "127.0.0.1:8765"})
P = _home_tmpdir()

# C0's preload never ran: its first kg_read registers an unbound session.
k0 = sm.register(P)["session_id"]
tr = Path(_home_tmpdir()) / "parent.jsonl"
tr.write_text(json.dumps({"sessionId": "C0", "content": f"graph...\n\nSession: {k0}"}) + "\n")

# C0 is FORKED before any hook of its own bound k0; the parent stays alive.
fork_copy = tr.with_name("fork.jsonl"); fork_copy.write_text(tr.read_text())
b1 = api.get("/api/session_bootstrap", params={"project_path": P, "claude_session_id": "C1",
             "source": "fork", "transcript_path": str(fork_copy)}).json()
k1 = b1["session_id"]
print(f"parent C0 uses k0={k0}; fork C1 bootstrap: session_id={k1} reused={b1['reused']}")

# The parent keeps working on k0: a kg_read(id=...) marks the node seen there.
sm.mark_seen(k0, ["parent-only-node"], via="read")

bugs = []
if k1 == k0:
    bugs.append("fork and live parent share one KG session")
child = sm.resolve_hook_session("C1", P, str(fork_copy))
if child and "parent-only-node" in sm.get_seen(child[0]):
    bugs.append("D: the fork's hooks treat a node only the parent read as seen, so it is never injected")
parent = sm.resolve_hook_session("C0", P, str(tr))
print("parent C0's hook resolves to:", parent and parent[0])
if parent is None:
    bugs.append("the parent's hooks resolve to nothing: k0 is now bound to the fork")
for b in bugs: print("BUG", b)
print("RESULT:", "FAIL" if bugs else "all hold")
store.shutdown()
