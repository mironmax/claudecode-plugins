"""Reproduce X2-X4 against the real Antigravity delivery path.

Real: the MCP application (srv.create_mcp_server) with Antigravity's
conversation metadata, the REST hook and ack routes (rest.create_rest_api),
antigravity.handle_event, delivery.py's queue, HTTPSessionManager and
MultiProjectGraphStore with its F11 stale-write guard. Nothing is stubbed;
transcripts are files the test writes, as agy would.

  X2a  A kg_search reply carries the "other sessions wrote" notice. The reply
       is queued for the next hook, but the notice already marked the node
       seen at that moment, so a write built on the older view goes through.
  X2b  The same reply refused (no hooks in this conversation): the notice's
       seen mark stays, and the overwrite goes through too.
  X3   A full read rendered while node a was preloaded shows a as a bare
       "(preloaded)" anchor and marks it seen. A checkpoint replays the read
       after a new preload that no longer shows a: a counts as seen, though no
       part of the new context shows its gist.
  X4   A checkpoint arrives while the queue is at its bound: the fresh preload
       is refused, and the replayed replies reach the new context before it.
Prints PASS per case once fixed.
"""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-repro-agy-")
os.environ["KG_BUDGET_NOTICES"] = "0"
os.environ["KG_AUTOCOMMIT_INTERVAL"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
import logging; logging.disable(logging.WARNING)

import httpx2 as httpx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp_http import delivery
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
import mcp_streamable_server as srv

RESULTS = {}


class World:
    def __init__(self):
        os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-repro-agy-")
        self.root = Path(tempfile.mkdtemp(prefix=".kg-repro-agy-", dir=Path.home()))
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        srv.store, srv.session_manager = self.store, self.sm
        self.app = create_rest_api(self.store, self.sm, None, "repro")
        self.writer = self.sm.register(str(self.root))["session_id"]
        self.transcript = self.root / ".gemini/antigravity-cli/transcript_full.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.row({"type": "USER_INPUT", "step_index": 1, "content": "<USER_REQUEST>hi</USER_REQUEST>"})

    def row(self, row):
        with self.transcript.open("a") as f:
            f.write(json.dumps(row) + "\n")

    async def call(self, name, arguments, cid="conv-a"):
        manager = StreamableHTTPSessionManager(app=srv.create_mcp_server(),
                                               json_response=True, stateless=True)
        async with manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.handle_request),
                    base_url="http://localhost", headers={
                        "accept": "application/json, text/event-stream",
                        "user-agent": "Go-http-client/1.1"}) as c:
                r = await c.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments,
                               "_meta": {"antigravity.google/conversation_id": cid}}})
        return r.json()["result"]["content"][0]["text"]

    async def rest(self, path, payload):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                     base_url="http://localhost") as c:
            return (await c.post(path, json=payload)).json()

    async def hook(self, event="PreInvocation", cid="conv-a"):
        return await self.rest("/api/antigravity/hook/" + event, {
            "conversationId": cid, "workspacePaths": [str(self.root)],
            "transcriptPath": str(self.transcript), "invocationNum": 1})

    async def ack(self, packet, cid="conv-a"):
        return (await self.rest("/api/antigravity/ack", {
            "conversationId": cid, "delivery_id": packet["delivery_id"]}))["ok"]

    @staticmethod
    def text(packet):
        return packet["output"]["injectSteps"][0]["systemMessage"]["systemMessage"]

    async def drain(self, cid="conv-a"):
        texts = []
        for _ in range(60):
            packet = await self.hook(cid=cid)
            if not packet["output"]:
                return texts
            texts.append(self.text(packet))
            assert await self.ack(packet, cid)
        raise RuntimeError("did not drain")

    def sid(self, cid="conv-a"):
        return self.sm.find_by_claude_sid(cid)[0]

    def put(self, nid, gist, sid, notes=None, guard=True):
        return self.store.put_node("project", nid, gist, notes=notes, session_id=sid, guard=guard)

    def node(self, nid):
        key = next(k for k in self.store.graphs if k.startswith("project:"))
        return self.store.graphs[key]["nodes"][nid]

    def close(self):
        self.store.shutdown()
        import shutil; shutil.rmtree(self.root, ignore_errors=True)


async def x2(variant):
    w = World()
    for i in range(14):   # enough matches that the search reply exceeds 3,500 bytes
        w.put(f"key-rotation-{i}", f"key rotation step {i}: " + "rotate signing keys per tenant " * 7,
              w.writer)
    cid = "conv-a" if variant == "queued" else "no-hooks"
    if variant == "queued":
        p = await w.hook("SessionStart"); assert await w.ack(p)
    w.put("cache-policy", "cache headers per route: v1", w.writer)
    if variant == "queued":
        await w.call("kg_read", {"session_id": w.sid(), "id": "cache-policy"})
    else:
        await w.call("kg_read", {"cwd": str(w.root), "id": "cache-policy"}, cid=cid)
    s = w.sid(cid)
    seen0 = w.sm.viewed_at(s, "cache-policy")
    time.sleep(0.01)
    t = w.sm.register(str(w.root))["session_id"]
    w.put("cache-policy", "cache headers per route: v2, T moved them to the edge", t)
    time.sleep(0.01)
    reply = await w.call("kg_search", {"session_id": s, "query": "key rotation"}, cid=cid)
    status = "queued" if "queued" in reply else ("refused" if "too large" in reply else "inline")
    seen1 = w.sm.viewed_at(s, "cache-policy")
    put = await w.call("kg_put_node", {"session_id": s, "level": "project", "id": "cache-policy",
                                       "gist": "cache headers per route: v1, plus S's change"},
                       cid=cid)
    written = "saved" in put
    print(f"X2{'a' if variant == 'queued' else 'b'} kg_search reply {status}; cache-policy seen_at "
          f"{'advanced' if seen1 != seen0 else 'unchanged'} before any delivery; "
          f"S's write on its v1 view: {'written' if written else 'refused'}; "
          f"gist now {w.node('cache-policy')['gist']!r}")
    ok = not written and seen1 == seen0
    print("   " + ("ok" if ok else "BUG: T's change is overwritten by a write built on the older view"))
    if variant == "queued" and ok:
        await w.drain(cid)
        put = await w.call("kg_put_node", {"session_id": s, "level": "project", "id": "cache-policy",
                                           "gist": "cache headers per route: v2 + S"}, cid=cid)
        print("   once delivered, the notice counts as a view: S's merged write",
              "goes through" if "saved" in put else "is refused")
        ok = "saved" in put
    RESULTS[f"X2{'a' if variant == 'queued' else 'b'}"] = ok
    w.close()


async def x3():
    w = World()
    old = time.time() - 90 * 86400
    w.put("anchor-node", "ANCHOR_GIST rarely used lesson", w.writer)
    for f in ("_created_ts", "_last_read_ts"):
        w.node("anchor-node")[f] = old
    w.node("anchor-node")["_written"]["ts"] = old
    p = await w.hook("SessionStart"); assert await w.ack(p)
    s = w.sid()
    preload_had = "anchor-node" in w.sm.get_preloaded(s)
    for i in range(45):   # other sessions write; the preload budget now drops anchor-node
        w.put(f"new-lesson-{i:02d}", f"lesson {i}: " + "fresh knowledge from another session " * 8,
              w.writer)
    reply = await w.call("kg_read", {"session_id": s})
    assert "queued" in reply, reply
    w.row({"type": "CHECKPOINT", "step_index": 9, "content": "{{ CHECKPOINT 0 }} summary"})
    texts = await w.drain()
    ctx = "\n".join(texts)
    new_preload = texts[0].split("KG context — kg_read:")[0]
    shown_by_preload = "  anchor-node:" in new_preload
    shown_by_read = "  anchor-node:" in ctx
    seen = "anchor-node" in w.sm.get_seen(s)
    print(f"X3 first preload showed anchor-node: {preload_had}; after the checkpoint the new "
          f"preload shows it: {shown_by_preload}; the replayed read shows its gist: "
          f"{shown_by_read} (anchor only: {'anchor-node (preloaded)' in ctx}); "
          f"seen after delivery: {seen}")
    ok = seen == (shown_by_preload or shown_by_read)
    print("   " + ("ok" if ok else "BUG: anchor-node counts as seen; no part of the new context shows its gist"))
    RESULTS["X3"] = ok and preload_had and not shown_by_preload
    w.close()


async def x4():
    w = World()
    w.put("bulky", "bulky node", w.writer, notes=["n" * 300000])
    p = await w.hook("SessionStart"); assert await w.ack(p)
    s = w.sid()
    queued = 0
    for _ in range(3):
        reply = await w.call("kg_read", {"session_id": s, "id": "bulky"})
        queued += "queued" in reply
    size = lambda: sum(len(i["text"].encode()) for i in w.sm.lookup(s)["agy_pending"])
    # One more reply that leaves less room than a preload takes.
    w.put("filler", "filler node", w.writer, notes=["f" * (delivery.QUEUE_BYTES - size() - 600)])
    queued += "queued" in await w.call("kg_read", {"session_id": s, "id": "filler"})
    size = size()
    w.row({"type": "CHECKPOINT", "step_index": 9, "content": "{{ CHECKPOINT 0 }} summary"})
    first = await w.hook()
    head = World.text(first)
    kind = head.split(":")[0]
    print(f"X4 {queued} replies queued ({size} of {delivery.QUEUE_BYTES} bytes); after the "
          f"checkpoint the first packet is {kind!r}")
    ok = head.startswith("KG context — preload:")
    print("   " + ("ok" if ok else "BUG: the fresh preload was refused by the full queue; "
                   "replayed replies reach the new context first"))
    RESULTS["X4"] = ok
    w.close()


async def main():
    await x2("queued")
    await x2("refused")
    await x3()
    await x4()

asyncio.run(main())
for k, v in RESULTS.items():
    print(f"RESULT {k}:", "PASS" if v else "FAIL")
sys.exit(0 if all(RESULTS.values()) else 1)
