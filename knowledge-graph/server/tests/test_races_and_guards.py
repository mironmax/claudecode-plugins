#!/usr/bin/env python3
"""Regression tests for the formal pass's first fixes (formal/FINDINGS.md).

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_races_and_guards.py

Each case is the deterministic reproduction from formal/<concern>/repro,
reduced to an assertion that holds on the fixed code and fails on the old.

Covers:
  1. F2  a failed write-through keeps the graph dirty; shutdown flushes it
  2. F3  a disk-wins reload of a dirty graph is logged, not silent
  3. F4  the saver thread survives a failing tick; session iteration is
         safe against concurrent registration
  4. F1  a dispatch that loses the race to a concurrent one does not run,
         and the stored count matches the dispatches
  5. F10 the pass prompt carries the ids a live session holds; an unknown
         live context refuses to dispatch rather than clearing every id
  6. F9  cross-site HTTP (Sec-Fetch-Site or Origin) is refused; local and
         non-browser requests pass
  7. kg_sync returns another session's write even when the syncing session
     wrote something of its own after it, and never returns its own writes
"""

import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE
os.environ.pop("KG_CHORES", None)

from core.chores import build_pass_prompt  # noqa: E402
from core.constants import CHORE_LOG_NAME, PASS_MIN_ACTIVE_NODES  # noqa: E402
from core.persistence import GraphPersistence  # noqa: E402
from mcp_http import chore_dispatch as cd  # noqa: E402
from mcp_http.security import request_refusal  # noqa: E402
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


def fresh_store(save_interval=9999):
    sm = HTTPSessionManager()
    return MultiProjectGraphStore(GraphConfig(save_interval=save_interval), sm), sm


def on_disk(store, nid):
    path = store._persistence["user"].path
    return path.exists() and nid in json.loads(path.read_text())["nodes"]


class Captured(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_failed_write_through():
    print("1. F2 failed write-through")
    store, sm = fresh_store()
    sid = sm.register(None)["session_id"]
    real_save = GraphPersistence.save
    GraphPersistence.save = lambda self, *a, **k: False
    try:
        store.put_node("user", "survivor", "written while the disk refused", session_id=sid)
    finally:
        GraphPersistence.save = real_save
    check("a failed save leaves the graph dirty", store.dirty.get("user") is True)
    check("the node is not on disk yet", not on_disk(store, "survivor"))
    store.put_node("user", "after", "a later good write", session_id=sid)
    check("the next successful write-through clears dirty", store.dirty.get("user") is False)
    check("and carries the earlier node to disk", on_disk(store, "survivor"))

    GraphPersistence.save = lambda self, *a, **k: False
    try:
        store.put_node("user", "at-shutdown", "only shutdown can save this", session_id=sid)
    finally:
        GraphPersistence.save = real_save
    store.shutdown()
    check("shutdown flushes a graph whose write-through failed", on_disk(store, "at-shutdown"))


def test_reload_discard_is_logged():
    print("2. F3 disk-wins reload")
    store, sm = fresh_store()
    sid = sm.register(None)["session_id"]
    store.put_node("user", "stamped", "a gist", session_id=sid)
    store.dirty["user"] = True          # what a read stamp or a failed save leaves
    cap = Captured()
    logging.getLogger("mcp_http.store").addHandler(cap)
    try:
        store.read_graphs(session_id=sid, force_reload=True)
        store.read_graphs(session_id=sid, force_reload=True)
    finally:
        logging.getLogger("mcp_http.store").removeHandler(cap)
    hits = [m for m in cap.lines if "discards unsaved" in m]
    check("reloading a dirty graph logs what it drops", len(hits) == 1, cap.lines)
    check("a clean reload logs nothing", store.dirty.get("user") is False)
    store.shutdown()


def test_saver_survives():
    print("3. F4 saver thread and session iteration")
    store, sm = fresh_store(save_interval=0.02)
    calls = {"n": 0}
    real = sm.cleanup_expired

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dictionary changed size during iteration")
        return real()
    sm.cleanup_expired = flaky
    deadline = time.time() + 5
    while calls["n"] < 3 and time.time() < deadline:
        time.sleep(0.02)
    check("the saver ticks again after a failing tick",
          calls["n"] >= 3 and store.saver_thread.is_alive(), calls)
    store.shutdown()

    sm = HTTPSessionManager()
    sm.save_sessions = lambda: None
    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                sm.recently_seen_ids(3600)
                sm.cleanup_expired()
            except Exception as e:
                errors.append(repr(e))
                return
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    t = threading.Thread(target=reader)
    t.start()
    try:
        for _ in range(5000):
            sm.register(None)
    finally:
        stop.set()
        t.join()
        sys.setswitchinterval(old_interval)
    check("iterating sessions while others register never raises", not errors, errors[:1])


def test_dispatch_race():
    print("4. F1 dispatch decides on fresh state")
    saved = {k: getattr(cd, k) for k in ("_gauge_read", "_pick_target", "_chore_gauge_ok",
                                         "_settings_path", "build_chore_prompt",
                                         "_runner", "_enabled")}
    payload = SimpleNamespace(level="user", graph="user", project_path=None, kind="edge",
                              targets=["n1"], debt=1.0, pool="p", context={})
    cd._enabled = lambda cfg: True
    cd._gauge_read = lambda cfg, now, runner: ({}, {}, "")
    cd._pick_target = lambda *a: ("chore", payload)
    cd._chore_gauge_ok = lambda cfg, raw: ""
    cd._settings_path = lambda cfg, tier="chore": "/dev/null"
    cd.build_chore_prompt = lambda *a, **k: "p"
    runner = cd.ClaudeRunner()
    runner.binary = lambda cfg: "/nonexistent/claude"       # spawn fails: _running clears at once
    cd._runner = lambda cfg: runner

    t1_read, release = threading.Event(), threading.Event()

    class Store:
        def maintain_lessons(self):
            if threading.current_thread().name == "T1":
                t1_read.set()
                release.wait(10)
            return []
    try:
        t1 = threading.Thread(target=cd.maybe_dispatch, args=(Store(), None, None), name="T1")
        t1.start()
        t1_read.wait(10)
        t0 = threading.Thread(target=cd.maybe_dispatch, args=(Store(), None, None), name="T0")
        t0.start()
        t0.join()
        release.set()
        t1.join()
    finally:
        for k, v in saved.items():
            setattr(cd, k, v)
    log = [json.loads(line) for line in open(Path(_TMP_STORAGE) / CHORE_LOG_NAME)]
    dispatches = [r for r in log if r.get("event") == "dispatch"]
    state = json.loads((Path(_TMP_STORAGE) / "chore_state.json").read_text())
    check("only one of two racing prompts dispatches", len(dispatches) == 1,
          [r.get("event") for r in log])
    check("the loser logs why it stood down",
          any(r.get("reason") == "a concurrent dispatch won" for r in log))
    check("the stored count matches the dispatches", state["count"] == len(dispatches), state)


def test_pass_live_context():
    print("5. F10 the pass tier respects live context")
    store, sm = fresh_store()
    sid = sm.register(None)["session_id"]
    for i in range(PASS_MIN_ACTIVE_NODES + 2):
        store.put_node("user", f"pass-node-{i}", f"gist {i}", session_id=sid)
    sm.mark_seen(sid, ["pass-node-0", "pass-node-3", "not-in-this-graph"], via="read")
    cfg = {"debt_floor": 0}
    tier, payload = cd._pick_target(store, sm, {}, cfg, time.time(), [("user", "user", None)])
    check("an unmaintained graph is due a pass", tier == "pass", (tier, payload))
    check("the pass payload names the held ids of that graph only",
          tier == "pass" and payload["held"] == ["pass-node-0", "pass-node-3"],
          payload.get("held") if tier == "pass" else payload)
    prompt = build_pass_prompt("user", "/home/x", {"score": 1}, held=payload.get("held", ()))
    check("the prompt lists them under LIVE CONTEXT",
          "LIVE CONTEXT" in prompt and "pass-node-0, pass-node-3" in prompt)
    check("rename and merge steps point at the list",
          prompt.count("listed under LIVE CONTEXT") == 2)
    check("no live context, no block", "LIVE CONTEXT" not in build_pass_prompt("user", "/h", {}))

    real = sm.recently_seen_ids
    sm.recently_seen_ids = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        tier, reason = cd._pick_target(store, sm, {}, cfg, time.time(), [("user", "user", None)])
    finally:
        sm.recently_seen_ids = real
    check("an unreadable live context refuses to dispatch",
          tier is None and reason == "live context unknown", (tier, reason))
    store.shutdown()


def test_cross_site_guard():
    print("6. F9 request guard")

    def scope(kind="http", **headers):
        return {"type": kind, "headers": [(k.replace("_", "-").encode(), v.encode())
                                          for k, v in {"host": "127.0.0.1:8765", **headers}.items()]}
    check("a non-browser client passes", request_refusal(scope()) is None)
    check("a local page passes",
          request_refusal(scope(origin="http://localhost:8766", sec_fetch_site="same-site")) is None)
    check("Sec-Fetch-Site: none (typed URL) passes",
          request_refusal(scope(sec_fetch_site="none")) is None)
    check("a cross-site <img> GET is refused (no Origin, Sec-Fetch-Site only)",
          request_refusal(scope(sec_fetch_site="cross-site")) == (403, "cross-site request (Sec-Fetch-Site)"))
    check("a foreign Origin is refused",
          (request_refusal(scope(origin="https://evil.example")) or (0,))[0] == 403)
    check("a foreign Host is still 421",
          (request_refusal(scope(host="evil.example:8765")) or (0,))[0] == 421)
    check("WebSocket Origin is left to the /ws route",
          request_refusal(scope("websocket", origin="https://evil.example")) is None)


def test_sync_keeps_others_writes():
    print("7. kg_sync after an own write")
    store, sm = fresh_store()
    a = sm.register(None)["session_id"]
    b = sm.register(None)["session_id"]
    time.sleep(0.01)
    store.put_node("user", "from-b", "written by the other session", session_id=b)
    time.sleep(0.01)
    store.put_node("user", "from-a", "written by the syncing session", session_id=a)
    store.put_edge("user", "from-a", "from-b", "relates-to", session_id=a)
    diff = store.get_sync_diff(a, sm.get_sync_ts(a))
    check("another session's earlier write is still returned", "from-b" in diff["user"]["nodes"],
          list(diff["user"]["nodes"]))
    check("the session's own writes are not", "from-a" not in diff["user"]["nodes"]
          and not diff["user"]["edges"], diff["user"])
    store.shutdown()


def main():
    test_failed_write_through()
    test_reload_discard_is_logged()
    test_saver_survives()
    test_dispatch_race()
    test_pass_live_context()
    test_cross_site_guard()
    test_sync_keeps_others_writes()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
