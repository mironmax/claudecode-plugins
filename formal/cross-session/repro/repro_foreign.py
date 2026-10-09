"""Reproduce the counterexamples of cross-session/lean/Foreign.lean against the
real push of other sessions' writes and kg_sync.

Real: the MCP application (srv.create_mcp_server) for kg_search, kg_put_node,
kg_read, kg_rename_node and kg_sync, with and without Antigravity's
conversation metadata; the REST hook routes (rest.create_rest_api), served by
uvicorn on a free port and called with the hook's own `curl --max-time 1`;
foreign.notice, HTTPSessionManager, MultiProjectGraphStore and Antigravity's
delivery queue. Stubbed: compaction's decision to archive a node (the flag is
set directly), and the slow moment in F38 (another thread holds store.lock
for 1.5 s, as the saver thread does while it saves or compacts).

  F35a A maintenance pass renames a node written before S started; S's next
       reply announces it as a new write by another session.
  F35b kg_sync lists F's write; F re-sends the node unchanged; the next reply
       pushes the same write again.
  F35c A session in another project reads an archived user node, promoting it;
       S's next reply announces the old node as new.
  F36  F changes a node S had read; S renames it. Neither kg_sync nor any push
       ever reports F's change.
  F37  Antigravity: a queued reply and an inline reply rendered before the
       queued one is delivered both carry the same change.
  F38  A hook reply the client gave up on (curl --max-time 1) still claims the
       push window and marks the node seen: the change is never pushed, and a
       gist-only write built on the older view goes through.

Each case prints BUG while its finding is unfixed and ok once fixed; the last
line is RESULT: FAIL <cases> or all hold.
"""
import asyncio, os, socket, subprocess, sys, tempfile, threading, time, json
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-repro-xs-")
os.environ["KG_BUDGET_NOTICES"] = "0"
os.environ["KG_AUTOCOMMIT_INTERVAL"] = "0"
os.environ["KG_CHORES"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
import logging; logging.disable(logging.WARNING)

import atexit, shutil
import httpx2 as httpx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
import mcp_streamable_server as srv

RESULTS = {}
TICK = 0.02   # between steps, so every timestamp is distinct


def tick():
    time.sleep(TICK)


class World:
    def __init__(self):
        os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-repro-xs-")
        atexit.register(shutil.rmtree, os.environ["KG_STORAGE_ROOT"], True)
        self.root = Path(tempfile.mkdtemp(prefix=".kg-repro-xs-p-", dir=Path.home()))
        self.other = Path(tempfile.mkdtemp(prefix=".kg-repro-xs-q-", dir=Path.home()))
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        srv.store, srv.session_manager = self.store, self.sm
        self.app = create_rest_api(self.store, self.sm, None, "repro")
        self.f = self.sm.register(str(self.root))["session_id"]       # F: project P
        self.g = self.sm.register(str(self.other))["session_id"]      # G: project Q
        self.transcript = self.root / ".gemini/antigravity-cli/transcript_full.jsonl"
        self.transcript.parent.mkdir(parents=True)
        with self.transcript.open("a") as fh:
            fh.write(json.dumps({"type": "USER_INPUT", "step_index": 1,
                                 "content": "<USER_REQUEST>hi</USER_REQUEST>"}) + "\n")

    async def call(self, name, arguments, cid=None):
        params = {"name": name, "arguments": arguments}
        if cid:
            params["_meta"] = {"antigravity.google/conversation_id": cid}
        manager = StreamableHTTPSessionManager(app=srv.create_mcp_server(),
                                               json_response=True, stateless=True)
        async with manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.handle_request),
                    base_url="http://localhost", headers={
                        "accept": "application/json, text/event-stream",
                        "user-agent": "Go-http-client/1.1" if cid else "claude-code/2.0"}) as c:
                r = await c.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": params})
        return r.json()["result"]["content"][0]["text"]

    async def rest(self, path, payload):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                     base_url="http://localhost") as c:
            return (await c.post(path, json=payload)).json()

    async def agy_hook(self, event="PreInvocation", cid="conv-a"):
        return await self.rest("/api/antigravity/hook/" + event, {
            "conversationId": cid, "workspacePaths": [str(self.root)],
            "transcriptPath": str(self.transcript), "invocationNum": 1})

    async def agy_drain(self, cid="conv-a"):
        texts = []
        for _ in range(60):
            packet = await self.agy_hook(cid=cid)
            if not packet["output"]:
                return texts
            texts.append(packet["output"]["injectSteps"][0]["systemMessage"]["systemMessage"])
            ok = await self.rest("/api/antigravity/ack", {"conversationId": cid,
                                                         "delivery_id": packet["delivery_id"]})
            assert ok["ok"]
        raise RuntimeError("did not drain")

    def put(self, nid, gist, sid, level="project", notes=None):
        return self.store.put_node(level, nid, gist, notes=notes, session_id=sid)

    def graph(self, level="project"):
        key = "user" if level == "user" else next(k for k in self.store.graphs
                                                   if k.startswith("project:"))
        return self.store.graphs[key]

    def session(self, cid=None):
        """S: a fresh session in project P, no preload, nothing seen yet."""
        return self.sm.register(str(self.root), claude_sid=cid)["session_id"]

    def close(self):
        self.store.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.other, ignore_errors=True)


def pushed(reply, nid):
    """The notice's line for nid, or None."""
    if "other sessions wrote" not in reply:
        return None
    for line in reply.split("other sessions wrote", 1)[1].splitlines():
        if line.startswith(f"- {nid} ("):
            return line
    return None


async def fx1a():
    w = World()
    w.put("unrelated-topic", "unrelated topic used as a search target", w.f)
    w.put("deploy-steps", "deploy steps: build, migrate, roll out", w.f)
    tick()
    s = w.session()
    tick()
    m = w.sm.register(str(w.root))["session_id"]
    w.store.mark_maintenance(m)
    await w.call("kg_rename_node", {"session_id": m, "old_id": "deploy-steps",
                                    "new_id": "deploy-runbook"})
    tick()
    reply = await w.call("kg_search", {"session_id": s, "query": "unrelated topic"})
    line = pushed(reply, "deploy-runbook")
    print(f"F35a deploy-steps written before S started; a maintenance pass renamed it; "
          f"S's next reply: {line or 'no notice'}")
    ok = line is None
    print("   " + ("ok" if ok else "BUG: an old node, only renamed by a maintenance pass, "
                                  "is announced as a new write by another session"))
    RESULTS["F35a"] = ok
    w.close()


async def fx1b():
    w = World()
    w.put("unrelated-topic", "unrelated topic used as a search target", w.f)
    tick()
    s = w.session()
    tick()
    w.put("cache-policy", "cache headers per route: v1", w.f)
    tick()
    sync = await w.call("kg_sync", {"session_id": s})
    listed = "node cache-policy" in sync
    tick()
    put = await w.call("kg_put_node", {"session_id": w.f, "level": "project", "id": "cache-policy",
                                       "gist": "cache headers per route: v1"})
    assert "saved" in put, put
    tick()
    reply = await w.call("kg_search", {"session_id": s, "query": "unrelated topic"})
    line = pushed(reply, "cache-policy")
    print(f"F35b kg_sync listed cache-policy: {listed}; F re-sent it unchanged; "
          f"S's next reply: {line or 'no notice'}")
    ok = listed and line is None
    print("   " + ("ok" if ok else "BUG: the write kg_sync already listed is pushed again"))
    RESULTS["F35b"] = ok
    w.close()


async def fx1c():
    w = World()
    w.put("unrelated-topic", "unrelated topic used as a search target", w.f)
    w.put("shell-quoting", "quote every variable in shell scripts", w.f, level="user")
    w.graph("user")["nodes"]["shell-quoting"]["_archived"] = True     # compaction's decision
    tick()
    s = w.session()
    tick()
    read = await w.call("kg_read", {"session_id": w.g, "id": "shell-quoting"})
    assert "shell-quoting" in read, read
    tick()
    reply = await w.call("kg_search", {"session_id": s, "query": "unrelated topic"})
    line = pushed(reply, "shell-quoting")
    print(f"F35c shell-quoting (user level, written by F before S started, archived) read by "
          f"a session in another project; S's next reply: {line or 'no notice'}")
    ok = line is None
    print("   " + ("ok" if ok else "BUG: a promotion is announced as a new write by another session"))
    RESULTS["F35c"] = ok
    w.close()


async def fx2():
    w = World()
    w.put("unrelated-topic", "unrelated topic used as a search target", w.f)
    w.put("cache-policy", "cache headers per route: v1", w.f)
    tick()
    s = w.session()
    await w.call("kg_read", {"session_id": s, "id": "cache-policy"})
    tick()
    w.put("cache-policy", "cache headers per route: v2, F moved them to the edge", w.f)
    tick()
    ren = await w.call("kg_rename_node", {"session_id": s, "old_id": "cache-policy",
                                          "new_id": "cache-rules"})
    assert "Renamed" in ren, ren
    tick()
    reply = await w.call("kg_search", {"session_id": s, "query": "unrelated topic"})
    sync = await w.call("kg_sync", {"session_id": s})
    seen = "cache-rules" in sync or pushed(reply, "cache-rules") is not None
    print(f"F36 S read cache-policy v1; F wrote v2; S renamed it to cache-rules. "
          f"Push: {pushed(reply, 'cache-rules') or 'none'}; kg_sync: {sync.splitlines()[0]!r}")
    ok = seen
    print("   " + ("ok" if ok else "BUG: F's change is never reported: S's own rename hides it "
                                  "from kg_sync and from the push"))
    RESULTS["F36"] = ok
    w.close()


async def fx3():
    w = World()
    for i in range(14):   # enough matches that the search reply exceeds 3,500 bytes
        w.put(f"key-rotation-{i}", f"key rotation step {i}: " + "rotate signing keys per tenant " * 7,
              w.f)
    p = await w.agy_hook("SessionStart")
    assert (await w.rest("/api/antigravity/ack", {"conversationId": "conv-a",
                                                  "delivery_id": p["delivery_id"]}))["ok"]
    s = w.sm.find_by_claude_sid("conv-a")[0]
    tick()
    w.put("cache-policy", "cache headers per route: v2, F moved them to the edge", w.f)
    tick()
    big = await w.call("kg_search", {"session_id": s, "query": "key rotation"}, cid="conv-a")
    assert "queued" in big, big[:200]
    small = await w.call("kg_put_node", {"session_id": s, "level": "project", "id": "s-note",
                                         "gist": "S's own lesson"}, cid="conv-a")
    texts = [small] + await w.agy_drain()
    copies = sum(pushed(t, "cache-policy") is not None for t in texts)
    print(f"F37 Antigravity: a kg_search reply is queued, a kg_put_node reply goes inline, then "
          f"the hook delivers the queued one; replies carrying cache-policy's notice: {copies}")
    ok = copies == 1
    print("   " + ("ok" if ok else "BUG: one change is pushed twice"))
    RESULTS["F37"] = ok
    w.close()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def fx4():
    import uvicorn
    w = World()
    w.put("cache-policy", "cache headers per route: v1", w.f)
    tick()
    s = w.session(cid="cc-conv")
    await w.call("kg_read", {"session_id": s, "id": "cache-policy"})
    tick()
    w.put("cache-policy", "cache headers per route: v2, F moved them to the edge", w.f)
    written = w.graph()["nodes"]["cache-policy"]["_written"]["ts"]
    pushed_before = w.sm.lookup(s).get("pushed_ts", 0)
    tick()

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(w.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)

    def hold_lock():   # the saver thread mid-save or mid-compaction
        with w.store.lock:
            time.sleep(1.5)
    holder = threading.Thread(target=hold_lock)
    holder.start()
    time.sleep(0.05)
    payload = json.dumps({"session_id": "cc-conv", "cwd": str(w.root),
                          "hook_event_name": "PostToolUse", "tool_name": "Bash",
                          "tool_input": {"command": "ls"}, "tool_response": {}})
    hook = subprocess.run(["curl", "-sf", "--max-time", "1", "-X", "POST",
                           "-H", "Content-Type: application/json", "--data-binary", "@-",
                           f"http://127.0.0.1:{port}/api/tool_event"],
                          input=payload, capture_output=True, text=True)
    holder.join()
    time.sleep(0.3)    # the handler finishes once the lock is free
    server.should_exit = True
    thread.join()

    data = w.sm.lookup(s)
    claimed = data.get("pushed_ts", 0) > pushed_before
    seen = w.sm.viewed_at(s, "cache-policy") or 0
    reply = await w.call("kg_put_node", {"session_id": s, "level": "project", "id": "cache-policy",
                                         "gist": "cache headers per route: v1, plus S's change"})
    accepted = "saved" in reply
    print(f"F38 the hook's curl gave up (exit {hook.returncode}, printed {len(hook.stdout)} bytes); "
          f"push window claimed: {claimed}; cache-policy seen_at >= F's write: {seen >= written}; "
          f"S's write on its v1 view: {'written' if accepted else 'refused'}")
    ok = not (seen >= written) and not accepted
    print("   " + ("ok" if ok else "BUG: the lost hook reply used up the push and marked the change "
                                  "seen; the older view overwrites F's gist"))
    RESULTS["F38"] = ok
    w.close()


async def main():
    only = sys.argv[1:]
    for name, case in (("F35a", fx1a), ("F35b", fx1b), ("F35c", fx1c), ("F36", fx2),
                       ("F37", fx3), ("F38", fx4)):
        if not only or name in only:
            await case()
    failed = [k for k, v in RESULTS.items() if not v]
    print("RESULT: " + ("FAIL " + ", ".join(failed) if failed else "all hold"))


asyncio.run(main())
