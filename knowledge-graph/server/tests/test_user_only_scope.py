#!/usr/bin/env python3
"""User-only sessions: one memory scope obeyed by kg_read, bootstrap, the
ambient hooks and maintenance.

    cd knowledge-graph/server && ./venv/bin/python tests/test_user_only_scope.py

Uses a temp KG_STORAGE_ROOT and temp folders under ~/.cache. $HOME and the
Codex scratch path appear only as strings: nothing is created there.
"""

import asyncio
import json
import os
os.environ["KG_BUDGET_NOTICES"] = "0"  # hook outputs below are exact; budget.py has its own tests
from pathlib import Path
import re
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-scope-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name

from fastapi.testclient import TestClient
import mcp.types as types

from core.constants import memory_project_root, project_slug
from mcp_http import ambient, chore_dispatch, file_recall
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager
import mcp_streamable_server as srv

HOME = str(Path.home().resolve())
SCRATCH = os.path.join(HOME, "Documents/Codex/2026-09-28/read-the-bridge")
STORAGE = Path(_STORAGE.name)


def recall_log() -> list[dict]:
    path = STORAGE / "recall.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class Fixture(unittest.TestCase):
    def setUp(self):
        (STORAGE / "sessions.json").unlink(missing_ok=True)
        (STORAGE / "recall.jsonl").unlink(missing_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="kg-scope-project-", dir=Path.home() / ".cache")
        self.addCleanup(folder.cleanup)
        self.root = str(Path(folder.name).resolve())
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)
        file_recall.reset_throttle()

    def transcript(self, text: str) -> Path:
        path = Path(self.root) / "transcript.jsonl"
        path.write_text(text)
        return path


class ClassifierTests(Fixture):
    def test_home_and_codex_scratch_are_user_only(self):
        self.assertIsNone(memory_project_root(HOME))
        self.assertIsNone(memory_project_root(SCRATCH))

    def test_other_folders_stay_projects(self):
        for folder in [self.root, os.path.join(HOME, "Documents/Codex/my-work"),
                       os.path.join(SCRATCH, "nested")]:
            with self.subTest(folder=folder):
                self.assertEqual(memory_project_root(folder), folder)

    def test_an_existing_graph_keeps_a_scratch_folder_a_project(self):
        graph = STORAGE / "projects" / project_slug(SCRATCH) / "graph.json"
        graph.parent.mkdir(parents=True)
        self.addCleanup(lambda: (graph.unlink(), graph.parent.rmdir()))
        graph.write_text("{}")
        self.assertEqual(memory_project_root(SCRATCH), SCRATCH)

    def test_outside_home_is_refused(self):
        with self.assertRaises(ValueError):
            memory_project_root("/etc")


class KgReadTests(Fixture):
    def setUp(self):
        super().setUp()
        srv.store, srv.session_manager = self.store, self.sm
        handler = srv.create_mcp_server().get_request_handler("tools/call").handler
        ctx = SimpleNamespace(transport=SimpleNamespace(headers={}))

        def call(name, **arguments):
            params = types.CallToolRequestParams(name=name, arguments=arguments)
            return asyncio.run(handler(ctx, params)).content[0].text
        self.call = call

    def user_only_session(self) -> str:
        text = self.call("kg_read")
        self.assertIn("User-only session", text)
        return re.search(r"Session: ([0-9a-f]{8})", text).group(1)

    def test_no_cwd_starts_a_reusable_user_only_session(self):
        sid = self.user_only_session()
        self.assertEqual(self.sm.lookup(sid)["scope"], "user")
        again = self.call("kg_read", session_id=sid)
        self.assertNotIn("Error", again)
        self.assertNotIn("User-only session", again)
        self.assertEqual(self.sm.count(), 1)

    def test_home_cwd_is_user_only(self):
        text = self.call("kg_read", cwd=HOME)
        self.assertIn("User-only session", text)
        sid = re.search(r"Session: ([0-9a-f]{8})", text).group(1)
        self.assertIsNone(self.sm.lookup(sid)["project_path"])

    def test_project_write_is_refused_with_the_way_out(self):
        sid = self.user_only_session()
        text = self.call("kg_put_node", session_id=sid, level="project", id="p-node", gist="g")
        self.assertIn(f"kg_read(session_id='{sid}', cwd='<project root>')", text)
        self.assertIn("level='user'", text)
        self.assertFalse((STORAGE / "projects" / project_slug(HOME)).exists())
        self.assertIn("saved", self.call("kg_put_node", session_id=sid, level="user",
                                         id="u-node", gist="g"))

    def test_attaching_a_project_keeps_id_and_seen_state(self):
        sid = self.user_only_session()
        self.call("kg_put_node", session_id=sid, level="user", id="u-node", gist="g")
        self.call("kg_read", session_id=sid, ids=["u-node"])
        text = self.call("kg_read", session_id=sid, cwd=self.root)
        self.assertIn(f"Project memory attached: {self.root}", text)
        record = self.sm.lookup(sid)
        self.assertEqual(record["project_path"], self.root)
        self.assertNotIn("scope", record)
        self.assertIn("u-node", self.sm.get_seen(sid))
        self.assertIn("saved", self.call("kg_put_node", session_id=sid, level="project",
                                         id="p-node", gist="g"))

    def test_a_bound_session_ignores_another_cwd(self):
        sid = self.sm.register(self.root)["session_id"]
        other = os.path.join(HOME, "kg-test-other-project")
        text = self.call("kg_read", session_id=sid, cwd=other)
        self.assertIn(f"stays bound to {self.root}", text)
        self.assertIn(f"kg_read(cwd='{other}')", text)
        self.assertEqual(self.sm.lookup(sid)["project_path"], self.root)
        inside = self.call("kg_read", session_id=sid, cwd=os.path.join(self.root, "src"))
        self.assertNotIn("stays bound", inside)

    def test_a_lost_session_needs_its_cwd_back(self):
        self.sm.ensure_session("abcd1234")
        self.assertTrue(self.call("kg_read", session_id="abcd1234").startswith("Error"))
        text = self.call("kg_read", session_id="abcd1234", cwd=self.root)
        self.assertIn("Project memory attached", text)
        self.assertIn("Session: abcd1234", text)

    def test_search_and_sync_work_user_only(self):
        sid = self.user_only_session()
        self.call("kg_put_node", session_id=sid, level="user", id="zephyr-uplink", gist="g")
        self.assertIn("zephyr-uplink", self.call("kg_search", session_id=sid, query="zephyr"))
        self.assertNotIn("Error", self.call("kg_sync", session_id=sid))


class BootstrapTests(Fixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(create_rest_api(self.store, self.sm, ConnectionManager(), "test"))

    def boot(self, project_path, claude_sid, source="startup", transcript=None):
        params = {"project_path": project_path, "claude_session_id": claude_sid, "source": source}
        if transcript:
            params["transcript_path"] = str(transcript)
        return self.client.get("/api/session_bootstrap", params=params).json()

    def test_home_bootstrap_is_user_only(self):
        sid = self.boot(HOME, "cc-home")["session_id"]
        record = self.sm.lookup(sid)
        self.assertIsNone(record["project_path"])
        self.assertEqual(record["scope"], "user")

    def test_resume_forks_a_bound_user_only_session(self):
        sid = self.boot(HOME, "cc-home")["session_id"]
        r = self.boot(HOME, "cc-home-2", "resume", self.transcript(f"Session: {sid}\n"))
        self.assertTrue(r["reused"])
        self.assertNotEqual(r["session_id"], sid)
        self.assertEqual(self.sm.lookup(r["session_id"])["scope"], "user")

    def test_resume_never_crosses_scope(self):
        project_sid = self.sm.register(self.root)["session_id"]
        r = self.boot(HOME, "cc-home-3", "resume", self.transcript(f"Session: {project_sid}\n"))
        self.assertFalse(r["reused"])
        self.assertIsNone(self.sm.lookup(r["session_id"])["project_path"])


class HookTests(Fixture):
    def event(self, claude_sid, target, transcript=None):
        return ambient.handle_tool_event(self.store, self.sm, {
            "cwd": self.root, "tool_name": "Read", "session_id": claude_sid,
            "transcript_path": str(transcript) if transcript else None,
            "tool_input": {"file_path": target}})

    def test_user_only_session_recalls_from_the_user_graph_alone(self):
        target = os.path.join(self.root, "engine.py")
        writer = self.sm.register(self.root)["session_id"]
        self.store.put_node(level="project", node_id="project-note", gist="PROJECT_MEMORY",
                            touches=["engine.py"], session_id=writer)
        self.store.put_node(level="user", node_id="user-note", gist="USER_MEMORY",
                            touches=[target], session_id=writer)
        self.sm.register(None, claude_sid="cc-user")
        text = self.event("cc-user", target)
        self.assertIn("USER_MEMORY", text)
        self.assertNotIn("PROJECT_MEMORY", text)
        self.assertFalse(ambient._events_path(self.root).exists())

    def test_an_unresolved_id_borrows_no_other_session(self):
        for claude_sid in ("cc-1", "cc-2"):
            self.sm.register(self.root, claude_sid=claude_sid)
        self.assertIsNone(ambient.build_prompt_recall(self.store, self.sm, self.root,
                                                      "anything at all", claude_sid="cc-3"))
        target = os.path.join(self.root, "engine.py")
        writer = self.sm.register(self.root)["session_id"]
        self.store.put_node(level="project", node_id="engine", gist="g",
                            touches=["engine.py"], session_id=writer)
        self.assertIsNone(self.event("cc-3", target))
        outcomes = [(r["reason"], r.get("outcome"), r["kg_session"]) for r in recall_log()]
        self.assertEqual(outcomes, [("unresolved_session", None, None),
                                    ("file_recall", "unresolved_session", None)])

    def test_transcript_evidence_binds_an_unbound_session(self):
        sid = self.sm.register(self.root)["session_id"]      # as kg_read registers
        transcript = self.transcript("no marker yet\n")
        prompt = lambda: ambient.build_prompt_recall(
            self.store, self.sm, self.root, "anything at all",
            claude_sid="cc-late", transcript_path=str(transcript))
        self.assertIsNone(prompt())
        self.assertEqual(self.sm._transcript_scans["cc-late"][1], transcript.stat().st_size)
        with transcript.open("a") as stream:
            stream.write(f"Session: {sid}\n")
        self.assertEqual(prompt(), ambient.FULL_READ_NUDGE)
        self.assertEqual(self.sm.find_by_claude_sid("cc-late")[0], sid)

    def test_transcript_naming_a_bound_or_foreign_session_is_ignored(self):
        bound = self.sm.register(self.root, claude_sid="cc-owner")["session_id"]
        foreign = self.sm.register(None)["session_id"]
        for sid in (bound, foreign):
            with self.subTest(sid=sid):
                transcript = self.transcript(f"Session: {sid}\n")
                self.assertIsNone(self.sm.resolve_hook_session("cc-quoter", self.root,
                                                               str(transcript)))
                self.sm._transcript_scans.clear()
        self.assertEqual(self.sm.find_by_claude_sid("cc-owner")[0], bound)
        self.assertIsNone(self.sm.find_by_claude_sid("cc-quoter"))


class MaintenanceTests(Fixture):
    def dispatched_project(self, claude_sid):
        seen, done = [], threading.Event()

        def record(store, session_manager, project_path):
            seen.append(project_path)
            done.set()
        client = TestClient(create_rest_api(self.store, self.sm, ConnectionManager(), "test"))
        with patch.object(chore_dispatch, "enabled", return_value=True), \
                patch.object(chore_dispatch, "maybe_dispatch", record):
            client.post("/api/prompt_context", json={
                "cwd": self.root, "session_id": claude_sid, "prompt": "go on"})
            self.assertTrue(done.wait(5))
        return seen[0]

    def test_maintenance_gets_the_session_scope_not_the_cwd(self):
        self.sm.register(None, claude_sid="cc-user")
        self.sm.register(self.root, claude_sid="cc-project")
        self.assertIsNone(self.dispatched_project("cc-user"))
        self.assertEqual(self.dispatched_project("cc-project"), self.root)
        self.assertIsNone(self.dispatched_project("cc-nobody"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
