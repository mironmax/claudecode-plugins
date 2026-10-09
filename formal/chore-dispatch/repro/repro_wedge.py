"""A runner command that raises leaves _running set for good (Tiers.lean P9).

Real: _config (chores.json), _settings_path, maybe_dispatch, the lock
section, _spawn, CodexRunner.command and allowed_tools.
Stubbed: the gauge (fresh, low), the target (one chore) and the codex
binary (a script that would exit at once). The fault: the configured
"settings" allowlist is not valid JSON (a hand edit gone wrong); the Claude
runner passes the path to claude, the Codex runner parses it itself.
"""
import json, os, sys, tempfile, time
from pathlib import Path

root = tempfile.mkdtemp(prefix="kg-wedge-")
os.environ["KG_STORAGE_ROOT"] = root
os.environ.pop("KG_CHORES", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
from core.chores import Chore  # noqa: E402
from core.constants import CHORE_LOG_NAME  # noqa: E402
from mcp_http import chore_dispatch as cd  # noqa: E402

fake = Path(root) / "codex"
fake.write_text("#!/bin/sh\ncat >/dev/null\n")
fake.chmod(0o755)
settings = Path(root) / "my-settings.json"
settings.write_text('{"permissions": {"allow": ["mcp__plugin_knowledge-graph_kg__kg_read",]}}')
(Path(root) / "chores.json").write_text(json.dumps(
    {"enabled": True, "runner": "codex", "codex_bin": str(fake), "settings": str(settings)}))

payload = Chore(kind="edge", targets=["n1"], reason="unconnected", debt=1.0)
cd._gauge_read = lambda cfg, now, runner: ({}, {}, "")
cd._pick_target = lambda *a: ("chore", payload)
cd._chore_gauge_ok = lambda cfg, raw: ""


class Store:
    def maintain_lessons(self):
        return []


cd.maybe_dispatch(Store(), None, None)
time.sleep(1.0)
settings.write_text(json.dumps({"permissions": {"allow": []}}))   # the user repairs the file
for _ in range(3):                                                 # later prompts
    cd.maybe_dispatch(Store(), None, None)
time.sleep(0.5)
log = [json.loads(line) for line in open(Path(root) / CHORE_LOG_NAME)]
print("log events:", [r["event"] for r in log])
print(f"_running set: {cd._running.is_set()}  (no process was started)")
ok = not cd._running.is_set()
print("PASS" if ok else
      "BUG: the dispatch is logged, no process runs, and _running stays set: every later "
      "prompt returns at the fast gate without a log line until the server restarts")
sys.exit(0 if ok else 1)
