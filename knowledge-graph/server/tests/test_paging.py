"""Paged kg_read replies through the real HTTP MCP application.

A reply longer than the client's tool-part limit arrives in parts; only what a
part showed counts as seen, read or promoted, and the full read completes with
its last part. Limits are lowered here so a small graph pages.
"""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
import logging
import os
os.environ["KG_BUDGET_NOTICES"] = "0"
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-paging-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name

import httpx2 as httpx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from core.exceptions import NodeConflictError
from mcp_http import harness, paging
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
import mcp_streamable_server as srv
logging.getLogger().setLevel(logging.WARNING)

AGENTS = {harness.CLAUDE_CODE: "claude-code/2.1.294", harness.CODEX: "codex-mcp-client/0.160.0"}
FOOTER = re.compile(r"\n\n\[KG reply part \d+ of \d+ — it continues\..*\]$", re.S)


class SplitTests(unittest.TestCase):
    def test_parts_fit_and_rejoin(self):
        cases = [("ascii", "line of memory text\n" * 900),
                 ("unicode", "урок 🙂 проверка границ\n" * 900),
                 ("one huge line", "x" * 20000 + "\nafter")]
        for name, text in cases:
            for measure in (harness.utf16_units, harness.utf8_bytes):
                parts = paging.split(text, 3000, measure)
                self.assertGreater(len(parts), 1, name)
                self.assertTrue(all(measure(p) <= 3000 - paging.FOOTER_RESERVE for p in parts), name)
                rejoined = "\n".join(parts) if name != "one huge line" else None
                if rejoined is not None:
                    self.assertEqual(rejoined, text, name)
        # A line cut inside loses no characters, only where the break falls.
        parts = paging.split("x" * 20000 + "\nafter", 3000, len)
        self.assertEqual("".join(parts).replace("\n", ""), "x" * 20000 + "after")


class PagingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.project = tempfile.TemporaryDirectory(prefix=".kg-paging-test-",
                                                   dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.project.cleanup)
        self.root = Path(self.project.name)
        storage = tempfile.TemporaryDirectory(prefix="kg-paging-case-")
        self.addCleanup(storage.cleanup)
        self.stack = AsyncExitStack()
        self.stack.enter_context(patch.dict(os.environ, {"KG_STORAGE_ROOT": storage.name}))
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)
        self.stack.enter_context(patch.object(srv, "store", self.store))
        self.stack.enter_context(patch.object(srv, "session_manager", self.sm))
        self.stack.enter_context(patch.dict(harness.PROFILES, {
            name: replace(harness.PROFILES[name], tool_part_limit=4000) for name in AGENTS}))
        self.writer = self.sm.register(str(self.root))["session_id"]
        for i in range(60):
            self.store.put_node(level="project", node_id=f"lesson-{i:02d}",
                                gist=f"Lesson {i}: a gist long enough that sixty of them page " * 2,
                                notes=[f"note {i} " * 40], session_id=self.writer, guard=False)

    async def asyncTearDown(self):
        await self.stack.aclose()

    async def call(self, arguments, client=harness.CLAUDE_CODE):
        manager = StreamableHTTPSessionManager(app=srv.create_mcp_server(),
                                               json_response=True, stateless=True)
        async with manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.handle_request),
                    base_url="http://localhost", headers={
                        "accept": "application/json, text/event-stream",
                        "user-agent": AGENTS[client]}) as client_:
                response = await client_.post("/", json={"jsonrpc": "2.0", "id": 1,
                    "method": "tools/call", "params": {"name": "kg_read", "arguments": arguments}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]["content"][0]["text"]

    async def read_all(self, arguments, client=harness.CLAUDE_CODE):
        parts = [await self.call(arguments, client)]
        sid = arguments["session_id"]
        while FOOTER.search(parts[-1]):
            parts.append(await self.call({"session_id": sid, "more": True}, client))
            self.assertLess(len(parts), 50)
        return parts

    def active_ids(self):
        return {f"lesson-{i:02d}" for i in range(60)}

    async def test_full_read_marks_seen_part_by_part(self):
        sid = self.sm.register(str(self.root))["session_id"]
        first = await self.call({"session_id": sid})
        self.assertRegex(first, FOOTER)
        self.assertLessEqual(harness.utf16_units(first), 4000)
        seen = self.sm.get_seen(sid)
        shown = paging._shown_in(first, self.active_ids())
        self.assertTrue(shown)
        self.assertEqual(seen & self.active_ids(), shown, "only part 1's nodes are seen")
        self.assertFalse(self.sm.has_full_read(sid), "the full read is not complete yet")
        parts = [first]
        while FOOTER.search(parts[-1]):
            parts.append(await self.call({"session_id": sid, "more": True}))
        self.assertGreater(len(parts), 2)
        self.assertEqual(self.sm.get_seen(sid) & self.active_ids(), self.active_ids())
        self.assertTrue(self.sm.has_full_read(sid))
        self.assertIn("I have recalled KG Memories", parts[-1])
        # The parts rejoin into the reply an unpaged client would have had.
        other = self.sm.register(str(self.root))["session_id"]
        with patch.dict(harness.PROFILES, {harness.CLAUDE_CODE: replace(
                harness.PROFILES[harness.CLAUDE_CODE], tool_part_limit=10 ** 6)}):
            whole = await self.call({"session_id": other})
        joined = "\n".join(FOOTER.sub("", p) for p in parts)
        self.assertEqual(joined, whole.replace(other, sid))
        self.assertIn("Nothing more to read", await self.call({"session_id": sid, "more": True}))

    async def test_codex_batch_defers_reads_and_promotion(self):
        graph_key = next(k for k in self.store.graphs if k.startswith("project:"))
        self.store.graphs[graph_key]["nodes"]["lesson-59"]["_archived"] = True
        sid = self.sm.register(str(self.root))["session_id"]
        ids = [f"lesson-{i:02d}" for i in range(50, 60)]
        first = await self.call({"session_id": sid, "ids": ids}, harness.CODEX)
        self.assertRegex(first, FOOTER)
        self.assertLessEqual(harness.utf8_bytes(first), 4000)
        self.assertNotIn("lesson-59", paging._shown_in(first, ids))
        self.assertNotIn("lesson-59", self.sm.get_seen(sid))
        self.assertTrue(self.store.graphs[graph_key]["nodes"]["lesson-59"].get("_archived"),
                        "an undelivered archived node is not promoted")
        await self.read_all_rest(sid, first)
        self.assertTrue(set(ids) <= self.sm.get_seen(sid))
        self.assertFalse(self.store.graphs[graph_key]["nodes"]["lesson-59"].get("_archived"),
                         "delivered, it is promoted")

    async def read_all_rest(self, sid, first, client=harness.CODEX):
        last = first
        while FOOTER.search(last):
            last = await self.call({"session_id": sid, "more": True}, client)

    async def test_a_new_read_or_compaction_drops_pending_parts(self):
        sid = self.sm.register(str(self.root))["session_id"]
        self.assertRegex(await self.call({"session_id": sid}), FOOTER)
        await self.call({"session_id": sid, "id": "lesson-01"})
        self.assertIn("Nothing more to read", await self.call({"session_id": sid, "more": True}))
        self.assertRegex(await self.call({"session_id": sid}), FOOTER)
        self.sm.reset_context(sid)
        self.assertIn("Nothing more to read", await self.call({"session_id": sid, "more": True}))
        self.assertFalse(self.sm.has_full_read(sid))

    async def test_a_node_counts_as_read_only_once_its_whole_block_went_out(self):
        # formal/delivery X1: a block whose notes run on into the next part was
        # credited as read with the part showing its header, so a write over
        # notes the session never received went through.
        notes = [f"case {i}: " + "what happened " * 6 for i in range(40)]
        self.store.put_node(level="project", node_id="long-notes", gist="a lesson with many cases",
                            notes=notes, session_id=self.writer, guard=False)
        sid = self.sm.register(str(self.root))["session_id"]
        ids = [f"lesson-{i:02d}" for i in range(4)] + ["long-notes"]
        first = await self.call({"session_id": sid, "ids": ids})
        self.assertRegex(first, FOOTER)
        self.assertIn("▸ long-notes (", first)
        self.assertNotIn(notes[-1], first, "the block continues in part 2")
        self.assertIsNone(self.sm.viewed_at(sid, "long-notes", full=True))
        shown = [n for n in notes if f"    - {n}\n" in first + "\n"]
        with self.assertRaises(NodeConflictError):
            self.store.put_node(level="project", node_id="long-notes", gist="a lesson with many cases",
                                notes=shown + ["mine"], session_id=sid)
        self.assertEqual(len(self.store.read_node("long-notes", session_id=self.writer)["node"]["notes"]),
                         len(notes))
        await self.read_all_rest(sid, first, harness.CLAUDE_CODE)
        self.assertIsNotNone(self.sm.viewed_at(sid, "long-notes", full=True))
        self.assertTrue(set(ids) <= self.sm.get_seen(sid))

    async def test_a_reply_that_fits_is_one_part(self):
        sid = self.sm.register(str(self.root))["session_id"]
        text = await self.call({"session_id": sid, "id": "lesson-02"})
        self.assertNotRegex(text, FOOTER)
        self.assertIn("lesson-02", self.sm.get_seen(sid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
