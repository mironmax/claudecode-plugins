"""The lock re-decides on fresh state but on the decision's config and clock
(Tiers.lean P5-P8).

Real: _config (chores.json, re-read on mtime), _runner, maybe_dispatch, the
lock section, _read_state/_write_state, _spawn and its watcher.
Stubbed: the gauge (fresh and low when read), the target (one chore on the
user graph, refused when the snapshot shows it on cooldown, as
_pick_target does), the agent binaries (/bin/true), and in `suspend` the
clock.
Usage: python repro_midflight.py [config|suspend]
  config   the user edits chores.json while a decision is in flight: chores
           off, or the runner changed
  suspend  the machine sleeps for two hours in the middle of a decision; a
           prompt after wake-up decides on the same old state
"""
import json, os, sys, tempfile, threading, time
from pathlib import Path
from types import SimpleNamespace

mode = sys.argv[1] if len(sys.argv) > 1 else "config"
root = tempfile.mkdtemp(prefix="kg-midflight-")
os.environ["KG_STORAGE_ROOT"] = root
os.environ.pop("KG_CHORES", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
from core.chores import Chore  # noqa: E402
from core.constants import (CHORE_GAUGE_MAX_AGE_SECONDS, CHORE_GRAPH_COOLDOWN_SECONDS,  # noqa: E402
                            CHORE_LOG_NAME)
from mcp_http import chore_dispatch as cd  # noqa: E402

cfg_path = Path(root) / "chores.json"
fake_codex = Path(root) / "codex"
fake_codex.write_text("#!/bin/sh\ncat >/dev/null\n")
fake_codex.chmod(0o755)
cd.RUNNERS["claude"].binary = lambda cfg: "/bin/true"


def write_cfg(data: dict, bump: int) -> None:
    cfg_path.write_text(json.dumps(data))
    os.utime(cfg_path, (1e9 + bump, 1e9 + bump))      # a new mtime: _config re-reads


payload = Chore(kind="edge", targets=["n1"], reason="unconnected", debt=1.0)
cd._chore_gauge_ok = lambda cfg, raw: ""
cd._gauge_read = lambda cfg, now, runner: ({"5h": 10, "age_s": 0}, {}, "")


def log():
    path = Path(root) / CHORE_LOG_NAME
    return [json.loads(line) for line in open(path)] if path.exists() else []


class Store:
    def maintain_lessons(self):
        return []


def wait_idle():
    while cd._running.is_set():
        time.sleep(0.02)


bad = []
if mode == "config":
    edits = [("chores switched off", {"enabled": False}),
             ("runner changed to codex", {"enabled": True, "runner": "codex",
                                          "codex_bin": str(fake_codex)})]
    for n, (label, edit) in enumerate(edits):
        (Path(root) / "chore_state.json").unlink(missing_ok=True)
        (Path(root) / CHORE_LOG_NAME).unlink(missing_ok=True)
        write_cfg({"enabled": True}, 10 * n)

        def pick(*a, edit=edit, n=n):
            write_cfg(edit, 10 * n + 1)        # the user saves chores.json now
            return "chore", payload
        cd._pick_target = pick
        cd.maybe_dispatch(Store(), None, None)
        wait_idle()
        ran = [r.get("runner") for r in log() if r["event"] == "dispatch"]
        print(f"{label} during target selection -> dispatched: {ran or 'nothing'}")
        if ran:
            bad.append(label)
else:
    write_cfg({"enabled": True}, 0)
    clock = [1_800_000_000.0]
    cd.time = SimpleNamespace(time=lambda: clock[0], strftime=time.strftime,
                              localtime=time.localtime, sleep=time.sleep)

    def pick(store, sm, state, cfg, now, candidates):
        last = (state.get("graphs") or {}).get("user", 0)
        if now - last < cfg.get("graph_cooldown_s", CHORE_GRAPH_COOLDOWN_SECONDS):
            return None, "all on cooldown"
        return "chore", payload
    cd._pick_target = pick
    cd._gauge_read = lambda cfg, now, runner: (
        {"5h": 10, "age_s": 0, "read_at": clock[0], "by": threading.current_thread().name}, {}, "")

    t0_in, t0_go, t1_in, t1_go = (threading.Event() for _ in range(4))

    class Paused:
        def maintain_lessons(self):
            name = threading.current_thread().name
            (t0_in if name == "T0" else t1_in).set()
            (t0_go if name == "T0" else t1_go).wait(10)
            return []

    t0 = threading.Thread(target=cd.maybe_dispatch, args=(Paused(), None, None), name="T0")
    t0.start(); t0_in.wait(10)
    clock[0] += 2 * 3600                     # suspended for two hours, mid-decision
    t1 = threading.Thread(target=cd.maybe_dispatch, args=(Paused(), None, None), name="T1")
    t1.start(); t1_in.wait(10)               # the first prompt after wake-up
    t0_go.set(); t0.join(); wait_idle()
    t1_go.set(); t1.join(); wait_idle()
    ds = [r for r in log() if r["event"] == "dispatch"]
    state = json.loads((Path(root) / "chore_state.json").read_text())
    print("log events:", [r["event"] + (f"({r['reason']})" if r.get("reason") else "") for r in log()])
    print(f"dispatches on graph 'user': {len(ds)}; state.graphs.user={state.get('graphs', {}).get('user')}, "
          f"wall clock at the end {clock[0]:.0f}")
    for d in ds:
        age = d["ts"] - d["gauge"]["read_at"]
        print(f"  {d['gauge']['by']} dispatched on a gauge read {age / 60:.0f} min earlier")
        if age > CHORE_GAUGE_MAX_AGE_SECONDS:
            bad.append(f"{d['gauge']['by']} spent on a gauge read {age / 60:.0f} min earlier "
                       f"(max age {CHORE_GAUGE_MAX_AGE_SECONDS // 60} min)")
    if len(ds) >= 2:   # both started at the same wall-clock moment, after the wake-up
        bad.append(f"two chores on one graph inside its {CHORE_GRAPH_COOLDOWN_SECONDS // 3600} h cooldown")

print("PASS" if not bad else "BUG: " + "; ".join(bad))
sys.exit(0 if not bad else 1)
