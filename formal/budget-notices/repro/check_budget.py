"""Checks of the budget notices against the real code: the properties that hold.

Real: budget.notice, reading_for, codex_limits, note_budget_level,
save_sessions/_load_sessions (sessions.json), fork, reset_context.
Stubbed: the gauge (a Codex rollout file written here) and the clock.
Prints one line per check; exits 1 if any check does not hold.
"""
import atexit, json, os, shutil, sys, tempfile, threading
from pathlib import Path
from unittest.mock import patch

# Under home: rollouts are read only from there (or CODEX_HOME).
(Path.home() / ".cache").mkdir(exist_ok=True)
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-budget-check-", dir=Path.home() / ".cache")
atexit.register(shutil.rmtree, os.environ["KG_STORAGE_ROOT"], True)
os.environ.pop("KG_BUDGET_NOTICES", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
from mcp_http import budget, chore_dispatch, harness  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402

NOW = 1_800_000_000.0
rollout = Path(os.environ["KG_STORAGE_ROOT"]) / "rollout-x.jsonl"
failed = 0


def check(name, cond, detail=""):
    global failed
    failed += not cond
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else '  ' + str(detail)}")


def gauge(five, resets, week=10):
    rollout.write_text(json.dumps({"timestamp": "2027-01-15T07:59:00Z", "payload": {"rate_limits": {
        "primary": {"used_percent": five, "window_minutes": 300, "resets_at": resets},
        "secondary": {"used_percent": week, "window_minutes": 10080, "resets_at": resets + 5 * 86400}}}}) + "\n")


def say(sm, sid, now=NOW):
    return budget.notice(sm, sid, harness.CODEX, str(rollout), now)


print("1. two hooks of one session racing (16 threads, one reading at 92%)")
sm = HTTPSessionManager()
sid = sm.register(None)["session_id"]
gauge(92, NOW + 3600)
start, out = threading.Barrier(16), []
def hook():
    start.wait()
    out.append(say(sm, sid))
threads = [threading.Thread(target=hook) for _ in range(16)]
for t in threads: t.start()
for t in threads: t.join()
spoken = [o for o in out if o]
check("exactly one notice", len(spoken) == 1, spoken)
check("and it is the stop level", spoken and "Wrap up now" in spoken[0], spoken)

print("2. a server restart")
sm2 = HTTPSessionManager()                       # reloads sessions.json
check("the session is restored", sm2.lookup(sid) is not None)
check("the level is not said again", say(sm2, sid) is None)

print("3. a window reset")
gauge(85, NOW + 3600 + 5 * 3600)                 # next window, at 85%
check("the next window warns again", "Plan the wrap-up" in (say(sm2, sid) or ""))
gauge(85, NOW + 3600)                            # the old window's reading, once its reset passed
check("a passed window reads empty", say(sm2, sid, NOW + 3601) is None)

print("4. switches")
sid4 = sm2.register(None)["session_id"]
gauge(95, NOW + 3600)
with patch.dict(os.environ, {"KG_BUDGET_NOTICES": "0"}):
    check("KG_BUDGET_NOTICES=0 is off", say(sm2, sid4) is None)
with patch.object(chore_dispatch, "_config", return_value={"budget_notices": False}):
    check('"budget_notices": false is off', say(sm2, sid4) is None)
    with patch.dict(os.environ, {"KG_BUDGET_NOTICES": "1"}):
        check("KG_BUDGET_NOTICES=1 does not override the config off", say(sm2, sid4) is None)
check("being off did not use up the level", say(sm2, sid4) is not None)
sid4b = sm2.register(None)["session_id"]
with patch.dict(os.environ, {"KG_BUDGET_NOTICES": "false"}):
    off = say(sm2, sid4b) is None
check("KG_BUDGET_NOTICES=false is NOT off (only 0 is documented)", not off)

print("5. a fork and a compaction")
sid5 = sm2.register(None, claude_sid="parent-sid")["session_id"]
check("the parent hears the level", say(sm2, sid5) is not None)
clone = sm2.fork(sid5, "child-sid")
check("a fork inherits what its parent heard", say(sm2, clone) is None)
sm2.reset_context(sid5)
check("a compaction keeps the record: not said again", say(sm2, sid5) is None)

print("PASS" if not failed else f"FAIL: {failed} check(s)")
sys.exit(1 if failed else 0)
