#!/usr/bin/env python3
"""Regression tests for the second formal pass over maintenance dispatch and
budget notices (formal/chore-dispatch/lean/Tiers.lean, formal/budget-notices).

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_dispatch_tiers.py

Each case is the deterministic reproduction from formal/<concern>/repro,
reduced to an assertion that holds on the fixed code and fails on the old.

Covers:
  1. a timed-out Antigravity run takes agy down with it, not just the wrapper
  2. under "auto", a configured *_bin pins its runner even when another
     harness is installed
  3. a state file that cannot be written refuses the dispatch
  4. a runner command that cannot be built clears _running
  5. a Claude reading is as old as its oldest window value (*_seen_at)
  6. the lock re-decides on the config and clock of now: switched off,
     runner changed, a decision the machine slept through, graph cooldown
  7. a cached Antigravity budget reading of a window that has reset is empty
"""

import json
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-test-tiers-")
os.environ.pop("KG_CHORES", None)
os.environ.pop("KG_BUDGET_NOTICES", None)

from core.chores import Chore  # noqa: E402
from core.constants import CHORE_LOG_NAME  # noqa: E402
from mcp_http import budget, chore_dispatch as cd, harness  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402

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


def fresh_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="kg-test-tiers-"))
    os.environ["KG_STORAGE_ROOT"] = str(root)
    cd._config_cache.update(mtime=None, data={})
    cd._running.clear()
    return root


def write_cfg(root: Path, data: dict, stamp: float) -> None:
    path = root / "chores.json"
    path.write_text(json.dumps(data))
    os.utime(path, (stamp, stamp))


@contextmanager
def stubbed(**attrs):
    saved = {k: getattr(cd, k) for k in attrs}
    for k, v in attrs.items():
        setattr(cd, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(cd, k, v)
        cd.RUNNERS["claude"].__dict__.pop("binary", None)


def log(root: Path) -> list:
    path = root / CHORE_LOG_NAME
    return [json.loads(line) for line in open(path)] if path.exists() else []


def wait_idle(limit=30):
    deadline = time.time() + limit
    while cd._running.is_set() and time.time() < deadline:
        time.sleep(0.02)


class Store:
    lock = threading.Lock()
    graphs: dict = {}
    _progress: dict = {}

    def maintain_lessons(self):
        return []


CHORE = Chore(kind="edge", targets=["n1"], reason="unconnected", debt=1.0)
GATES = dict(_gauge_read=lambda cfg, now, runner: ({"age_s": 0}, {}, ""),
             _pick_target=lambda *a: ("chore", CHORE),
             _chore_gauge_ok=lambda cfg, raw: "")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, sys, time
if sys.argv[1:3] == ["-p", "/agents"]:
    print(json.dumps({"command": {"data": {"agents": ["kg-maintainer"]}}}))
    sys.exit(0)
sys.stdin.readline()
open(os.environ["AGY_PIDS"], "a").write(f"{os.getpid()}\n")
time.sleep(30)
'''


def test_agy_timeout():
    print("1. a timed-out Antigravity run stops agy too")
    root = fresh_root()
    fake = root / "agy"
    fake.write_text(FAKE_AGY)
    fake.chmod(0o755)
    pids = root / "agy.pids"
    os.environ["AGY_PIDS"] = str(pids)
    job = {"tier": "chore", "runner": "antigravity", "bin": str(fake),
           "settings": str(cd.SHIPPED_SETTINGS), "timeout_s": 2, "graph": "user",
           "level": "user", "kind": "edge", "targets": ["n1"], "debt_before": 1.0}
    cd._running.set()
    cd._spawn({}, job, "TIDY", Store())
    wait_idle()
    time.sleep(0.3)
    started = [int(x) for x in pids.read_text().split()] if pids.exists() else []
    survivors = [p for p in started if alive(p)]
    for p in survivors:
        os.kill(p, 9)
    check("the run started agy", len(started) == 1, started)
    check("no agy process outlives the timeout", not survivors, survivors)
    rec = [r for r in log(root) if r["event"] == "done"]
    check("the run is logged as a timeout", rec and rec[0]["rc"] == -9, rec)


def test_runner_pins():
    print("2. a configured binary pins its runner under auto")
    fresh_root()
    with stubbed():
        cd.RUNNERS["claude"].binary = lambda cfg: cfg.get("claude_bin") or "/usr/bin/claude"
        check("a correct codex_bin pins Codex over an installed Claude Code",
              cd._runner({"codex_bin": "/usr/bin/codex"}).name == "codex")
        check("a mistyped codex_bin pins Codex too (and then refuses as missing)",
              cd._runner({"codex_bin": "/nonexistent/codx"}).name == "codex")
        check("an antigravity_bin pins Antigravity",
              cd._runner({"antigravity_bin": "/nonexistent/agy"}).name == "antigravity")
        check("no binary configured: Claude Code first, as before",
              cd._runner({}).name == "claude")


def test_state_write_failure():
    print("3. a state file that cannot be written refuses the dispatch")
    root = fresh_root()
    write_cfg(root, {"enabled": True}, 1e9)
    (root / "chore_state.tmp").mkdir()          # every _write_state now raises
    with stubbed(**GATES):
        cd.RUNNERS["claude"].binary = lambda cfg: "/bin/true"
        for _ in range(3):
            cd.maybe_dispatch(Store(), None, None)
            wait_idle()
    events = [r["event"] for r in log(root)]
    check("no dispatch without a record of it", "dispatch" not in events, events)
    check("the refusal says why",
          any(r.get("reason") == "state write failed" for r in log(root)), log(root))


def test_command_failure():
    print("4. a runner command that cannot be built clears _running")
    root = fresh_root()
    fake = root / "codex"
    fake.write_text("#!/bin/sh\ncat >/dev/null\n")
    fake.chmod(0o755)
    settings = root / "my-settings.json"
    settings.write_text('{"permissions": {"allow": [,]}}')
    write_cfg(root, {"enabled": True, "runner": "codex", "codex_bin": str(fake),
                     "settings": str(settings)}, 1e9)
    with stubbed(**GATES):
        cd.maybe_dispatch(Store(), None, None)
    time.sleep(0.2)
    check("_running is clear", not cd._running.is_set())
    check("the failure is logged",
          any(r["event"] == "spawn_failed" for r in log(root)), log(root))


def test_carried_window_is_stale():
    print("5. a carried five-hour value is as old as its own stamp")
    root = fresh_root()
    now = 1_800_000_000
    limits = root / "last-limits.json"
    limits.write_text(json.dumps({
        "five_hour_pct": 10, "five_hour_resets_at": now + 3600, "five_hour_seen_at": now - 3 * 3600,
        "seven_day_pct": 20, "seven_day_resets_at": now + 4 * 86400, "seven_day_seen_at": now,
        "updated_at": now}))
    reading, raw, err = cd._gauge_read({"limits": str(limits)}, now, cd.ClaudeRunner())
    check("a three-hour-old five-hour value is stale", err == "gauge stale", (reading, err))
    data = json.loads(limits.read_text())
    data["five_hour_seen_at"] = now - 600
    limits.write_text(json.dumps(data))
    _r, raw, err = cd._gauge_read({"limits": str(limits)}, now, cd.ClaudeRunner())
    check("both windows fresh: readable", err == "" and raw is not None, err)
    del data["five_hour_seen_at"], data["seven_day_seen_at"]
    limits.write_text(json.dumps(data))
    _r, raw, err = cd._gauge_read({"limits": str(limits)}, now, cd.ClaudeRunner())
    check("a file without per-window stamps falls back to updated_at", err == "", err)


def test_lock_redecides():
    print("6. the lock re-decides on the config and clock of now")
    for label, edit in (("switched off", {"enabled": False}),
                        ("runner changed", {"enabled": True, "runner": "codex",
                                            "codex_bin": "/bin/true"})):
        root = fresh_root()
        write_cfg(root, {"enabled": True}, 1e9)

        def pick(*a, edit=edit, root=root):
            write_cfg(root, edit, 1e9 + 1)      # the user saves chores.json mid-decision
            return "chore", CHORE
        with stubbed(**{**GATES, "_pick_target": pick}):
            cd.RUNNERS["claude"].binary = lambda cfg: "/bin/true"
            cd.maybe_dispatch(Store(), None, None)
            wait_idle()
        events = [r["event"] for r in log(root)]
        check(f"{label} during the decision: no dispatch", "dispatch" not in events, events)

    root = fresh_root()
    write_cfg(root, {"enabled": True}, 1e9)
    clock = [1_800_000_000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0], strftime=time.strftime,
                                localtime=time.localtime, sleep=time.sleep)
    paused, go = {}, {}

    class Paused(Store):
        def maintain_lessons(self):
            name = threading.current_thread().name
            paused[name].set()
            go[name].wait(10)
            return []

    def pick(store, sm, state, cfg, now, candidates):
        if now - (state.get("graphs") or {}).get("user", 0) < cd.CHORE_GRAPH_COOLDOWN_SECONDS:
            return None, "cooldown"
        return "chore", CHORE
    with stubbed(**{**GATES, "_pick_target": pick, "time": fake_time}):
        cd.RUNNERS["claude"].binary = lambda cfg: "/bin/true"
        threads = {}
        for name in ("T0", "T1"):
            paused[name], go[name] = threading.Event(), threading.Event()
            threads[name] = threading.Thread(target=cd.maybe_dispatch,
                                             args=(Paused(), None, None), name=name)
        threads["T0"].start(); paused["T0"].wait(10)
        clock[0] += 2 * 3600                      # the machine slept mid-decision
        threads["T1"].start(); paused["T1"].wait(10)
        go["T0"].set(); threads["T0"].join(); wait_idle()
        go["T1"].set(); threads["T1"].join(); wait_idle()
    reasons = [r.get("reason") for r in log(root)]
    dispatches = [r for r in log(root) if r["event"] == "dispatch"]
    check("a decision older than the gauge's max age stands down",
          "gauge went stale" in reasons, reasons)
    check("one chore on the graph, not two inside its cooldown", len(dispatches) == 1, reasons)


def test_agy_budget_after_reset():
    print("7. a cached Antigravity budget reading of a reset window is empty")
    fresh_root()
    reset = 1_800_000_000.0                      # 2027-01-15T08:00:00Z
    usage = {"command": {"data": {"groups": [{"name": "Gemini Models", "buckets": [
        {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.04,
         "reset_time": "2027-01-15T08:00:00Z"}]}]}}}
    reading = cd.antigravity_limits(usage, None, reset - 60)
    saved = dict(budget._agy)
    budget._agy.update(at=reset - 60, data=reading, refreshing=False)
    try:
        sm = HTTPSessionManager()
        sid = sm.register(None)["session_id"]
        before = budget.notice(sm, sid, harness.ANTIGRAVITY, None, now=reset - 30)
        sid2 = sm.register(None)["session_id"]
        after = budget.notice(sm, sid2, harness.ANTIGRAVITY, None, now=reset + 30)
    finally:
        budget._agy.clear()
        budget._agy.update(saved)
    check("before the reset the cached 96% is said", "weekly quota at 96%" in (before or ""), before)
    check("after the reset it is not", after is None, after)
    check("the cache itself is left as it was", reading["seven_day_pct"] == 96.0, reading)


def main():
    test_agy_timeout()
    test_runner_pins()
    test_state_write_failure()
    test_command_failure()
    test_carried_window_is_stale()
    test_lock_redecides()
    test_agy_budget_after_reset()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
