"""chore_state.json cannot be written, and dispatch goes on (Tiers.lean P2-P4).

Real: maybe_dispatch, _read_state, _write_state, _roll_day, the lock
section, _log, _spawn and its watcher.
Stubbed: the gauge (fresh, low), the target (one chore), the agent binary
(/bin/true: the run ends at once). The write fault: a directory where
_write_state puts its temporary file, so every write raises, as a full
disk or a read-only mount would.
"""
import json, os, sys, tempfile, time
from pathlib import Path

root = tempfile.mkdtemp(prefix="kg-state-write-")
os.environ["KG_STORAGE_ROOT"] = root
os.environ["KG_CHORES"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
from core.chores import Chore  # noqa: E402
from core.constants import CHORE_LOG_NAME, CHORE_MIN_INTERVAL_SECONDS  # noqa: E402
from mcp_http import chore_dispatch as cd  # noqa: E402

payload = Chore(kind="edge", targets=["n1"], reason="unconnected", debt=1.0)
cd._gauge_read = lambda cfg, now, runner: ({}, {}, "")
cd._pick_target = lambda *a: ("chore", payload)
cd._chore_gauge_ok = lambda cfg, raw: ""
runner = cd.RUNNERS["claude"]
runner.binary = lambda cfg: "/bin/true"

(Path(root) / "chore_state.tmp").mkdir()      # every _write_state now fails


class Store:
    def maintain_lessons(self):
        return []


for prompt in range(3):
    cd.maybe_dispatch(Store(), None, None)
    while cd._running.is_set():
        time.sleep(0.02)

log = [json.loads(line) for line in open(Path(root) / CHORE_LOG_NAME)]
dispatches = [r for r in log if r["event"] == "dispatch"]
state_file = Path(root) / "chore_state.json"
print(f"min_interval={CHORE_MIN_INTERVAL_SECONDS}s; three prompts within a second")
print("log events:", [r["event"] + (f"({r['reason']})" if r.get("reason") else "") for r in log])
print(f"dispatches={len(dispatches)}  chore_state.json exists={state_file.exists()}")
ok = len(dispatches) <= 1
print("PASS" if ok else
      "BUG: the state write failed silently, so every prompt finds no record of the "
      "last run: no interval, no daily cap, no count")
sys.exit(0 if ok else 1)
