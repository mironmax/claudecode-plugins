#!/usr/bin/env python3
"""Editor search and score API integration, entirely in process.

Run with both server and editor dependencies. Uses ASGI transports and temporary storage, never
the live graphs or network. The editor is exercised against the real REST
app, including its list-shaped graph snapshots and project addressing.
"""

import copy
import json
import threading
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))

from fastapi import HTTPException
import httpx

from core.constants import project_namespace
from core.debt import GIST_OVERSIZE_CHARS
from core.utils import GIST_SCAN_LIMIT, gist_length_warning
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager

spec = importlib.util.spec_from_file_location(
    "visual_backend", SERVER.parent / "visual-editor/backend/server.py",
)
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)

NOW = 2_000_000_000.0
DAY = 86400


def fixtures():
    nodes = {}
    for nid, gist in (
        ("signal-alpha", "Signal scheduling database sync"),
        ("signal-beta", "Signal scheduling decisions"),
        ("signal-gamma", "Signal database reasoning"),
        ("notes-hit", "A match in its notes"),
        ("files-hit", "A match in a file reference"),
        ("signal-sixth", "Signal follow-up"),
        ("bridge-node", "Connects two concepts"),
        ("quiet-node", "An unrelated subject"),
        ("new-node", "A newly created item"),
    ):
        nodes[nid] = {"id": nid, "gist": gist, "_created_ts": NOW - 30 * DAY}
    nodes["signal-beta"]["_archived"] = True
    nodes["signal-gamma"].update(_archived=True, _orphaned_ts=NOW - DAY)
    nodes["notes-hit"]["notes"] = ["Keep the signal scheduling rationale here"]
    nodes["files-hit"]["touches"] = ["src/signal/scheduler.py:10-20"]
    nodes["signal-alpha"]["_useful_ts"] = [NOW, NOW - 90 * DAY]
    nodes["signal-alpha"]["_last_read_ts"] = NOW - DAY
    nodes["new-node"]["_created_ts"] = NOW - DAY
    edges = {}
    for index, (src, dst) in enumerate((
        ("signal-alpha", "bridge-node"), ("bridge-node", "signal-beta"),
        ("signal-alpha", "signal-beta"), ("signal-beta", "signal-alpha"),
        ("signal-gamma", "signal-alpha"), ("missing-node", "signal-alpha"),
    )):
        edges[(src, dst, f"rel-{index}")] = {"from": src, "to": dst, "rel": f"rel-{index}"}
    versions = {f"node:{nid}": {"v": 1, "ts": NOW - (index + 2) * DAY}
                for index, nid in enumerate(nodes)}
    return {"nodes": nodes, "edges": edges}, versions


class EditorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Server paths must be under home, matching the production resolver.
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="kg-visual-test-", dir=cache)
        cls.root = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {"KG_STORAGE_ROOT": str(cls.root)})
        cls.env.start()
        cls.sessions = HTTPSessionManager()
        cls.store = MultiProjectGraphStore(
            GraphConfig(storage_root=cls.root, user_path=cls.root / "user.json", save_interval=9999),
            cls.sessions,
        )
        cls.project = cls.root / "workspace"
        cls.project.mkdir()
        cls.store.read_graphs(project_path=str(cls.project))
        cls.user_sid = cls.sessions.register(None)["session_id"]
        upstream = create_rest_api(cls.store, cls.sessions, ConnectionManager(), "test")
        real_async_client = httpx.AsyncClient

        def local_client(*args, **kwargs):
            return real_async_client(*args, **kwargs, transport=httpx.ASGITransport(app=upstream))

        cls.client_patch = patch.object(editor.httpx, "AsyncClient", side_effect=local_client)
        cls.client_patch.start()
        cls.client = real_async_client(transport=httpx.ASGITransport(app=editor.app), base_url="http://localhost")

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.client.aclose())
        cls.client_patch.stop()
        cls.store.shutdown()
        cls.env.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.graph, self.versions = fixtures()
        self.store.graphs["user"] = self.graph
        self.store._versions["user"] = self.versions
        self.store.dirty["user"] = False
        project_key = project_namespace(str(self.project))
        self.store.graphs[project_key] = {
            "nodes": {"quasar-node": {"id": "quasar-node", "gist": "Quasar indexing",
                                      "_created_ts": NOW - 30 * DAY},
                      "project-other": {"id": "project-other", "gist": "A different concept",
                                        "_created_ts": NOW - 30 * DAY}},
            "edges": {},
        }
        self.store._versions[project_key] = {}
        self.store.dirty[project_key] = False
        self.clock = patch("core.scorer.time.time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        # These few short nodes would all fit the real fresh tier and go unscored.
        # Tier selection is tested in the server suite; here only new-node is fresh.
        self.fresh = patch.object(self.store.scorer, "fresh_ids",
                                  side_effect=lambda nodes, edges: {"new-node"} & set(nodes))
        self.fresh.start()
        self.addCleanup(self.fresh.stop)

    async def search(self, query, **params):
        response = await self.client.get("/api/search", params={"query": query, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def score(self, nid, **params):
        response = await self.client.get(f"/api/nodes/user/{nid}/score", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_health_exposes_running_server_gist_target(self):
        self.assertEqual(GIST_OVERSIZE_CHARS, GIST_SCAN_LIMIT)
        response = await self.client.get("/api/health")
        data = response.json()
        self.assertEqual(data["limits"], {"gist_target_chars": GIST_SCAN_LIMIT})
        self.assertEqual(data["limits"], data["mcp_server"]["limits"])
        # A different running version takes precedence over this checkout.
        with patch("mcp_http.rest.GIST_SCAN_LIMIT", 240):
            data = (await self.client.get("/api/health")).json()
            self.assertEqual(data["limits"]["gist_target_chars"], 240)

    async def test_health_uses_canonical_target_with_an_older_server(self):
        with patch.object(editor.httpx, "AsyncClient") as client:
            client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=httpx.Response(200, json={"status": "ok"}),
            )
            data = (await self.client.get("/api/health")).json()
        self.assertEqual(data["limits"]["gist_target_chars"], GIST_SCAN_LIMIT)
        self.assertEqual(data["mcp_server"]["status"], "ok")

    async def test_over_target_gists_can_be_created_and_updated(self):
        for length in (GIST_SCAN_LIMIT, GIST_SCAN_LIMIT + 1, GIST_SCAN_LIMIT + 200):
            gist = "🧠" * length
            response = await self.client.post("/api/nodes", json={
                "level": "user", "id": "long-gist", "gist": gist,
                "session_id": self.user_sid,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.graph["nodes"]["long-gist"]["gist"], gist)
            self.assertEqual(bool(gist_length_warning(gist)), length > GIST_SCAN_LIMIT)

    async def test_search_matches_kg_search_ranking_and_paths(self):
        before = copy.deepcopy(self.graph)
        for query in ("SIGNAL", "database_sync scheduling", "signal-alpha", "scheduler.py"):
            actual = await self.search(query)
            expected = self.store.search(query, session_id=self.user_sid, more_k=9999)
            self.assertEqual([n["id"] for n in actual["top"]], [n["id"] for n in expected["top"]])
            self.assertEqual([n["id"] for n in actual["more"]], [n["id"] for n in expected["more"]])
            self.assertEqual(actual["path_edges"], expected["path_edges"])
            self.assertEqual(actual["total"], expected["total"])
        self.assertEqual(self.graph, before)
        self.assertFalse(self.store.dirty["user"])

    async def test_search_reaches_all_tiers_and_fields(self):
        result = await self.search("signal")
        hits = {n["id"]: n for n in result["top"] + result["more"]}
        self.assertTrue(hits["signal-beta"]["archived"])
        self.assertTrue(hits["signal-gamma"]["orphaned"])
        self.assertEqual(hits["notes-hit"]["matched_fields"], ["Notes"])
        self.assertEqual(hits["files-hit"]["matched_fields"], ["Files"])
        self.assertIn("src/signal", hits["files-hit"]["excerpt"])
        self.assertEqual(len(result["top"]), 5)
        self.assertEqual(result["total"], 6)

    async def test_search_keeps_connectors_and_all_remaining_matches(self):
        self.graph["edges"] = dict(list(self.graph["edges"].items())[:2])
        for index in range(16):
            nid = f"extra-signal-{index}"
            self.graph["nodes"][nid] = {"id": nid, "gist": "Signal follow-up"}
        result = await self.search("signal scheduling")
        self.assertIn("bridge-node", [node["id"] for node in result["connectors"]])
        self.assertEqual(len(result["path_edges"]), 2)
        self.assertEqual(len(result["top"]) + len(result["more"]), result["total"])
        self.assertGreater(len(result["more"]), 10)

    async def test_project_search_is_scoped_to_selected_graph(self):
        result = await self.search("quasar", level="project", project_path=str(self.project))
        self.assertEqual([n["id"] for n in result["top"]], ["quasar-node"])
        self.assertEqual((await self.search("signal", level="project", project_path=str(self.project)))["total"], 0)
        self.assertEqual((await self.search("quasar"))["total"], 0)

    async def test_search_validation_and_upstream_errors(self):
        self.assertEqual((await self.search("   "))["total"], 0)
        self.assertEqual((await self.client.get("/api/search")).status_code, 422)
        self.assertEqual((await self.client.get("/api/search", params={"query": "x", "level": "maintain"})).status_code, 422)
        self.assertEqual((await self.client.get("/api/search", params={"query": "x", "level": "project"})).status_code, 400)
        with patch.object(editor, "get_graph", AsyncMock(side_effect=HTTPException(503, "Offline"))):
            self.assertEqual((await self.client.get("/api/search", params={"query": "x"})).status_code, 503)

    async def test_active_score_and_factors_match_canonical_compaction(self):
        before = copy.deepcopy(self.graph), copy.deepcopy(self.versions), copy.deepcopy(self.sessions.get_seen(self.user_sid))
        data = await self.score("signal-alpha")
        expected = self.store.scorer.score_all(self.graph["nodes"], self.graph["edges"], self.versions)
        self.assertEqual(data["score"], expected["signal-alpha"])
        self.assertAlmostEqual(sum(c["contribution"] for c in data["components"]), data["score"])
        self.assertEqual([c["weight"] for c in data["components"]], [0.25, 0.4, 0.35])
        raw = {c["key"]: c["raw"] for c in data["components"]}
        # The latest endorsement (NOW) is newer than the last write (NOW - DAY).
        self.assertEqual(raw["recency"], NOW)
        self.assertEqual(data["recency"]["credit_ts"], NOW)
        self.assertEqual(raw["usefulness"], 1.5)
        self.assertEqual(data["usefulness"]["endorsements"], 2)
        self.assertEqual(data["connectedness"]["weighted_in"], 0.2)
        self.assertEqual(data["connectedness"]["incoming"]["unweighted"], 2)
        self.assertEqual(raw["connectedness"], max(data["connectedness"]["weighted_degree"], data["connectedness"]["hub_floor"]))
        self.assertEqual((self.graph, self.versions, self.sessions.get_seen(self.user_sid)), before)
        self.assertFalse(self.store.dirty["user"])

    async def test_archived_score_matches_unified_refill_pool(self):
        data = await self.score("signal-beta")
        expected = self.store.scorer.score_all(self.graph["nodes"], self.graph["edges"], self.versions, include_archived=True)
        self.assertTrue(data["pool"]["include_archived"])
        self.assertEqual(data["score"], expected["signal-beta"])

    async def test_orphaned_and_grace_nodes_get_read_only_previews(self):
        before = copy.deepcopy(self.graph)
        for nid in ("signal-gamma", "new-node"):
            data = await self.score(nid)
            self.assertFalse(data["eligible"])
            self.assertIsNone(data["score"])
            self.assertIsInstance(data["preview_score"], float)
            self.assertAlmostEqual(sum(c["contribution"] for c in data["components"]), data["preview_score"])
        self.assertTrue((await self.score("new-node"))["fresh"]["protected"])
        self.assertEqual(self.graph, before)
        self.assertFalse(self.store.dirty["user"])

    async def test_project_score_path_overrides_editor_user_session(self):
        response = await self.client.get("/api/nodes/project/quasar-node/score", params={
            "project_path": str(self.project), "session_id": self.user_sid,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["score"], 0.5)  # every factor tied

    async def test_score_missing_node_and_invalid_level(self):
        response = await self.client.get("/api/nodes/user/absent-node/score")
        self.assertEqual(response.status_code, 404)
        self.assertIn("absent-node", response.json()["detail"])
        self.assertEqual((await self.client.get("/api/nodes/invalid/x/score")).status_code, 422)

    async def test_cross_site_requests_are_refused(self):
        # A cross-site <img> GET would otherwise promote the node through the proxy.
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://example.com"}):
            response = await self.client.get("/api/nodes/user/signal-beta", headers=headers)
            self.assertEqual(response.status_code, 403, headers)
        self.assertTrue(self.graph["nodes"]["signal-beta"].get("_archived"))
        same_origin = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:8766"}
        self.assertEqual((await self.client.get("/api/health", headers=same_origin)).status_code, 200)
        # A link to the editor on another site still opens the page itself.
        link = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
        self.assertEqual((await self.client.get("/", headers=link)).status_code, 200)


class _FakeUpstream:
    """websockets.connect stand-in for the memory server's /ws: answers a
    subscribe as the server does; {"type": "restart"} ends the connection."""
    def __init__(self, url):
        self.url, self.sent, self.queue = url, [], None

    async def __aenter__(self):
        self.queue = asyncio.Queue()
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        self.sent.append(data)
        msg = json.loads(data)
        if msg.get("type") == "subscribe":
            await self.queue.put(json.dumps({"type": "subscribed", "sub": msg["sub"],
                                             "project_path": msg["project_path"]}))
        elif msg.get("type") == "restart":
            await self.queue.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class EditorProxyTests(unittest.TestCase):
    def test_ws_proxy_forwards_subscriptions_and_closes_with_the_server(self):
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        upstreams = []

        def connect(url):
            upstreams.append(_FakeUpstream(url))
            return upstreams[-1]

        with patch.dict(sys.modules, {"websockets": SimpleNamespace(connect=connect)}):
            client = TestClient(editor.app)
            with client.websocket_connect("ws://localhost/ws",
                                          headers={"origin": "http://localhost:8766"}) as ws:
                ws.send_text(json.dumps({"type": "subscribe", "sub": 1, "project_path": "/p"}))
                self.assertEqual(ws.receive_json(),
                                 {"type": "subscribed", "sub": 1, "project_path": "/p"})
                # The memory server goes away: the page must see its socket
                # close (and reconnect), not sit on a dead proxy.
                ws.send_text(json.dumps({"type": "restart"}))
                outcome = []

                def receive():
                    try:
                        outcome.append(ws.receive_json())
                    except WebSocketDisconnect:
                        outcome.append("closed")
                waiter = threading.Thread(target=receive, daemon=True)
                waiter.start()
                waiter.join(5)
                self.assertEqual(outcome, ["closed"])
        self.assertEqual(upstreams[0].url, editor.MCP_WS_URL)


class EditorConfigTests(unittest.TestCase):
    def load(self, **env):
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location(
                "visual_backend_config", SERVER.parent / "visual-editor/backend/server.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def cors_origins(self, module):
        return next(m.kwargs["allow_origins"] for m in module.app.user_middleware
                    if "allow_origins" in m.kwargs)

    def test_defaults(self):
        self.assertEqual(editor.MCP_WS_URL, "ws://127.0.0.1:8765/ws")
        self.assertIn("http://localhost:8766", self.cors_origins(editor))

    def test_ports_follow_the_environment(self):
        module = self.load(MCP_SERVER_URL="http://127.0.0.1:8767/", EDITOR_PORT="8770")
        self.assertEqual(module.MCP_WS_URL, "ws://127.0.0.1:8767/ws")
        self.assertEqual(self.cors_origins(module), ["http://localhost:8770", "http://127.0.0.1:8770"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
