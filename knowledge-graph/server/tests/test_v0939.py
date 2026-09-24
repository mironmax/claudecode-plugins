#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.39 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0939.py

ENDORSEMENT LOG. kg_useful credits two different things — a node that helped
from the surface, and a node the session needed and had to dig for — and both
landed in one undifferentiated _useful_ts list, so "is archival too
aggressive?" had no data behind it. The session now remembers the FIRST route
by which each gist reached it, and every endorsed id appends a line to
useful.jsonl carrying that route. The server records what it can observe (was
the node surfaced?), not what the agent meant — a gist that was on screen and
overlooked still logs as surfaced.

Covers:
  1. mark_seen keeps the first route per node; a later route never overwrites
  2. mark_promoted and rename_node_ref carry the new session fields
  3. An accepted like logs level, route, surfaced, promoted, archived
  4. Refusals are logged with a reason (duplicate, not_found, cap)
  5. A like on a never-shown node logs via null; one seen before routes were
     tracked logs "unknown" — a session spanning the upgrade is not a miss
  6. End to end through the MCP handler: search, id read of an archived node,
     and full read each stamp the route the log then reports
  7. A failing log write never breaks kg_useful
  8. The shared appender rotates to .prev and never raises
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-", dir=str(Path.home() / ".cache"))
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from mcp import types  # noqa: E402

import mcp_streamable_server as mss  # noqa: E402
from core.constants import MAX_LIKES_PER_SESSION, USEFUL_LOG_NAME  # noqa: E402
from core.persistence import append_jsonl  # noqa: E402
from mcp_http import store as store_mod  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402
from mcp_http.store import GraphConfig, MultiProjectGraphStore  # noqa: E402

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


LOG = Path(_TMP_STORAGE) / USEFUL_LOG_NAME


def read_log():
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]


def clear_log():
    for p in (LOG, LOG.with_name(LOG.name + ".prev")):
        if p.exists():
            p.unlink()


def fresh():
    sm = HTTPSessionManager()
    store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, broadcast_callback=None)
    project = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    sid = sm.register(project, claude_sid="cc-useful-1")["session_id"]
    return sm, store, sid


def test_session_routes():
    print("session routes:")
    sm, _store, sid = fresh()
    sm.mark_seen(sid, ["a", "b"], via="preload")
    sm.mark_seen(sid, ["b", "c"], via="search")
    via = sm.lookup(sid)["seen_via"]
    check("first route kept", via == {"a": "preload", "b": "preload", "c": "search"}, via)
    check("seen_ids still deduped", sm.lookup(sid)["seen_ids"] == ["a", "b", "c"])

    sm.mark_promoted(sid, ["c"])
    sm.mark_promoted(sid, ["c"])
    check("promoted deduped", sm.lookup(sid)["promoted_ids"] == ["c"])

    sm.rename_node_ref("c", "c2")
    s = sm.lookup(sid)
    check("rename carries route", s["seen_via"].get("c2") == "search" and "c" not in s["seen_via"], s["seen_via"])
    check("rename carries promotion", s["promoted_ids"] == ["c2"], s["promoted_ids"])


def test_log_records():
    print("log records:")
    clear_log()
    sm, store, sid = fresh()
    for i in range(MAX_LIKES_PER_SESSION + 2):
        store.put_node("user", f"u{i}", f"gist {i}", session_id=sid)
    store.graphs["user"]["nodes"]["u1"]["_archived"] = True
    sm.mark_seen(sid, ["u0"], via="ambient")
    sm.mark_seen(sid, ["u1"], via="search")

    store.mark_useful(["u0", "u1", "u2"], sid)
    recs = {r["id"]: r for r in read_log()}
    r0 = recs.get("u0", {})
    check("surfaced like logged", r0.get("via") == "ambient" and r0.get("surfaced") is True
          and r0.get("level") == "user" and "refused" not in r0, r0)
    check("record joins recall log", r0.get("kg_session") == sid
          and r0.get("claude_session") == "cc-useful-1", r0)
    r1 = recs.get("u1", {})
    check("dug-up archived like logged", r1.get("via") == "search" and r1.get("surfaced") is False
          and r1.get("archived") is True, r1)
    r2 = recs.get("u2", {})
    check("never-shown like has null route", r2.get("via") is None and r2.get("surfaced") is False, r2)
    check("session_likes counts in order",
          [r["session_likes"] for r in read_log()] == [1, 2, 3], read_log())

    clear_log()
    store.mark_useful(["u0", "ghost"], sid)
    refused = {r["id"]: r.get("refused") for r in read_log()}
    check("refusals logged with reason", refused == {"u0": "duplicate", "ghost": "not_found"}, refused)

    clear_log()
    store.mark_useful([f"u{i}" for i in range(3, MAX_LIKES_PER_SESSION + 2)], sid)
    check("cap refusal logged", read_log()[-1].get("refused") == "cap", read_log()[-1:])


def test_sighting_before_tracking():
    print("sighting before route tracking:")
    clear_log()
    sm, store, sid = fresh()
    store.put_node("user", "old-sight", "gist", session_id=sid)
    sm.lookup(sid)["seen_ids"] = ["old-sight"]  # a session that spans the upgrade
    store.mark_useful(["old-sight"], sid)
    rec = read_log()[0]
    check("seen without a route logs unknown, not never-shown",
          rec.get("via") == "unknown" and rec.get("surfaced") is False, rec)


def test_handler_end_to_end():
    print("handler end to end:")
    clear_log()
    sm, store, sid = fresh()
    mss.store, mss.session_manager = store, sm
    store.put_node("project", "zebra-migration-notes", "how the zebra herd migrates", session_id=sid)
    store.put_node("project", "archived-lore", "old lore about a kettle", session_id=sid)
    store.put_node("project", "plain-active", "an ordinary active node", session_id=sid)
    graph_key = store._get_graph_key("project", sid)
    store.graphs[graph_key]["nodes"]["archived-lore"]["_archived"] = True

    handler = mss.create_mcp_server().request_handlers[types.CallToolRequest]

    def call(name, **arguments):
        req = types.CallToolRequest(method="tools/call",
                                    params=types.CallToolRequestParams(name=name, arguments=arguments))
        return asyncio.run(handler(req))

    call("kg_search", query="zebra", session_id=sid)
    call("kg_read", session_id=sid, ids=["archived-lore"])
    call("kg_read", session_id=sid)
    call("kg_useful", session_id=sid, ids=["zebra-migration-notes", "archived-lore", "plain-active"])

    recs = {r["id"]: r for r in read_log()}
    check("search route stamped", recs.get("zebra-migration-notes", {}).get("via") == "search", recs)
    lore = recs.get("archived-lore", {})
    check("id read stamps route and promotion", lore.get("via") == "read" and lore.get("promoted") is True
          and lore.get("archived") is False, lore)
    check("full read stamps surface route", recs.get("plain-active", {}).get("via") == "full_read"
          and recs["plain-active"]["surfaced"] is True, recs.get("plain-active"))


def test_log_failure_is_silent():
    print("log failure:")
    sm, store, sid = fresh()
    store.put_node("user", "f0", "gist", session_id=sid)
    original = store_mod.get_storage_root
    store_mod.get_storage_root = lambda: Path("/proc/version/nope")
    try:
        r = store.mark_useful(["f0"], sid)
        check("like still accepted when the log cannot be written", r["accepted"] == ["f0"], r)
    except Exception as e:
        check("like still accepted when the log cannot be written", False, repr(e))
    finally:
        store_mod.get_storage_root = original


def test_appender():
    print("appender:")
    path = Path(_TMP_STORAGE) / "rot.jsonl"
    for i in range(20):
        append_jsonl(path, {"i": i, "pad": "x" * 20}, 200)
    prev = path.with_name(path.name + ".prev")
    check("rolled to .prev", prev.exists())
    check("live file under ceiling", path.stat().st_size <= 200, path.stat().st_size)
    check("last record kept", json.loads(path.read_text().splitlines()[-1])["i"] == 19)
    try:
        append_jsonl(Path("/proc/version/nope/x.jsonl"), {"a": 1}, 100)
        check("unwritable path does not raise", True)
    except Exception as e:
        check("unwritable path does not raise", False, repr(e))


def main():
    print("=== v0.9.39 endorsement log tests ===")
    test_session_routes()
    test_log_records()
    test_sighting_before_tracking()
    test_handler_end_to_end()
    test_log_failure_is_silent()
    test_appender()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
