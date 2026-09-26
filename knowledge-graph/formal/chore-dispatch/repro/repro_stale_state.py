"""Deterministic reproduction of the Lean counterexample against the real code.

Real: maybe_dispatch's state read, lock section, _write_state, _log, _spawn.
Stubbed: the gauge / target / prompt gates (they decide *whether* a moment is
eligible, not how dispatch is serialized) and the claude binary.
Forced interleaving: T1 pauses between reading state and taking the lock
(inside store.maintain_lessons, which the real code calls in that window)
while T0 runs a whole dispatch.
Usage: python repro_stale_state.py [spawn_fail|quick_exit]
"""
import json, os, sys, tempfile, threading, time
from pathlib import Path
from types import SimpleNamespace

mode = sys.argv[1] if len(sys.argv) > 1 else "spawn_fail"
root = tempfile.mkdtemp()
os.environ["KG_STORAGE_ROOT"] = root
os.environ["KG_CHORES"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "server"))
from mcp_http import chore_dispatch as cd
from core.constants import CHORE_LOG_NAME, CHORE_MAX_PER_DAY, CHORE_MIN_INTERVAL_SECONDS

payload = SimpleNamespace(level="user", graph="user", project_path=None, kind="anchor",
                          targets=["n1"], debt=1.0, pool="p", context={})
cd._gauge_read = lambda cfg, now: ({}, {}, "")
cd._pick_target = lambda *a: ("chore", payload)
cd._chore_gauge_ok = lambda cfg, raw: ""
cd._settings_path = lambda cfg, tier="chore": "/dev/null"
cd.build_chore_prompt = lambda *a, **k: "p"
cd._graph_debt = lambda *a, **k: ({}, 0, 0)
if mode == "spawn_fail":
    cd._claude_bin = lambda cfg: "/nonexistent/claude"      # Popen raises -> _running.clear()
else:
    cd._claude_bin = lambda cfg: "/bin/true"                # process exits at once

t1_has_read = threading.Event()
release_t1 = threading.Event()

class Store:
    def maintain_lessons(self):
        if threading.current_thread().name == "T1":
            t1_has_read.set()          # T1 has read chore_state.json and passed every gate
            release_t1.wait(10)
        return []

store = Store()
t1 = threading.Thread(target=cd.maybe_dispatch, args=(store, None, None), name="T1")
t1.start()
assert t1_has_read.wait(10)
t0 = threading.Thread(target=cd.maybe_dispatch, args=(store, None, None), name="T0")
t0.start(); t0.join()
deadline = time.time() + 10
while cd._running.is_set() and time.time() < deadline:   # quick_exit: wait for watcher
    time.sleep(0.01)
release_t1.set(); t1.join()
time.sleep(0.2)

log = [json.loads(l) for l in open(os.path.join(root, CHORE_LOG_NAME))]
dispatches = [r for r in log if r.get("event") == "dispatch"]
state = json.load(open(os.path.join(root, "chore_state.json")))
print(f"mode={mode}  min_interval={CHORE_MIN_INTERVAL_SECONDS}s  max_per_day={CHORE_MAX_PER_DAY}")
print("log events:", [r["event"] for r in log])
print(f"dispatches={len(dispatches)}  state.count={state['count']}  state.last_ts={state['last_ts']:.3f}")
ok = len(dispatches) <= 1 and state["count"] == len(dispatches)
print("PASS" if ok else
      "FAIL: two dispatches inside min_interval, and state.count lost one of them")
sys.exit(0 if ok else 1)
