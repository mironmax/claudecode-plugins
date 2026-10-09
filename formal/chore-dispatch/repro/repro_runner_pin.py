"""Under "auto", a configured codex_bin does not pin Codex (Tiers.lean R1).

Real: _config (chores.json), _runner, maybe_dispatch's runner and gauge
choice, the dispatch log, _spawn.
Stubbed: which binaries exist (a fake `claude` stands in for an installed one; codex_bin names a
path, correct or mistyped) and the gauge reading (fresh, low). The target
choice is stubbed to one chore.
"""
import json, os, sys, tempfile, time
from pathlib import Path

root = tempfile.mkdtemp(prefix="kg-runner-pin-")
os.environ["KG_STORAGE_ROOT"] = root
os.environ.pop("KG_CHORES", None)
bindir = Path(root) / "bin"
bindir.mkdir()
for name in ("claude", "codex"):
    (bindir / name).write_text("#!/bin/sh\ncat >/dev/null\n")
    (bindir / name).chmod(0o755)
os.environ["PATH"] = f"{bindir}:/usr/bin:/bin"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
from core.constants import CHORE_LOG_NAME  # noqa: E402
from mcp_http import chore_dispatch as cd  # noqa: E402

from core.chores import Chore  # noqa: E402
payload = Chore(kind="edge", targets=["n1"], reason="unconnected", debt=1.0)
gauged = []
cd._gauge_read = lambda cfg, now, runner: (gauged.append(runner.name) or ({}, {}, ""))
cd._pick_target = lambda *a: ("chore", payload)
cd._chore_gauge_ok = lambda cfg, raw: ""
# Claude Code is installed: here the fake one, never a real binary on this machine.
cd.RUNNERS["claude"].binary = lambda cfg: cfg.get("claude_bin") or str(bindir / "claude")


class Store:
    def maintain_lessons(self):
        return []


cases = [("codex_bin correct", str(bindir / "codex")),
         ("codex_bin mistyped", str(bindir / "codx"))]
bad = 0
for label, path in cases:
    (Path(root) / "chore_state.json").unlink(missing_ok=True)
    (Path(root) / CHORE_LOG_NAME).unlink(missing_ok=True)
    cfg_path = Path(root) / "chores.json"
    cfg_path.write_text(json.dumps({"enabled": True, "codex_bin": path}))
    os.utime(cfg_path, (time.time(), time.time() + len(gauged)))   # new mtime per case
    gauged.clear()
    cd.maybe_dispatch(Store(), None, None)
    while cd._running.is_set():
        time.sleep(0.02)
    log = [json.loads(line) for line in open(Path(root) / CHORE_LOG_NAME)]
    ran = [r.get("runner") for r in log if r["event"] == "dispatch"]
    print(f"{label}: chores.json {{'codex_bin': {Path(path).name!r}}} (runner auto) -> "
          f"gauge read: {gauged}, dispatched on: {ran}")
    if ran and ran != ["codex"]:
        bad += 1
print("PASS" if not bad else
      "BUG: a configured codex_bin does not pin Codex while Claude Code is installed; "
      "the run spends the Claude subscription")
sys.exit(0 if not bad else 1)
