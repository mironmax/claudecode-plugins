"""Does a connected editor receive live project-level changes?

Real: create_rest_api, /ws endpoint, ConnectionManager, store._broadcast.
The editor page connects to /ws without session_id (through the editor's
proxy, which forwards text both ways) and, since the F6 fix, sends
{"type": "subscribe", "project_path": <selected project>, "sub": n} on every
(re)connect and selection change (frontend app.js subscribeCurrent), exactly
as here. A Claude session registered on project P writes a project node, a
session on project Q writes another, then a user node is written as a marker.
The editor must get P's change, not Q's, before the marker.

Before the fix the server ignored the subscribe message (a ping sent after it
is answered first) and the first message the editor got was the marker:
"FAIL: project-level change never reached the editor".
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
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
import logging; logging.disable(logging.WARNING)
from fastapi.testclient import TestClient
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.websocket import ConnectionManager
from mcp_http.rest import create_rest_api

sm = HTTPSessionManager(); cm = ConnectionManager()
async def cb(project_path, message, exclude):
    await cm.broadcast_to_project(project_path, message, exclude, sm)
store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, cb)
client = TestClient(create_rest_api(store, sm, cm, "test"),
                    headers={"host": "127.0.0.1:8765"})
P = os.path.realpath(_home_tmpdir()); Q = os.path.realpath(_home_tmpdir())
sid_p = sm.register(P)["session_id"]; sid_q = sm.register(Q)["session_id"]

def writes(tag):
    r = [client.post("/api/nodes", json={"level": "project", "id": f"p-{tag}", "gist": "g", "session_id": sid_p}),
         client.post("/api/nodes", json={"level": "project", "id": f"q-{tag}", "gist": "g", "session_id": sid_q}),
         client.post("/api/nodes", json={"level": "user", "id": f"marker-{tag}", "gist": "g"})]
    print("writes (P, Q, user marker):", *[x.status_code for x in r])

def until_marker(ws, tag):
    got = []
    while True:
        m = ws.receive_json(); nid = m.get("node", {}).get("id")
        if nid == f"marker-{tag}": return got
        got.append(nid)

failed = False
with client.websocket_connect("/ws") as ws:
    hello = ws.receive_json()
    print("editor ws session:", hello["session_id"],
          "session project:", sm.get_project_path(hello["session_id"]))
    ws.send_text(json.dumps({"type": "subscribe", "project_path": P, "sub": 1}))
    ws.send_text("ping")
    reply = ws.receive_json()
    print("reply to subscribe(P):", reply.get("type"), "sub", reply.get("sub"),
          "project matches P:", reply.get("project_path") == P)
    if reply.get("type") == "subscribed":
        ws.receive_json()  # the pong
    writes("a")
    got = until_marker(ws, "a")
    print("changes the editor received before the marker:", got)
    if "p-a" not in got:
        print("FAIL: project-level change never reached the editor"); failed = True
    if "q-a" in got:
        print("FAIL: another project's change reached the editor"); failed = True

# An older page never subscribes: it must keep working as before (user-level
# changes live, project changes via Refresh), and never get a project change.
with client.websocket_connect("/ws") as ws:
    ws.receive_json()
    writes("b")
    got = until_marker(ws, "b")
    print("older page (no subscribe) received before the marker:", got)
    if got:
        print("FAIL: an unsubscribed connection got a project change"); failed = True

print("FAIL" if failed else
      "PASS: the subscribed editor got its project's change and not the other project's; "
      "an unsubscribed (older) page gets none, as before")
store.shutdown()
