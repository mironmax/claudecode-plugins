#!/usr/bin/env python3
"""Budget notices and the Antigravity maintenance runner.

The agy wrapper runs against a fake `agy` script: the real CLI's behaviour
(agent loading, /agents, stream-json) is covered by the probe in
docs/harnesses/tools/antigravity-probe/chore_smoke.py.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-budget-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name
os.environ.pop("KG_BUDGET_NOTICES", None)

from mcp_http import budget, chore_dispatch, harness
from mcp_http.session_manager import HTTPSessionManager

NOW = 1_800_000_000.0


def usage(gemini=0.10, third=0.30, reset="2027-01-15T08:00:00Z", window="weekly"):
    return {"command": {"name": "usage", "data": {"groups": [
        {"name": "Gemini Models", "buckets": [
            {"id": "gemini-weekly", "window": window, "remaining_fraction": gemini, "reset_time": reset}]},
        {"name": "Claude and GPT models", "buckets": [
            {"id": "3p-weekly", "window": window, "remaining_fraction": third, "reset_time": reset}]}]}}}


class AntigravityGaugeTests(unittest.TestCase):
    def test_model_picks_the_bucket_group(self):
        gemini = chore_dispatch.antigravity_limits(usage(), None, NOW)
        self.assertEqual(gemini["seven_day_pct"], 90.0)
        self.assertTrue(gemini["no_five_hour_window"])
        third = chore_dispatch.antigravity_limits(usage(), "claude-sonnet-4-6", NOW)
        self.assertEqual(third["seven_day_pct"], 70.0)
        self.assertEqual(third["bucket_group"], "Claude and GPT models")

    def test_passed_reset_reads_empty_and_api_key_reading_refuses(self):
        old = chore_dispatch.antigravity_limits(usage(reset="2020-01-01T00:00:00Z"), None, NOW)
        self.assertEqual(old["seven_day_pct"], 0.0)
        with self.assertRaises(ValueError):
            chore_dispatch.antigravity_limits({"command": {"data": {"groups": []}}}, None, NOW)
        with self.assertRaises(ValueError):
            chore_dispatch.antigravity_limits(usage(window="monthly"), None, NOW)

    def test_weekly_only_plan_passes_the_five_hour_gate_but_not_a_spent_week(self):
        cfg = {}
        light = chore_dispatch.antigravity_limits(usage(gemini=0.6), None, NOW)
        self.assertEqual(chore_dispatch._chore_gauge_ok(cfg, light), "")
        heavy = chore_dispatch.antigravity_limits(usage(gemini=0.1), None, NOW)
        self.assertIn("7d", chore_dispatch._chore_gauge_ok(cfg, heavy))
        # A Claude/Codex reading without its five-hour window still refuses.
        self.assertIn("5h", chore_dispatch._chore_gauge_ok(cfg, {"seven_day_pct": 10}))


class AntigravityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="kg-agy-runner-"))
        self.settings_file = self.dir / "settings.json"
        patcher = patch.object(chore_dispatch, "AGY_SETTINGS", self.settings_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = chore_dispatch.AntigravityRunner()
        self.chore = str(chore_dispatch.SHIPPED_SETTINGS)

    def grant(self, allow, **extra):
        self.settings_file.write_text(json.dumps({"permissions": {"allow": allow}, **extra}))

    def test_refusals(self):
        self.assertIn("not granted kg_read", self.runner.refusal({}, self.chore))
        self.grant(["mcp(knowledge-graph_kg/*)"], useG1Credits=True)
        self.assertIn("useG1Credits", self.runner.refusal({}, self.chore))
        self.assertEqual(self.runner.refusal({"antigravity_allow_credits": True}, self.chore), "")
        tools = ["kg_read", "kg_search", "kg_put_node", "kg_put_edge", "kg_rename_node", "kg_progress"]
        self.grant([f"mcp(knowledge-graph_kg/{t})" for t in tools])
        self.assertEqual(self.runner.refusal({}, self.chore), "")
        # Deletions are never required: on Ask, a headless run cannot delete.
        self.assertEqual(self.runner.refusal({}, str(chore_dispatch.SHIPPED_PASS_SETTINGS)), "")

    def test_command_runs_the_wrapper(self):
        cmd = self.runner.command({"antigravity_model": "gemini-x"},
                                  {"tier": "pass", "bin": "/x/agy", "settings": self.chore})
        self.assertEqual(cmd[:2], [sys.executable, str(chore_dispatch.AGY_CHORE_SCRIPT)])
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
        self.assertEqual(cmd[cmd.index("--model") + 1], "gemini-x")


FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
if args[:2] == ["-p", "/agents"]:
    listed = os.path.exists(".agents/agents/kg-maintainer/agent.md") and os.environ.get("FAKE_LOAD") != "no"
    print(json.dumps({"command": {"data": {"agents": ["kg-maintainer"] if listed else []}}}))
    sys.exit(0)
log = args[args.index("--log-file") + 1]
message = json.loads(sys.stdin.readline())
if os.environ.get("FAKE_FALLBACK"):
    open(log, "a").write('Agent "kg-maintainer" not found, falling back to default\n')
    time.sleep(10)
print(json.dumps({"event": "init", "init": {"agent": "kg-maintainer"}}))
print(json.dumps({"event": "result", "result": {"status": "SUCCESS", "num_turns": 2,
      "response": "DONE " + message["message"]["content"], "denied_actions": ["kg_delete_node"]}}))
'''


class WrapperTests(unittest.TestCase):
    def run_wrapper(self, **env):
        work = Path(tempfile.mkdtemp(prefix="kg-agy-wrap-"))
        fake = work / "agy"
        fake.write_text(FAKE_AGY)
        fake.chmod(0o755)
        started = time.time()
        proc = subprocess.run([sys.executable, str(chore_dispatch.AGY_CHORE_SCRIPT), "--bin", str(fake)],
                              input="TIDY THE GRAPH", capture_output=True, text=True, cwd=work,
                              env={**os.environ, **env}, timeout=30)
        return proc, work, time.time() - started

    def test_happy_run_reports_status_and_denials(self):
        proc, work, _ = self.run_wrapper()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('"denied_actions": ["kg_delete_node"]', proc.stdout)
        self.assertIn("DONE TIDY THE GRAPH", proc.stdout)
        self.assertIn("tools: []", (work / "agy-runner/.agents/agents/kg-maintainer/agent.md").read_text())

    def test_agent_that_does_not_load_never_runs(self):
        proc, _, _ = self.run_wrapper(FAKE_LOAD="no")
        self.assertEqual(proc.returncode, 3)
        self.assertIn("did not load", proc.stdout)

    def test_fallback_to_the_default_agent_is_killed(self):
        proc, _, elapsed = self.run_wrapper(FAKE_FALLBACK="1")
        self.assertEqual(proc.returncode, 3)
        self.assertIn("fell back", proc.stdout)
        self.assertLess(elapsed, 8)


def rollout(path: Path, five: float, week: float, resets: float):
    event = {"timestamp": "2027-01-15T07:00:00Z", "type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {
            "primary": {"used_percent": five, "window_minutes": 300, "resets_at": resets},
            "secondary": {"used_percent": week, "window_minutes": 10080, "resets_at": resets + 86400}}}}
    path.write_text(json.dumps(event) + "\n")


class BudgetNoticeTests(unittest.TestCase):
    def setUp(self):
        self.sm = HTTPSessionManager()
        self.sid = self.sm.register(None)["session_id"]
        # Under home: the server reads only a transcript there, as hooks write them.
        (Path.home() / ".cache").mkdir(exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="kg-budget-", dir=Path.home() / ".cache"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.rollout = self.dir / "rollout-test.jsonl"
        self.now = 1_800_000_000.0  # 2027-01-15
        self.resets = self.now + 3600

    def notice(self):
        return budget.notice(self.sm, self.sid, harness.CODEX, str(self.rollout), self.now)

    def test_each_level_is_said_once_per_window(self):
        rollout(self.rollout, 70, 20, self.resets)
        self.assertIsNone(self.notice())
        rollout(self.rollout, 82, 20, self.resets)
        self.assertIn("Plan the wrap-up", self.notice())
        self.assertIsNone(self.notice())
        rollout(self.rollout, 91, 20, self.resets)
        text = self.notice()
        self.assertIn("5-hour quota at 91%", text)
        self.assertIn("Wrap up now", text)
        self.assertIsNone(self.notice())
        rollout(self.rollout, 83, 20, self.resets + 18000)   # a new window
        self.assertIn("Plan the wrap-up", self.notice())

    def test_weekly_and_switches(self):
        rollout(self.rollout, 10, 96, self.resets)
        self.assertIn("weekly quota at 96%", self.notice())
        rollout(self.rollout, 95, 96, self.resets)
        with patch.dict(os.environ, {"KG_BUDGET_NOTICES": "0"}):
            self.assertIsNone(self.notice())
        with patch.object(chore_dispatch, "_config", return_value={"budget_notices": False}):
            self.assertIsNone(self.notice())
        self.assertIsNone(budget.notice(self.sm, self.sid, harness.CODEX, None, self.now))

    def test_a_rollout_outside_home_is_not_read(self):
        outside = Path(tempfile.mkdtemp(prefix="kg-budget-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        if (str(outside.resolve()) + "/").startswith(str(Path.home().resolve()) + "/"):
            self.skipTest("the temp directory is under home here")
        rollout(outside / "rollout-test.jsonl", 95, 96, self.resets)
        self.assertIsNone(budget.notice(self.sm, self.sid, harness.CODEX,
                                        str(outside / "rollout-test.jsonl"), self.now))

    def test_claude_reads_the_status_line_and_a_passed_reset_is_empty(self):
        limits = self.dir / "last-limits.json"
        limits.write_text(json.dumps({"five_hour_pct": 93, "five_hour_resets_at": self.now - 60,
                                      "seven_day_pct": 91, "seven_day_resets_at": self.now + 9e4,
                                      "updated_at": self.now - 7200}))
        with patch.object(chore_dispatch, "_config", return_value={"limits": str(limits)}):
            text = budget.notice(self.sm, self.sid, harness.CLAUDE_CODE, None, self.now)
        self.assertNotIn("5-hour", text)
        self.assertIn("weekly quota at 91%", text)

    def test_antigravity_uses_the_cached_live_reading(self):
        reading = chore_dispatch.antigravity_limits(usage(gemini=0.04), None, self.now)
        with patch.dict(budget._agy, {"at": self.now, "data": reading, "refreshing": False}):
            text = budget.notice(self.sm, self.sid, harness.ANTIGRAVITY, None, self.now)
        self.assertIn("weekly quota at 96%", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
