#!/usr/bin/env python3
"""Antigravity boundaries through real HTTP MCP and REST applications.

No Google account or listening socket is needed. Run with the server venv.
The separate CLI smoke probe exercises native packaging against agy itself.
"""

import asyncio
from contextlib import AsyncExitStack
import json
import logging
import os
os.environ["KG_BUDGET_NOTICES"] = "0"  # hook outputs below are exact; budget.py has its own tests
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-agy-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name

import httpx2 as httpx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp_http import antigravity, chore_dispatch, file_recall, harness
from mcp_http.delivery import DeferredView, HOOK_BYTES, QUEUE_BYTES
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
import mcp_streamable_server as srv
logging.getLogger().setLevel(logging.WARNING)


class AntigravityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Projects must sit under home; this location is also sandbox-writable.
        self.project = tempfile.TemporaryDirectory(prefix=".kg-agy-test-",
                         dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.project.cleanup)
        self.root = Path(self.project.name)
        storage = tempfile.TemporaryDirectory(prefix="kg-agy-case-")
        self.addCleanup(storage.cleanup)
        self.stack = AsyncExitStack()
        self.stack.enter_context(patch.dict(os.environ, {"KG_STORAGE_ROOT": storage.name}))
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)
        file_recall.reset_throttle()
        self.stack.enter_context(patch.object(srv, "store", self.store))
        self.stack.enter_context(patch.object(srv, "session_manager", self.sm))
        self.rest = await self.stack.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_rest_api(self.store, self.sm, None, "test")),
            base_url="http://localhost"))
        self.writer = self.sm.register(str(self.root))["session_id"]

    async def asyncTearDown(self):
        await self.stack.aclose()

    async def call(self, name, arguments, cid="conversation-a"):
        response = await self.mcp_request({"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": name, "arguments": arguments,
                "_meta": {"antigravity.google/conversation_id": cid}}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    async def mcp_request(self, payload):
        manager = StreamableHTTPSessionManager(app=srv.create_mcp_server(),
                                               json_response=True, stateless=True)
        # anyio's cancel scope must enter and exit in this same task.
        async with manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.handle_request),
                    base_url="http://localhost", headers={
                        "accept": "application/json, text/event-stream", "user-agent": "Go-http-client/1.1"}) as client:
                return await client.post("/", json=payload)

    async def hook(self, event="PreInvocation", cid="conversation-a", **extra):
        payload = {"conversationId": cid, "workspacePaths": [str(self.root)],
                   "transcriptPath": str(self.root / ".gemini/antigravity-cli/transcript_full.jsonl"),
                   "invocationNum": 1, **extra}
        response = await self.rest.post("/api/antigravity/hook/" + event, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def ack(self, packet, cid="conversation-a"):
        response = await self.rest.post("/api/antigravity/ack", json={
            "conversationId": cid, "delivery_id": packet["delivery_id"]})
        return response.json()["ok"]

    def text(self, packet):
        return packet["output"]["injectSteps"][0]["systemMessage"]["systemMessage"]

    def sid(self, cid="conversation-a"):
        return self.sm.find_by_claude_sid(cid)[0]

    async def bootstrap(self, cid="conversation-a"):
        packet = await self.hook("SessionStart", cid)
        self.assertIn("KG MEMORY PRELOADED", self.text(packet))
        self.assertTrue(await self.ack(packet, cid))
        return self.sid(cid)

    def seed(self, nid="large-memory", gist="snapshot gist", notes=None, touches=None):
        return self.store.put_node(level="project", node_id=nid, gist=gist,
            notes=notes or [], touches=touches or [], session_id=self.writer, guard=False)

    async def drain(self, cid="conversation-a"):
        parts = []
        for _ in range(40):
            packet = await self.hook(cid=cid)
            if not packet["output"]:
                return parts
            text = self.text(packet)
            self.assertLessEqual(len(text.encode("utf-8")), HOOK_BYTES)
            parts.append(text)
            self.assertTrue(await self.ack(packet, cid))
        self.fail("Delivery did not drain")

    async def test_metadata_detection_and_native_tool_configuration(self):
        self.assertEqual(harness.from_user_agent("Go-http-client/1.1"), harness.CLAUDE_CODE)
        path = "/home/u/.gemini/antigravity-cli/brain/id/transcript_full.jsonl"
        self.assertEqual(harness.from_transcript(path), harness.ANTIGRAVITY)
        self.assertEqual(harness.from_transcript(path.replace("antigravity-cli", "antigravity-ide")),
                         harness.CLAUDE_CODE)
        response = await self.mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {tool["name"] for tool in response.json()["result"]["tools"]}
        plugin = Path(__file__).resolve().parents[2]
        eager = json.loads((plugin / "mcp_config.json").read_text())["mcpServers"]["kg"]["tools"]
        self.assertEqual(set(eager), tools)
        self.assertTrue(all(value == {"eager": True} for value in eager.values()))
        # Claude Code and Codex keep their own hooks and reach the server through kg mcp.
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", (plugin / "hooks/hooks.json").read_text())
        self.assertIn("kg mcp", (plugin / ".mcp.json").read_text())

    async def test_preload_view_waits_for_ack_and_new_prompt_retries(self):
        self.seed("bootstrap-node", "BOOTSTRAP_GIST")
        packet = await self.hook("SessionStart")
        sid = self.sid()
        self.assertNotIn("bootstrap-node", self.sm.get_seen(sid))
        retried = await self.hook(invocationNum=0)
        self.assertEqual(retried, packet)
        self.assertFalse(await self.ack(packet, "unknown-conversation"))
        self.assertNotIn("bootstrap-node", self.sm.get_seen(sid))
        self.assertTrue(await self.ack(packet))
        self.assertIn("bootstrap-node", self.sm.get_preloaded(sid))
        self.assertFalse(await self.ack(packet))
        self.assertEqual((await self.hook())["output"], {})

    async def test_large_unicode_batch_only_commits_after_last_chunk(self):
        sid = await self.bootstrap()
        notes = "BEGIN_UNICODE_" + "кава☕" * 9000 + "_END_UNICODE"
        self.seed(notes=[notes])
        self.seed("second-memory", "SECOND_GIST")
        result = await self.call("kg_read", {"session_id": sid, "ids": ["large-memory", "second-memory"]})
        self.assertIn("queued", result["content"][0]["text"])
        self.assertIsNone(self.sm.viewed_at(sid, "large-memory", full=True))
        self.assertNotIn("second-memory", self.sm.get_seen(sid))
        parts = []
        while self.sm.has_pending_context(sid):
            packet = await self.hook(invocationNum=0)  # Survives a new prompt.
            text = self.text(packet)
            self.assertLessEqual(len(text.encode()), HOOK_BYTES)
            self.assertNotIn("�", text)
            if "delivery continues" in text:
                self.assertIsNone(self.sm.viewed_at(sid, "large-memory", full=True))
            parts.append(text)
            await self.ack(packet)
        self.assertGreater(len(parts), 1)
        self.assertIn("BEGIN_UNICODE_", parts[0])
        self.assertIn("_END_UNICODE", "".join(parts))
        self.assertEqual("".join(parts).count("☕"), 9000)
        self.assertIn("second-memory", self.sm.get_seen(sid))
        self.assertIsNotNone(self.sm.viewed_at(sid, "large-memory", full=True))

    async def test_deferred_read_timestamp_does_not_authorize_a_stale_write(self):
        sid = await self.bootstrap()
        self.seed(notes=["OLD " + "x" * 10000])
        await self.call("kg_read", {"session_id": sid, "id": "large-memory"})
        snapshot_time = time.time()
        self.seed(gist="CONCURRENT_GIST", notes=["NEW " + "x" * 10000])
        await self.drain()
        self.assertLessEqual(self.sm.viewed_at(sid, "large-memory", full=True), snapshot_time)
        before_conflict = self.sm.viewed_at(sid, "large-memory", full=True)
        result = await self.call("kg_put_node", {"session_id": sid, "level": "project",
            "id": "large-memory", "gist": "STALE_WRITE", "notes": ["OLD"]})
        self.assertIn("queued", result["content"][0]["text"])
        self.assertEqual(self.sm.viewed_at(sid, "large-memory", full=True), before_conflict)
        parts = await self.drain()
        self.assertIn("NOT WRITTEN", "".join(parts))
        self.assertIn("CONCURRENT_GIST", "".join(parts))
        self.assertGreater(self.sm.viewed_at(sid, "large-memory", full=True), before_conflict)
        self.assertEqual(self.store.read_node("large-memory", session_id=sid)["node"]["gist"], "CONCURRENT_GIST")

    async def test_full_read_is_outstanding_until_context_delivery(self):
        sid = await self.bootstrap()
        for i in range(30):
            self.seed(f"graph-memory-{i}", f"Unique topic {i} " + "g" * 300)
        result = await self.call("kg_read", {"session_id": sid})
        self.assertIn("queued", result["content"][0]["text"])
        self.assertFalse(self.sm.has_full_read(sid))
        parts = await self.drain()
        self.assertIn('announce "I have recalled KG Memories"', "".join(parts))
        self.assertTrue(self.sm.has_full_read(sid))
        self.assertIn("graph-memory-29", self.sm.get_seen(sid))

    async def test_conversations_share_graphs_but_not_pending_output_or_session_ids(self):
        a = await self.bootstrap()
        b = await self.bootstrap("conversation-b")
        self.seed(notes=["PRIVATE_REPLY_A" + "x" * 8000])
        await self.call("kg_read", {"session_id": a, "id": "large-memory"})
        self.assertEqual((await self.hook(cid="conversation-b"))["output"], {})
        rejected = await self.call("kg_put_node", {"session_id": a, "level": "project",
            "id": "cross-session-node", "gist": "SHOULD_NOT_WRITE"}, cid="conversation-b")
        self.assertTrue(rejected["isError"])
        self.assertIn("another conversation", rejected["content"][0]["text"])
        self.assertNotIn("large-memory", self.sm.get_seen(b))
        self.assertIn("PRIVATE_REPLY_A", "".join(await self.drain()))

    async def test_other_conversations_hook_evidence_cannot_enable_large_mcp_reply(self):
        await self.bootstrap()
        self.seed(notes=["x" * 8000])
        result = await self.call("kg_read", {"cwd": str(self.root), "id": "large-memory"}, cid="no-hooks")
        self.assertTrue(result["isError"])
        self.assertIn("No Antigravity memory hook", result["content"][0]["text"])
        sid = self.sid("no-hooks")
        self.assertFalse(self.sm.has_pending_context(sid))
        self.assertIsNone(self.sm.viewed_at(sid, "large-memory", full=True))

    def transcript(self, *rows):
        path = self.root / ".gemini/antigravity-cli/transcript_full.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            stream.writelines(json.dumps(row) + "\n" for row in rows)

    async def test_checkpoint_restores_preload_and_forgets_what_the_summary_dropped(self):
        self.transcript({"type": "USER_INPUT", "step_index": 1, "content": "<USER_REQUEST>hi</USER_REQUEST>"})
        sid = await self.bootstrap()
        self.seed("outside-preload", "OUTSIDE_GIST")
        viewed = time.time()
        self.sm.mark_seen(sid, ["outside-preload"], via="read", at=viewed, full=True)
        self.sm.mark_full_read(sid)
        self.assertEqual((await self.hook())["output"], {})  # No checkpoint yet.
        self.transcript({"type": "CHECKPOINT", "step_index": 9, "content": "{{ CHECKPOINT 0 }} summary"})
        packet = await self.hook(invocationNum=4)  # Compaction can land mid-turn.
        self.assertIn("KG MEMORY PRELOADED", self.text(packet))
        self.assertNotIn("outside-preload", self.sm.get_seen(sid))
        self.assertFalse(self.sm.has_full_read(sid))
        self.assertEqual(self.sm.viewed_at(sid, "outside-preload", full=True), viewed)
        self.assertTrue(await self.ack(packet))
        self.assertIn("outside-preload", self.sm.get_preloaded(sid))
        self.assertEqual((await self.hook())["output"], {})  # Same checkpoint: once.

    async def test_checkpoint_during_delivery_replays_from_the_start(self):
        self.transcript({"type": "USER_INPUT", "step_index": 1, "content": "<USER_REQUEST>hi</USER_REQUEST>"})
        sid = await self.bootstrap()
        self.seed(notes=["BEGIN_REPLY_" + "z" * 90000 + "_END_REPLY"])
        await self.call("kg_read", {"session_id": sid, "id": "large-memory"})
        first = await self.hook()
        self.assertIn("BEGIN_REPLY_", self.text(first))
        self.assertTrue(await self.ack(first))
        outstanding = await self.hook()
        self.assertNotIn("BEGIN_REPLY_", self.text(outstanding))
        self.transcript({"type": "CHECKPOINT", "step_index": 9, "content": "{{ CHECKPOINT 0 }} summary"})
        replay = await self.hook()
        self.assertTrue(self.text(replay).startswith("KG context — preload:"))
        self.assertFalse(await self.ack(outstanding))  # Old-context receipt.
        self.assertTrue(await self.ack(replay))
        rest = "".join(await self.drain())
        self.assertIn("BEGIN_REPLY_", self.text(replay) + rest)
        self.assertIn("_END_REPLY", rest)
        self.assertIsNotNone(self.sm.viewed_at(sid, "large-memory", full=True))

    def archive(self, nid):
        _, key = self.store._resolve_graph_key("project", self.writer, None)
        node = self.store.graphs[key]["nodes"][nid]
        node["_archived"] = True
        node.pop("_last_read_ts", None)
        return node

    async def test_refused_read_leaves_the_graph_unchanged(self):
        self.seed("archived-memory", notes=["x" * 10000])
        node = self.archive("archived-memory")
        result = await self.call("kg_read", {"cwd": str(self.root), "id": "archived-memory"},
                                 cid="no-hooks")
        self.assertTrue(result["isError"])
        self.assertTrue(node.get("_archived"))
        self.assertNotIn("_last_read_ts", node)
        self.assertNotIn("archived-memory", self.sm.lookup(self.sid("no-hooks")).get("promoted_ids", []))

    async def test_queued_read_promotes_only_after_its_last_chunk(self):
        sid = await self.bootstrap()
        self.seed("archived-memory", notes=["y" * 60000])
        node = self.archive("archived-memory")
        await self.call("kg_read", {"session_id": sid, "id": "archived-memory"})
        packet = await self.hook()
        self.assertIn("delivery continues", self.text(packet))
        self.assertTrue(await self.ack(packet))
        self.assertTrue(node.get("_archived"))
        self.assertNotIn("_last_read_ts", node)
        await self.drain()
        self.assertNotIn("_archived", node)
        self.assertIn("_last_read_ts", node)
        self.assertIn("archived-memory", self.sm.lookup(sid)["promoted_ids"])

    async def test_inline_read_promotes_at_once(self):
        sid = await self.bootstrap()
        self.seed("archived-memory", "SMALL_ARCHIVED")
        node = self.archive("archived-memory")
        result = await self.call("kg_read", {"session_id": sid, "id": "archived-memory"})
        self.assertIn("SMALL_ARCHIVED", result["content"][0]["text"])
        self.assertNotIn("_archived", node)
        self.assertIn("_last_read_ts", node)

    async def test_small_reply_without_hooks_is_inline_and_bound_by_metadata(self):
        result = await self.call("kg_read", {"cwd": str(self.root)}, cid="no-hooks")
        sid = self.sid("no-hooks")
        self.assertIn(f"Session: {sid}", result["content"][0]["text"])
        self.assertTrue(self.sm.has_full_read(sid))
        self.assertEqual(self.sm.lookup(sid)["harness"], harness.ANTIGRAVITY)
        again = await self.call("kg_read", {}, cid="no-hooks")
        self.assertIn(f"Session: {sid}", again["content"][0]["text"])

    async def test_generic_progress_and_sync_output_use_the_same_queue(self):
        sid = await self.bootstrap()
        await self.call("kg_progress", {"session_id": sid, "task_id": "probe",
            "state": {"large": "PROGRESS_BEGIN_" + "p" * 8000 + "_PROGRESS_END"}})
        result = await self.call("kg_progress", {"session_id": sid, "task_id": "probe"})
        self.assertIn("queued", result["content"][0]["text"])
        self.assertIn("_PROGRESS_END", "".join(await self.drain()))
        before = self.sm.get_sync_ts(sid)
        nodes = {f"sync-node-{i}": {"gist": "s" * 100} for i in range(60)}
        diff = {"user": {"nodes": {}, "edges": {}}, "project": {"nodes": nodes, "edges": {}}}
        with patch.object(self.store, "get_sync_diff", return_value=diff):
            result = await self.call("kg_sync", {"session_id": sid})
        snapshot_time = time.time()
        self.assertIn("queued", result["content"][0]["text"])
        self.assertEqual(self.sm.get_sync_ts(sid), before)
        parts = await self.drain()
        self.assertIn("sync-node-59", "".join(parts))
        self.assertGreater(self.sm.get_sync_ts(sid), before)
        self.assertLessEqual(self.sm.get_sync_ts(sid), snapshot_time)

    async def test_pending_context_and_snapshot_effects_survive_server_restart(self):
        sid = await self.bootstrap()
        self.seed(notes=["PERSISTED_REPLY" + "x" * 8000])
        await self.call("kg_read", {"session_id": sid, "id": "large-memory"})
        packet = self.sm.prepare_context(sid)
        restored = HTTPSessionManager()
        self.assertEqual(restored.prepare_context(sid), packet)
        self.assertIsNone(restored.viewed_at(sid, "large-memory", full=True))
        self.assertIsNotNone(restored.acknowledge_context(sid, packet["id"]))
        self.assertIsNotNone(restored.viewed_at(sid, "large-memory", full=True))

    async def test_post_tool_recall_is_delivered_on_next_invocation_and_dedups(self):
        sid = await self.bootstrap()
        target = self.root / "component.py"
        target.write_text("fixture\n")
        self.seed("component-memory", "COMPONENT_CONTEXT", touches=["component.py"])
        packet = await self.hook("PostToolUse", toolCall={
            "name": "replace_file_content", "args": {"TargetFile": str(target)}})
        self.assertEqual(packet, {"output": {}})
        self.assertNotIn("component-memory", self.sm.get_seen(sid))
        parts = await self.drain()
        self.assertIn("COMPONENT_CONTEXT", "".join(parts))
        self.assertIn("component-memory", self.sm.get_seen(sid))
        await self.hook("PostToolUse", toolCall={"name": "view_file", "args": {"AbsolutePath": str(target)}})
        self.assertEqual((await self.hook())["output"], {})

    async def test_shell_read_uses_command_cwd_and_never_workspace_fallback(self):
        await self.bootstrap()
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "README.md").write_text("fixture")
        self.seed("nested-memory", "RIGHT_NESTED_CONTEXT", touches=["nested/README.md"])
        self.seed("root-memory", "WRONG_ROOT_CONTEXT", touches=["README.md"])
        await self.hook("PostToolUse", toolCall={"name": "run_command", "args": {"CommandLine": "cat README.md"}})
        self.assertEqual((await self.hook())["output"], {})
        await self.hook("PostToolUse", toolCall={"name": "run_command", "args": {
            "CommandLine": "cat README.md", "Cwd": str(nested)}})
        parts = "".join(await self.drain())
        self.assertIn("RIGHT_NESTED_CONTEXT", parts)
        self.assertNotIn("WRONG_ROOT_CONTEXT", parts)

    async def test_prompt_recall_reads_human_record_and_never_dispatches_a_chore(self):
        sid = await self.bootstrap()
        self.sm.mark_full_read(sid)
        self.seed("lunar-orbit-memory", "Lunar orbit correction uses ephemeris tables.")
        for i in range(8):
            self.seed(f"unrelated-node-{i}", f"Unrelated fixture topic {i}")
        transcript = self.root / ".gemini/antigravity-cli/transcript_full.jsonl"
        transcript.parent.mkdir(parents=True)
        rows = [{"type": "USER_INPUT", "step_index": 7, "created_at": "one",
                 "content": "<USER_REQUEST>\nPlease examine the lunar orbit ephemeris.\n</USER_REQUEST>\n<ADDITIONAL_METADATA>fake metadata</ADDITIONAL_METADATA>"},
                {"type": "USER_MESSAGE", "content": "INJECTED_BAD_PROMPT"}]
        transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with patch.object(chore_dispatch, "maybe_dispatch") as dispatch:
            packet = await self.hook(invocationNum=0)
            self.assertIn("Lunar orbit correction", self.text(packet))
            self.assertNotIn("fake metadata", self.text(packet))
            self.assertNotIn("lunar-orbit-memory", self.sm.get_seen(sid))
            await self.ack(packet)
            self.assertIn("lunar-orbit-memory", self.sm.get_seen(sid))
            self.assertEqual((await self.hook(invocationNum=0))["output"], {})
            dispatch.assert_not_called()

    async def test_a_new_prompt_dispatches_maintenance_when_chores_are_on(self):
        await self.bootstrap()
        self.transcript({"type": "USER_INPUT", "step_index": 1, "content": "<USER_REQUEST>tidy</USER_REQUEST>"})
        with patch.object(chore_dispatch, "enabled", return_value=True), \
                patch.object(chore_dispatch, "maybe_dispatch") as dispatch:
            await self.hook(invocationNum=0)
            await self.hook(invocationNum=1)
            for _ in range(50):
                if dispatch.called:
                    break
                await asyncio.sleep(0.01)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(dispatch.call_args.args[2], str(self.root))

    async def test_missing_workspace_is_user_only_and_ide_events_are_ignored(self):
        await self.hook("SessionStart", cid="folderless", workspacePaths=[])
        self.assertEqual(self.sm.lookup(self.sid("folderless"))["scope"], "user")
        packet = await self.hook("SessionStart", cid="ide", transcriptPath="/home/u/.gemini/antigravity-ide/brain/full.jsonl")
        self.assertEqual(packet, {"output": {}})
        self.assertIsNone(self.sm.find_by_claude_sid("ide"))

    async def test_queue_overflow_keeps_earlier_reads_and_does_not_commit_rejected_view(self):
        sid = await self.bootstrap()
        view = DeferredView(self.sm)
        view.mark_seen(sid, ["never-delivered"], via="read", at=time.time(), full=True)
        self.assertTrue(self.sm.queue_context(sid, "EARLIER_REPLY", "read"))
        self.assertFalse(self.sm.queue_context(sid, "x" * QUEUE_BYTES, "read", view.effects))
        self.assertNotIn("never-delivered", self.sm.get_seen(sid))
        self.assertIn("EARLIER_REPLY", "".join(await self.drain()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
