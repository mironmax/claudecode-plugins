"""Reproduce X1 against the real code: a paged kg_read credits a node as READ
on the part that shows its header, while its notes run on into the next part.

Real: the MCP application (srv.create_mcp_server) over the stateless HTTP
transport, paging.call_paged with Claude Code's real 45,000-unit part limit,
HTTPSessionManager and MultiProjectGraphStore.put_node with its F11 guard.

  1. Session S reads two nodes in one call; each has ~30 KB of notes, so the
     reply pages and node b's notes straddle parts 1 and 2.
  2. S gets part 1 only: a new read (or a compaction) drops part 2.
  3. S writes b with the notes it was shown plus one of its own.
The F11 guard refuses a write that replaces notes this session has not read.
Here S has not read b's later notes, yet put_node takes the write and they
are gone. Prints PASS once b counts as read only with the part that ends it.
"""
import asyncio, os, re, sys, tempfile
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-repro-paging-")
os.environ["KG_BUDGET_NOTICES"] = "0"
os.environ["KG_AUTOCOMMIT_INTERVAL"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
import logging; logging.disable(logging.WARNING)

import httpx2 as httpx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp_http import harness
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
import mcp_streamable_server as srv

FOOTER = re.compile(r"\n\n\[KG reply part \d+ of \d+ — it continues\..*\]$", re.S)
project = tempfile.mkdtemp(prefix=".kg-repro-paging-", dir=Path.home())
sm = HTTPSessionManager()
store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm)
srv.store, srv.session_manager = store, sm


async def kg_read(arguments):
    manager = StreamableHTTPSessionManager(app=srv.create_mcp_server(),
                                           json_response=True, stateless=True)
    async with manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.handle_request),
                base_url="http://localhost", headers={
                    "accept": "application/json, text/event-stream",
                    "user-agent": "claude-code/2.1.294"}) as client:
            r = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                             "params": {"name": "kg_read", "arguments": arguments}})
    return r.json()["result"]["content"][0]["text"]


async def main():
    writer = sm.register(project)["session_id"]
    notes = lambda tag: [f"{tag} case {i}: " + "what happened and why it mattered " * 8
                         for i in range(110)]
    for nid in ("deploy-rules", "rollback-rules"):
        store.put_node("project", nid, f"how {nid} work", notes=notes(nid), session_id=writer)
    stored = store.graphs[next(k for k in store.graphs if k.startswith("project:"))]["nodes"]

    s = sm.register(project)["session_id"]
    part1 = await kg_read({"session_id": s, "ids": ["deploy-rules", "rollback-rules"]})
    paged = bool(FOOTER.search(part1))
    shown = [n for n in stored["rollback-rules"]["notes"] if f"    - {n}\n" in part1 + "\n"]
    print(f"part 1: {harness.utf16_units(part1)} units, paged={paged}; it shows the "
          f"rollback-rules header and {len(shown)} of its "
          f"{len(stored['rollback-rules']['notes'])} notes")
    read = sm.viewed_at(s, "rollback-rules", full=True)
    print(f"after part 1: rollback-rules counts as read in full: {read is not None}")
    await kg_read({"session_id": s, "id": "deploy-rules"})   # a new read drops part 2
    print("a new read drops part 2:",
          "Nothing more" in await kg_read({"session_id": s, "more": True}))
    try:
        store.put_node("project", "rollback-rules", "how rollback-rules work",
                       notes=shown + ["S's new case"], session_id=s)
        outcome = "written"
    except Exception as e:
        outcome = f"refused ({type(e).__name__})"
    left = len(stored["rollback-rules"]["notes"])
    print(f"S writes rollback-rules with the {len(shown)} notes it saw + 1: {outcome}; "
          f"stored notes now {left}")
    ok = outcome.startswith("refused")
    print("   " + ("ok: a write over notes S never received is refused" if ok else
                   f"BUG: {110 - len(shown)} notes S never received are gone"))
    store.shutdown()
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok

ok = asyncio.run(main())
import shutil; shutil.rmtree(project, ignore_errors=True)
sys.exit(0 if ok else 1)
