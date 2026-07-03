#!/usr/bin/env python3
"""Endpoint smoke tests for the REST API wiring (mcp_http/rest.py).

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_http.py

Every shipped bug in this plugin's history lived in the HTTP wiring layer
(recall 500, delete 500, swapped delete-edge args, missing project_path on
writes), not in core logic — so each REST endpoint gets exercised at least
once here, in-process via FastAPI's TestClient, including the visual editor's
exact addressing shape (session without a registered project path, plus an
explicit project_path).

Uses a temp KG_STORAGE_ROOT and a temp project dir under $HOME — never touches
real graphs. Exits non-zero if any assertion fails.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Isolated storage MUST be configured before core/store modules read the env.
_STORAGE_TMP = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE_TMP

# Make server modules importable when run from the tests/ dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.websocket import ConnectionManager
from mcp_http.rest import create_rest_api
from mcp_http.security import host_allowed, origin_allowed

# --- tiny test runner ---------------------------------------------------------
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
    print("=== HTTP endpoint smoke tests ===")

    # A "project" must live under $HOME (safe_project_path containment).
    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))

    config = GraphConfig(save_interval=9999)  # saver thread stays asleep during the test
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)
    rest_api = create_rest_api(store, session_manager, ConnectionManager(), version="test")
    client = TestClient(rest_api)

    try:
        # --- security helpers ------------------------------------------------
        print("security:")
        check("host_allowed local", host_allowed("127.0.0.1:8765") and host_allowed("localhost"))
        check("host_allowed rejects foreign", not host_allowed("evil.com:8765") and not host_allowed(""))
        check("origin_allowed absent + local", origin_allowed(None) and origin_allowed("http://localhost:8766"))
        check("origin_allowed rejects foreign", not origin_allowed("https://evil.com") and not origin_allowed("file://x"))

        # --- health + session -------------------------------------------------
        print("endpoints:")
        r = client.get("/api/health")
        check("health 200", r.status_code == 200 and r.json()["status"] == "ok", r.text)

        # Editor-shaped session: registered with NO project path (like the WS session)
        r = client.post("/api/sessions/register")
        sid = r.json().get("session_id")
        check("session register", r.status_code == 200 and sid, r.text)

        # --- user-level node CRUD ---------------------------------------------
        r = client.post("/api/nodes", json={
            "level": "user", "id": "u-node", "gist": "a user node",
            "notes": ["n1"], "session_id": sid,
        })
        check("create user node", r.status_code == 200, r.text)

        r = client.get(f"/api/nodes/user/u-node?session_id={sid}")
        check("read user node", r.status_code == 200 and r.json()["node"]["gist"] == "a user node", r.text)

        # --- project-level writes via project_path (the v0.9.12 missed family) -
        # The session has NO project path registered; only project_path addresses
        # the graph — exactly how the visual editor calls these endpoints.
        r = client.post("/api/nodes", json={
            "level": "project", "id": "p-node", "gist": "a project node",
            "session_id": sid, "project_path": project_dir,
        })
        check("create project node via project_path", r.status_code == 200, r.text)

        r = client.post("/api/nodes", json={
            "level": "project", "id": "p-node-2", "gist": "second project node",
            "session_id": sid, "project_path": project_dir,
        })
        check("create second project node", r.status_code == 200, r.text)

        # Inline-edit shape: same id again, updated gist
        r = client.post("/api/nodes", json={
            "level": "project", "id": "p-node", "gist": "edited gist",
            "session_id": sid, "project_path": project_dir,
        })
        check("update project node (inline edit shape)", r.status_code == 200, r.text)

        r = client.post("/api/edges", json={
            "level": "project", "from": "p-node", "to": "p-node-2", "rel": "links-to",
            "session_id": sid, "project_path": project_dir,
        })
        check("create project edge via project_path", r.status_code == 200, r.text)

        # --- graph read --------------------------------------------------------
        r = client.get(f"/api/graph/read?project_path={project_dir}")
        body = r.json()
        ids = {n["id"] for n in body["project"]["nodes"]}
        check("graph read shows project nodes", r.status_code == 200 and {"p-node", "p-node-2"} <= ids, r.text[:200])
        check("graph read shows project edge", len(body["project"]["edges"]) == 1, body["project"]["edges"])

        # --- node read/recall via project_path ---------------------------------
        r = client.get(f"/api/nodes/project/p-node?session_id={sid}&project_path={project_dir}")
        check("read project node via project_path", r.status_code == 200 and r.json()["node"]["gist"] == "edited gist", r.text)

        # --- delete edge (regression: swapped positional args -> always 500) ---
        r = client.delete(f"/api/edges/project/p-node/p-node-2/links-to"
                          f"?session_id={sid}&project_path={project_dir}")
        check("delete project edge", r.status_code == 200 and r.json().get("deleted") is True, r.text)

        # deleting again reports not-deleted, not an error
        r = client.delete(f"/api/edges/project/p-node/p-node-2/links-to"
                          f"?session_id={sid}&project_path={project_dir}")
        check("delete missing edge is graceful", r.status_code == 200 and r.json().get("deleted") is False, r.text)

        # --- delete node via project_path --------------------------------------
        r = client.delete(f"/api/nodes/project/p-node-2?session_id={sid}&project_path={project_dir}")
        check("delete project node via project_path", r.status_code == 200, r.text)

        r = client.delete(f"/api/nodes/project/p-node-2?session_id={sid}&project_path={project_dir}")
        check("delete missing node is 404", r.status_code == 404, r.text)

        # --- validation ---------------------------------------------------------
        r = client.post("/api/nodes", json={
            "level": "user", "id": "<script>alert(1)</script>", "gist": "x", "session_id": sid,
        })
        check("hostile node id rejected with 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

        r = client.post("/api/edges", json={
            "level": "user", "from": "u-node", "to": "x' onerror='1", "rel": "r", "session_id": sid,
        })
        check("hostile edge ref rejected with 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

        r = client.post("/api/nodes", json={
            "level": "project", "id": "escape", "gist": "x", "project_path": "/etc",
        })
        check("project_path outside home rejected", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

        # --- progress ------------------------------------------------------------
        r = client.post("/api/progress", json={"task_id": "t1", "state": {"step": 2}, "session_id": sid})
        check("set progress", r.status_code == 200, r.text)
        r = client.get("/api/progress/t1")
        check("get progress", r.status_code == 200 and r.json().get("step") == 2, r.text)

        # --- session stats ---------------------------------------------------------
        r = client.get(f"/api/sessions/{sid}/stats")
        check("session stats", r.status_code == 200 and "graphs" in r.json(), r.text)

        # --- WebSocket origin policy -------------------------------------------------
        print("websocket:")
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            check("ws connects without Origin (non-browser)", msg.get("type") == "connected", msg)

        with client.websocket_connect("/ws", headers={"origin": "http://localhost:8766"}) as ws:
            msg = ws.receive_json()
            check("ws connects from local Origin", msg.get("type") == "connected", msg)

        rejected = False
        try:
            with client.websocket_connect("/ws", headers={"origin": "https://evil.com"}) as ws:
                ws.receive_json()
        except Exception:
            rejected = True
        check("ws rejects foreign Origin", rejected)

    finally:
        shutil.rmtree(_STORAGE_TMP, ignore_errors=True)
        shutil.rmtree(project_dir, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
