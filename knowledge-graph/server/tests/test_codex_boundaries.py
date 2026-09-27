#!/usr/bin/env python3
"""Codex regressions at the quota, HTTP MCP and hook boundaries.

Run directly with the server venv. Graphs and rollouts are temporary; the
HTTP test uses the real MCP transport in-process and needs no listening port.
"""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-codex-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name

import httpx2 as httpx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp_http import ambient, chore_dispatch as cd, file_recall
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
import mcp_streamable_server as srv


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kg-codex-quota-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.now = time.time()
        self.patcher = patch.object(cd, "codex_home", return_value=self.home)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def rollout(self, day, age, pct, *, written_age=None, event=True):
        folder = self.home / "sessions" / "2026" / "09" / day
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "rollout-test.jsonl"
        record = {"timestamp": datetime.fromtimestamp(self.now - age, timezone.utc).isoformat(),
                  "type": "event_msg", "payload": {"type": "token_count"}}
        if event:
            record["payload"]["rate_limits"] = {
                "primary": {"window_minutes": 300, "used_percent": pct,
                            "resets_at": self.now + 3600},
                "secondary": {"window_minutes": 10080, "used_percent": 20,
                              "resets_at": self.now + 86400}}
        target.write_text(json.dumps(record) + "\n")
        stamp = self.now - (age if written_age is None else written_age)
        os.utime(target, (stamp, stamp))
        return target

    def read(self):
        return cd._gauge_read({}, self.now, cd.RUNNERS["codex"])

    def test_resumed_old_directory_blocks_spending(self):
        self.rollout("20", 0, 95)
        for day, age, pct in [("25", 600, 5), ("26", 400, 8), ("27", 300, 10)]:
            self.rollout(day, age, pct)
        reading, raw, err = self.read()
        self.assertEqual(err, "")
        self.assertEqual(reading["5h"], 95)
        self.assertEqual(cd._chore_gauge_ok({}, raw), "5h 95%")
        self.assertIn("5h 95%", cd._pass_gauge_ok({}, raw, self.now))

    def test_event_recency_wins_over_file_mtime(self):
        self.rollout("20", 10, 95, written_age=5)
        self.rollout("27", 300, 10, written_age=0)
        self.assertEqual(self.read()[0]["5h"], 95)

    def test_empty_new_session_keeps_fresh_reading(self):
        self.rollout("20", 5, 95)
        self.rollout("27", 0, 0, event=False)
        self.assertEqual(self.read()[0]["5h"], 95)

    def test_only_empty_or_old_files_refuse(self):
        self.rollout("20", 100000, 5)
        self.rollout("27", 0, 0, event=False)
        self.assertTrue(self.read()[2].startswith("gauge unreadable"))

    def test_touching_stale_reading_does_not_refresh_it(self):
        self.rollout("20", 100000, 5, written_age=0)
        self.assertEqual(self.read()[2], "gauge stale")

    def test_invalid_timestamp_does_not_fall_back_to_mtime(self):
        target = self.rollout("20", 0, 5)
        record = json.loads(target.read_text())
        record["timestamp"] = "missing"
        target.write_text(json.dumps(record) + "\n")
        self.assertTrue(self.read()[2].startswith("gauge unreadable"))

    def test_future_reading_refuses(self):
        self.rollout("20", -60, 5)
        self.assertTrue(self.read()[2].startswith("gauge unreadable"))

    def test_conflicting_equal_timestamp_readings_refuse(self):
        self.rollout("20", 0, 5)
        self.rollout("27", 0, 95)
        self.assertTrue(self.read()[2].startswith("gauge unreadable"))

    def test_malformed_usage_refuses(self):
        for pct in [float("nan"), float("inf"), -1, 101, True, "5"]:
            with self.subTest(pct=pct):
                self.rollout("20", 0, pct)
                self.assertTrue(self.read()[2].startswith("gauge unreadable"))

    def test_old_files_are_not_read(self):
        self.rollout("20", 100000, 5)
        self.rollout("27", 0, 95)
        with patch.object(cd, "codex_limits", wraps=cd.codex_limits) as reader:
            self.assertEqual(self.read()[0]["5h"], 95)
            self.assertEqual(reader.call_count, 1)

    def test_unreadable_candidate_refuses(self):
        self.rollout("20", 0, 5)
        with patch.object(cd, "codex_limits", side_effect=PermissionError):
            self.assertTrue(self.read()[2].startswith("gauge unreadable"))

    def test_truncated_final_line_preserves_complete_reading(self):
        target = self.rollout("20", 0, 95)
        with target.open("a") as stream:
            stream.write('{"payload":{"rate_limits":')
        self.assertEqual(self.read()[0]["5h"], 95)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.TemporaryDirectory(prefix="kg-codex-project-", dir=Path.home() / ".cache")
        self.addCleanup(self.project.cleanup)
        self.root = Path(self.project.name).resolve()
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)
        file_recall.reset_throttle()

    def test_real_mcp_http_context_identifies_codex(self):
        async def exercise():
            with patch.object(srv, "store", self.store), patch.object(srv, "session_manager", self.sm):
                manager = StreamableHTTPSessionManager(app=srv.create_mcp_server(),
                            json_response=True, stateless=True)
                async with manager.run():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.handle_request),
                            base_url="http://localhost", headers={
                                "accept": "application/json, text/event-stream",
                                "user-agent": "codex-mcp-client/0.157.1"}) as client:
                        response = await client.post("/", json={"jsonrpc": "2.0", "id": 1,
                            "method": "tools/call", "params": {
                                "name": "kg_read", "arguments": {"cwd": str(self.root)}}})
                        self.assertEqual(response.status_code, 200, response.text)
                        result = response.json()["result"]
                        self.assertFalse(result.get("isError"), result)
                        self.assertIn("/hooks", result["content"][0]["text"])
                        sessions = [s for s in self.sm._sessions.values()
                                    if s.get("project_path") == str(self.root)]
                        self.assertEqual(len(sessions), 1)
                        self.assertEqual(sessions[0]["harness"], "codex")
                        # Non-Codex HTTP clients keep their existing behavior.
                        response = await client.post("/", headers={"user-agent": "claude-code/2.1"},
                            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                                "name": "kg_read", "arguments": {"cwd": str(self.root)}}})
                        self.assertNotIn("/hooks", response.json()["result"]["content"][0]["text"])
        asyncio.run(exercise())

    def event(self, command, **extra):
        return ambient.handle_tool_event(self.store, self.sm, {
            "cwd": str(self.root), "tool_name": "Bash", "session_id": "codex-test",
            "transcript_path": "/fixture/rollout-test.jsonl",
            "tool_input": {"command": command, **extra}})

    def test_missing_workdir_never_recalls_or_counts_wrong_file(self):
        (self.root / "nested").mkdir()
        for target in [self.root / "README.md", self.root / "nested/README.md"]:
            target.write_text("fixture\n")
        self.sm.register(str(self.root), claude_sid="codex-test", harness="codex")
        writer = self.sm.register(str(self.root))["session_id"]
        self.store.put_node(level="project", node_id="root-file", gist="WRONG_ROOT_MEMORY",
                            touches=["README.md"], session_id=writer)
        self.store.put_node(level="project", node_id="nested-file", gist="RIGHT_NESTED_MEMORY",
                            touches=["nested/README.md"], session_id=writer)
        self.assertIsNone(self.event("cat README.md"))
        self.assertFalse(ambient._events_path(str(self.root)).exists())
        recalled = self.event("cat README.md", workdir=str(self.root / "nested"))
        self.assertIn("RIGHT_NESTED_MEMORY", recalled)
        self.assertNotIn("WRONG_ROOT_MEMORY", recalled)
        events = json.loads(ambient._events_path(str(self.root)).read_text())["events"]
        self.assertIn("read:nested/README.md", events)
        self.assertNotIn("read:README.md", events)

    def test_absolute_shell_reads_still_count(self):
        target = self.root / "notes.md"
        target.write_text("fixture\n")
        self.sm.register(str(self.root), claude_sid="codex-test", harness="codex")
        self.event(f"cat {target}")
        events = json.loads(ambient._events_path(str(self.root)).read_text())["events"]
        self.assertIn("read:notes.md", events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
