"""Reproduce the Lean fork counterexamples through the real bootstrap endpoint.

Real: GET /api/session_bootstrap (rest.py:93-186) incl. transcript recovery and
bind_claude_sid; ambient.build_prompt_recall's session resolution. The parent's
transcript is written with the bootstrap context the server itself returned —
exactly the marker the fork's copy of the transcript carries.
"""
import json, os, sys, tempfile
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()

import atexit, shutil
def _home_tmpdir():
    """Project roots must live under $HOME (safe_project_path); removed at exit."""
    d = tempfile.mkdtemp(dir=Path.home(), prefix="kg-formal-")
    atexit.register(shutil.rmtree, d, True)
    return d
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "server"))
import logging; logging.disable(logging.WARNING)
from fastapi.testclient import TestClient
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.websocket import ConnectionManager
from mcp_http.rest import create_rest_api
from mcp_http.ambient import build_prompt_recall, FULL_READ_NUDGE

sm = HTTPSessionManager()
store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None)
api = TestClient(create_rest_api(store, sm, ConnectionManager(), "t"), headers={"host": "127.0.0.1:8765"})
P = _home_tmpdir()
store.put_node("user", "some-node", "a gist")

def boot(claude_sid, source, transcript=None):
    q = {"project_path": P, "claude_session_id": claude_sid, "source": source}
    if transcript: q["transcript_path"] = transcript
    r = api.get("/api/session_bootstrap", params=q).json()
    return r

# Parent Claude session C0 starts; its transcript records the preload context.
b0 = boot("C0", "startup")
k0 = b0["session_id"]
tr = Path(_home_tmpdir()) / "parent.jsonl"
tr.write_text(json.dumps({"sessionId": "C0", "content": b0["context"]}) + "\n")
sm.mark_full_read(k0)                        # C0 did its loud full read

# Another Claude session C9 starts in the same project later (newest by path).
k9 = boot("C9", "startup")["session_id"]

# C0 is FORKED: new Claude sid C1, copied transcript; the parent stays alive.
fork_copy = tr.with_name("fork.jsonl"); fork_copy.write_text(tr.read_text())
b1 = boot("C1", "fork", str(fork_copy))
print(f"parent C0 -> k0={k0}; other session C9 -> k9={k9}")
print(f"fork C1 bootstrap: session_id={b1['session_id']} reused={b1['reused']}")

bugs = []
if b1["session_id"] == k0:
    bugs.append("D: fork and live parent now share one KG session (one seen-set for two diverging contexts)")
hit = sm.find_by_claude_sid("C0")
print("find_by_claude_sid('C0') after fork:", hit and hit[0])
reply = build_prompt_recall(store, sm, P, "tell me about some-node", claude_sid="C0")
resolved_other = reply == FULL_READ_NUDGE
print("parent C0's next prompt hook got the full-read nudge (meant for a session that never read):",
      resolved_other)
if hit is None and resolved_other:
    bugs.append("I: parent's hooks resolve to C9's session k9 (newest-by-path) — re-nagged, and writes k9's seen-set")
for b in bugs: print("BUG", b)
print("RESULT:", "FAIL" if bugs else "all hold")
store.shutdown()
