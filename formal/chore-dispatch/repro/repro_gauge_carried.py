"""A five-hour value carried for hours gates as fresh (Tiers.lean P7).

Real: cli/gauge.py `record` (the status-line writer, `kg gauge`), the file
it writes, ClaudeRunner.gauge, _gauge_read, _chore_gauge_ok and
_pass_gauge_ok.
Stubbed: the clock (explicit timestamps) and the frames Claude Code pipes
to the status line: one with both windows at 10:00, then frames carrying
the weekly window only until 13:00. The five-hour value of 10% is three
hours old; the gauge's max age is 90 minutes.
"""
import importlib.util, json, os, sys, tempfile
from pathlib import Path

root = tempfile.mkdtemp(prefix="kg-gauge-carried-")
os.environ["KG_STORAGE_ROOT"] = root
repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo / "knowledge-graph" / "server"))
from core.constants import CHORE_GAUGE_MAX_AGE_SECONDS  # noqa: E402
from mcp_http import chore_dispatch as cd  # noqa: E402

spec = importlib.util.spec_from_file_location("kg_gauge", repo / "knowledge-graph" / "cli" / "gauge.py")
gauge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gauge)
gauge.LIMITS = Path(root) / "last-limits.json"          # never the user's own file

T10 = 1_800_000_000                     # 10:00
T13 = T10 + 3 * 3600                    # 13:00
week_reset = T10 + 5 * 86400            # the week is ~2/7 through: pace well under 1
gauge.record({"rate_limits": {
    "five_hour": {"used_percentage": 10, "resets_at": T10 + 4 * 3600},
    "seven_day": {"used_percentage": 20, "resets_at": week_reset}}}, T10)
for t in range(T10 + 600, T13 + 1, 600):          # weekly window only, every 10 minutes
    gauge.record({"rate_limits": {"seven_day": {"used_percentage": 20, "resets_at": week_reset}}}, t)

data = json.loads(gauge.LIMITS.read_text())
print("last-limits.json:", {k: data[k] for k in ("five_hour_pct", "five_hour_seen_at",
                                                  "seven_day_seen_at", "updated_at")})
cfg = {"limits": str(gauge.LIMITS)}
reading, raw, err = cd._gauge_read(cfg, T13, cd.ClaudeRunner())
print(f"_gauge_read at 13:00 -> reading={reading} error={err!r}")
chore_gate = cd._chore_gauge_ok(cfg, raw) if raw else "refused"
pass_gate = cd._pass_gauge_ok(cfg, raw, T13) if raw else "refused"
print(f"chore gate: {chore_gate or 'open'}; pass gate: {pass_gate or 'open'}")
five_age = T13 - data["five_hour_seen_at"]
ok = bool(err) or five_age <= CHORE_GAUGE_MAX_AGE_SECONDS
print(f"five-hour value observed {five_age // 60} min ago; max age {CHORE_GAUGE_MAX_AGE_SECONDS // 60} min")
print("PASS" if ok else
      "BUG: freshness is judged by updated_at, which any frame bumps; the five-hour "
      "number the gates spend on is three hours old")
sys.exit(0 if ok else 1)
