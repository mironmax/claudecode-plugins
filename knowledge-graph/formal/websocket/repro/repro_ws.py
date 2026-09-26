"""Does a connected editor receive live project-level changes?

Real: create_rest_api, /ws endpoint, ConnectionManager, store._broadcast.
The editor proxy connects to /ws WITHOUT session_id (visual-editor/backend/server.py:344,
frontend app.js:121), exactly as here. A Claude session registered on project P
writes a project node; then a user node is written as a marker.
If the first message the editor gets is the marker, the project change was dropped.
"""
import os, sys, tempfile
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

sm = HTTPSessionManager(); cm = ConnectionManager()
async def cb(project_path, message, exclude):
    await cm.broadcast_to_project(project_path, message, exclude, sm)
store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, cb)
client = TestClient(create_rest_api(store, sm, cm, "test"),
                    headers={"host": "127.0.0.1:8765"})
P = _home_tmpdir()
claude_sid = sm.register(P)["session_id"]

with client.websocket_connect("/ws") as ws:
    hello = ws.receive_json()
    print("editor ws session:", hello["session_id"],
          "project:", sm.get_project_path(hello["session_id"]))
    r1 = client.post("/api/nodes", json={"level": "project", "id": "proj-node",
                                         "gist": "g", "session_id": claude_sid})
    r2 = client.post("/api/nodes", json={"level": "user", "id": "user-marker", "gist": "g"})
    print("writes:", r1.status_code, r2.status_code)
    first = ws.receive_json()
    got = first.get("node", {}).get("id")
    print("first broadcast the editor received:", first.get("type"), got)
    print("FAIL: project-level change never reached the editor" if got == "user-marker"
          else "project change delivered")
store.shutdown()
