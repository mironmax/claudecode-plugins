"""The Antigravity budget notice reports a window that has already reset
(Budget.lean B2, Antigravity row).

Real: budget.notice, reading_for, _agy_reading and its five-minute cache,
antigravity_limits (which zeroes a passed window only when it parses
/usage), note_budget_level, HTTPSessionManager.
Stubbed: the clock (explicit timestamps) and the cached `agy -p /usage`
reading: taken 60 s before the weekly reset, at 96% used.
"""
import os, sys, tempfile
from pathlib import Path

os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-budget-agy-")
os.environ.pop("KG_BUDGET_NOTICES", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
from mcp_http import budget, chore_dispatch, harness  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402

RESET = 1_800_000_000.0                      # the weekly window resets here
usage = {"command": {"data": {"groups": [{"name": "Gemini Models", "buckets": [
    {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.04,
     "reset_time": "2027-01-15T08:00:00Z"}]}]}}}
reading = chore_dispatch.antigravity_limits(usage, None, RESET - 60)
assert reading["seven_day_resets_at"] == RESET
budget._agy.update(at=RESET - 60, data=reading, refreshing=False)

sm = HTTPSessionManager()
sid = sm.register(None)["session_id"]        # a session that starts after the reset
note = budget.notice(sm, sid, harness.ANTIGRAVITY, None, now=RESET + 30)
print(f"cached reading: weekly {reading['seven_day_pct']}% used, resets at RESET; "
      f"cache age at the hook: 90 s (cache lifetime {budget.AGY_CACHE_SECONDS} s)")
print(f"notice 30 s after the reset: {note!r}")
ok = note is None
print("PASS" if ok else
      "BUG: the cached reading of a window that has reset is not zeroed; a fresh "
      "session is told to wrap up now while its weekly quota is empty")
sys.exit(0 if ok else 1)
