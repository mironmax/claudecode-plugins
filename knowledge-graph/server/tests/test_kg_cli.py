#!/usr/bin/env python3
"""The `kg` command: package metadata, server lifecycle and `kg doctor`.

Every run uses its own HOME, port, state and storage directories, so the
shared server and the user's harness configs are never touched.
"""

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest

PLUGIN = Path(__file__).resolve().parents[2]
KG = PLUGIN / "cli" / "kg.py"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def requirement_lines(path: Path) -> set[str]:
    return {line.split("#")[0].strip() for line in path.read_text().splitlines()
            if line.split("#")[0].strip()}


class Sandbox:
    def __init__(self, test: unittest.TestCase):
        self.tmp = tempfile.TemporaryDirectory(prefix="kg-cli-")
        test.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.port = free_port()
        self.env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "KG_HTTP_PORT": str(self.port),
            "KG_STORAGE_ROOT": str(self.home / ".knowledge-graph"),
            "XDG_STATE_HOME": str(self.home / ".local/state"),
            "KG_AUTOCOMMIT_INTERVAL": "0",
            "KG_CHORES": "0",
        }
        # kg keeps a non-default port's state in its own folder
        self.state = self.home / f".local/state/knowledge-graph/port-{self.port}"

    def kg(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(KG), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def write(self, relative: str, text: str) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class PackageTests(unittest.TestCase):
    def test_dependencies_match_both_requirement_files(self):
        text = (PLUGIN / "pyproject.toml").read_text()
        block = text.split("dependencies = [", 1)[1].split("\n]", 1)[0]
        declared = set(re.findall(r'"([^"]+)"', block))
        wanted = (requirement_lines(PLUGIN / "server/requirements.txt")
                  | requirement_lines(PLUGIN / "visual-editor/requirements.txt"))
        self.assertEqual(declared, wanted)

    def test_version_comes_from_the_server(self):
        box = Sandbox(self)
        out = box.kg("version")
        want = re.search(r'__version__ = "([^"]+)"', (PLUGIN / "server/version.py").read_text())[1]
        self.assertEqual(out.stdout.strip(), want)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self)

    def tearDown(self):
        self.box.kg("stop")

    def test_start_status_stop(self):
        started = self.box.kg("start")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        status = self.box.kg("status")
        self.assertIn("Server is running", status.stdout)
        self.assertIn(str(KG.parents[1] / "server" / "mcp_streamable_server.py"), status.stdout)
        self.assertEqual(self.box.kg("start").returncode, 0)  # already running
        self.assertEqual(self.box.kg("stop").returncode, 0)
        self.assertEqual(self.box.kg("status").returncode, 1)

    def test_a_second_server_on_the_same_storage_refuses(self):
        self.assertEqual(self.box.kg("start").returncode, 0)
        second = dict(self.box.env, KG_HTTP_PORT=str(free_port()),
                      XDG_STATE_HOME=str(self.box.home / "second-state"))
        out = subprocess.run([sys.executable, str(KG), "start"], env=second,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 1)
        self.assertIn("another memory server already uses", out.stdout)

    def test_two_ports_keep_separate_state(self):
        self.assertEqual(self.box.kg("start").returncode, 0)
        first = int((self.box.state / "server.pid").read_text())
        second_port = free_port()
        second = dict(self.box.env, KG_HTTP_PORT=str(second_port),
                      KG_STORAGE_ROOT=str(self.box.home / "second-storage"))
        run = lambda *a: subprocess.run([sys.executable, str(KG), *a], env=second,
                                        capture_output=True, text=True, timeout=60)
        self.assertEqual(run("start").returncode, 0)
        second_state = self.box.home / f".local/state/knowledge-graph/port-{second_port}"
        self.assertNotEqual(int((second_state / "server.pid").read_text()), first)
        self.assertEqual(int((self.box.state / "server.pid").read_text()), first)
        self.assertEqual(run("stop").returncode, 0)
        self.assertEqual(self.box.kg("status").returncode, 0)   # the first server is untouched

    def test_concurrent_starts_share_one_server(self):
        starts = [subprocess.Popen([sys.executable, str(KG), "start"], env=self.box.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                  for _ in range(3)]
        outputs = [p.communicate(timeout=60)[0] for p in starts]
        self.assertEqual([p.returncode for p in starts], [0, 0, 0], outputs)
        self.assertEqual(sum("Server started" in out for out in outputs), 1, outputs)
        self.assertFalse((self.box.state / "last_start_error").exists())

    def test_a_held_port_refuses_and_leaves_the_cause(self):
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", self.box.port))
            holder.listen()
            out = self.box.kg("start")
        self.assertEqual(out.returncode, 1)
        crumb = self.box.state / "last_start_error"
        self.assertIn(f"port {self.box.port} is held", crumb.read_text())


class McpShimTests(unittest.TestCase):
    """`kg mcp`: stdio in, the shared HTTP server behind it, started and
    restarted on demand."""

    def setUp(self):
        self.box = Sandbox(self)
        self.shim = subprocess.Popen([sys.executable, str(KG), "mcp"], env=self.box.env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(self.box.kg, "stop")
        self.addCleanup(self.close_shim)

    def close_shim(self):
        self.shim.stdin.close()
        self.shim.wait(10)
        self.shim.stdout.close()

    def call(self, request_id, method, params=None):
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.shim.stdin.write(json.dumps(message) + "\n")
        self.shim.stdin.flush()
        return json.loads(self.shim.stdout.readline())

    def test_starts_the_server_and_rides_out_a_stop(self):
        init = self.call(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "codex-mcp-client", "version": "1"}})
        self.assertIn("serverInfo", init["result"])
        self.assertEqual(self.box.kg("status").returncode, 0)
        self.assertEqual(len(self.call(2, "tools/list")["result"]["tools"]), 10)

        self.assertEqual(self.box.kg("stop").returncode, 0)
        self.assertEqual(len(self.call(3, "tools/list")["result"]["tools"]), 10)
        self.assertEqual(self.call(4, "no/such")["error"]["code"], -32601)

    def test_the_harness_keeps_its_identity(self):
        sys.path.insert(0, str(PLUGIN / "server"))
        from mcp_http import harness
        self.assertEqual(harness.from_user_agent("codex-mcp-client/1 (kg mcp)"), harness.CODEX)
        self.assertEqual(harness.from_user_agent("claude-code/2 (kg mcp)"), harness.CLAUDE_CODE)


class EditorTests(unittest.TestCase):
    def test_editor_starts_with_its_server_and_stops(self):
        box = Sandbox(self)
        box.env["EDITOR_PORT"] = str(free_port())
        self.addCleanup(box.kg, "stop")
        self.addCleanup(box.kg, "editor", "stop")
        out = box.kg("editor")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn(f"localhost:{box.env['EDITOR_PORT']}", out.stdout)
        self.assertEqual(box.kg("status").returncode, 0)
        self.assertIn("Editor stopped", box.kg("editor", "stop").stdout)


def fake_binaries(box, *names):
    for name in names:
        box.write(f".local/bin/{name}", "#!/bin/sh\n").chmod(0o755)


class DoctorTests(unittest.TestCase):
    def test_bare_machine_names_the_server_and_skips_absent_harnesses(self):
        box = Sandbox(self)
        out = box.kg("doctor")
        self.assertEqual(out.returncode, 1)
        self.assertIn("✗ not running", out.stdout)
        self.assertEqual(out.stdout.count("– not installed"), 4)  # one line per absent harness

    def test_stale_plugins_untrusted_hooks_and_old_permissions(self):
        box = Sandbox(self)
        fake_binaries(box, "claude", "codex", "agy")
        box.write(".claude/plugins/installed_plugins.json", json.dumps(
            {"plugins": {"knowledge-graph@maxim-plugins": [{"version": "0.0.1"}]}}))
        box.write(".claude/plugins/known_marketplaces.json", json.dumps({"maxim-plugins": {}}))
        box.write(".claude/settings.json", json.dumps(
            {"permissions": {"allow": ["mcp__plugin_memory_kg__kg_read", "mcp__memory__search_nodes"]}}))
        (box.home / ".codex/plugins/cache/maxim-plugins/knowledge-graph/0.0.1").mkdir(parents=True)
        box.write(".codex/config.toml", "[hooks.state]\n")
        box.write(".gemini/antigravity-cli/settings.json", "{}")
        out = box.kg("doctor").stdout
        self.assertIn("plugin 0.0.1, server", out)
        self.assertIn("auto-update off", out)
        self.assertIn("1 permissions name this plugin's old name", out)  # not the other server's
        self.assertIn("auto-memory on", out)
        self.assertIn("no quota gauge", out)
        self.assertIn("not trusted: session_start, user_prompt_submit, post_tool_use", out)
        self.assertIn("✗ plugin not installed", out)  # Antigravity

    def test_auto_mode_is_a_suggestion_not_a_problem(self):
        box = Sandbox(self)
        fake_binaries(box, "claude")
        box.write(".claude/settings.json", json.dumps({"permissions": {"defaultMode": "auto"}}))
        self.assertIn("· approved by auto mode today", box.kg("doctor").stdout)


class SetupTests(unittest.TestCase):
    def test_plan_changes_nothing_and_no_terminal_needs_yes(self):
        box = Sandbox(self)
        fake_binaries(box, "claude")
        settings = box.write(".claude/settings.json", "{}")
        out = box.kg("setup", "--plan")
        self.assertIn("[claude-automemory]", out.stdout)
        self.assertEqual(settings.read_text(), "{}")
        self.assertIn("rerun with --yes", box.kg("setup").stdout)

    def test_settings_steps_apply_with_backups(self):
        box = Sandbox(self)
        fake_binaries(box, "claude", "agy")
        box.write(".claude/settings.json", json.dumps({"model": "x", "permissions": {"allow": [
            "Bash(ls)", "mcp__plugin_memory_kg__kg_read", "mcp__memory__search_nodes"]},
            "statusLine": {"type": "command", "command": "my-line --flag 'a b'"}}))
        box.write(".gemini/antigravity-cli/settings.json", json.dumps({"permissions": {"allow": ["command(ls)"]}}))
        out = box.kg("setup", "--yes", "--only",
                     "claude-permissions,claude-automemory,claude-gauge,agy-permissions,upkeep,command")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        claude = json.loads((box.home / ".claude/settings.json").read_text())
        allow = claude["permissions"]["allow"]
        self.assertIn("Bash(ls)", allow)
        self.assertIn("mcp__memory__search_nodes", allow)
        self.assertNotIn("mcp__plugin_memory_kg__kg_read", allow)
        self.assertIn("mcp__plugin_knowledge-graph_kg__kg_delete_edge", allow)
        self.assertIs(claude["autoMemoryEnabled"], False)
        self.assertEqual(claude["model"], "x")
        self.assertIn("gauge --wrap 'my-line --flag '\"'\"'a b'\"'\"''", claude["statusLine"]["command"])
        agy = json.loads((box.home / ".gemini/antigravity-cli/settings.json").read_text())["permissions"]["allow"]
        self.assertIn("command(ls)", agy)
        self.assertIn("mcp(knowledge-graph_kg/kg_progress)", agy)
        self.assertNotIn("mcp(knowledge-graph_kg/kg_delete_node)", agy)
        self.assertTrue(json.loads((box.home / ".knowledge-graph/chores.json").read_text())["enabled"])
        self.assertTrue((box.home / ".local/bin/kg").is_symlink())
        backups = list((box.state / "backups").rglob("settings.json"))
        self.assertEqual(len(backups), 2)
        again = box.kg("doctor").stdout
        self.assertIn("kg tools pre-approved for every project", again)
        self.assertIn("kg tools granted", again)

    def test_desktop_and_old_commands_move_to_kg(self):
        box = Sandbox(self)
        rel = ("Library/Application Support/Claude" if sys.platform == "darwin"
               else ".config/Claude") + "/claude_desktop_config.json"
        config = box.write(rel, json.dumps({"mcpServers": {
            "knowledge-graph": {"command": str(box.home / ".local/bin/kg-desktop-bridge")},
            "other": {"command": "x"}}}))
        box.write(".local/bin/kg", "#!/bin/sh\n").chmod(0o755)
        bridge = box.home / ".local/bin/kg-desktop-bridge"
        bridge.symlink_to(box.home / "desktop_bridge.sh")
        visual = box.home / ".local/bin/kg-visual"
        visual.symlink_to(box.home / "visual-editor/manage_visual.sh")
        out = box.kg("setup", "--yes", "--only", "claude-desktop,legacy-commands")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        servers = json.loads(config.read_text())["mcpServers"]
        self.assertEqual(servers["knowledge-graph"],
                         {"command": str(box.home / ".local/bin/kg"), "args": ["mcp"]})
        self.assertEqual(servers["other"], {"command": "x"})
        self.assertFalse(bridge.is_symlink())
        self.assertFalse(visual.is_symlink())
        self.assertIn("connected through kg mcp", box.kg("doctor").stdout)
        box.kg("uninstall", "--yes")
        self.assertNotIn("knowledge-graph", json.loads(config.read_text())["mcpServers"])


class UninstallAndUpdateTests(unittest.TestCase):
    def test_uninstall_gives_back_the_users_own_settings(self):
        box = Sandbox(self)
        fake_binaries(box, "claude", "agy")
        original = {"permissions": {"allow": ["Bash(ls)", "mcp__memory__search_nodes"]},
                    "statusLine": {"type": "command", "command": "my-line --flag 'a b'"}}
        box.write(".claude/settings.json", json.dumps(original))
        box.write(".gemini/antigravity-cli/settings.json", json.dumps({"permissions": {"allow": ["command(ls)"]}}))
        keys = "claude-permissions,claude-automemory,claude-gauge,agy-permissions,upkeep,command"
        self.assertEqual(box.kg("setup", "--yes", "--only", keys).returncode, 0)
        plan = box.kg("uninstall", "--plan").stdout
        self.assertIn("restore your own status line", plan)
        self.assertIn("remove the development kg link", plan)
        self.assertEqual(box.kg("uninstall", "--yes").returncode, 0)
        self.assertEqual(json.loads((box.home / ".claude/settings.json").read_text()), original)
        agy = json.loads((box.home / ".gemini/antigravity-cli/settings.json").read_text())
        self.assertEqual(agy["permissions"]["allow"], ["command(ls)"])
        self.assertFalse(json.loads((box.home / ".knowledge-graph/chores.json").read_text())["enabled"])
        self.assertFalse((box.home / ".local/bin/kg").exists())
        self.assertIn("Nothing set up by kg", box.kg("uninstall", "--plan").stdout)

    def test_update_from_a_checkout_brings_the_server_up(self):
        box = Sandbox(self)
        self.addCleanup(box.kg, "stop")
        out = box.kg("update")
        self.assertIn("Development checkout", out.stdout)
        self.assertIn("server restarted on this copy", out.stdout)
        self.assertIn("Server is running", box.kg("status").stdout)


class GaugeTests(unittest.TestCase):
    def gauge(self, box, frame, *args):
        return subprocess.run([sys.executable, str(KG), "gauge", *args], env=box.env,
                              input=json.dumps(frame), capture_output=True, text=True, timeout=30)

    def test_records_and_wraps_the_users_line(self):
        box = Sandbox(self)
        frame = {"rate_limits": {"five_hour": {"used_percentage": 41, "resets_at": 100},
                                 "seven_day": {"used_percentage": 22, "resets_at": 200}},
                 "context_window": {"used_percentage": 9}}
        out = self.gauge(box, frame, "--wrap", "cat > /dev/null; echo mine")
        self.assertEqual(out.stdout, "mine\n")
        limits = json.loads((box.home / ".claude/last-limits.json").read_text())
        self.assertEqual((limits["five_hour_pct"], limits["seven_day_pct"], limits["context_pct"]), (41, 22, 9))

    def test_a_missing_window_keeps_its_old_value_and_stamp(self):
        box = Sandbox(self)
        box.write(".claude/last-limits.json", json.dumps(
            {"five_hour_pct": 10, "five_hour_seen_at": 5, "seven_day_pct": 30, "seven_day_seen_at": 6}))
        out = self.gauge(box, {"rate_limits": {"five_hour": {"used_percentage": 50}}})
        self.assertEqual(out.stdout.strip(), "5h 50% · week 30%")
        limits = json.loads((box.home / ".claude/last-limits.json").read_text())
        self.assertEqual((limits["seven_day_pct"], limits["seven_day_seen_at"]), (30, 6))
        self.gauge(box, {})  # no live window: the file is left alone
        self.assertEqual(json.loads((box.home / ".claude/last-limits.json").read_text()), limits)


class HookTests(unittest.TestCase):
    """With kg installed, the hooks start the server through it and read its
    breadcrumb, whichever plugin copy the harness runs."""

    def setUp(self):
        self.box = Sandbox(self)
        self.marker = self.box.home / "kg-called"
        self.box.write(".local/bin/kg", f"#!/bin/sh\necho \"$@\" > {self.marker}\n").chmod(0o755)

    def wait_marker(self):
        for _ in range(40):
            if self.marker.exists():
                return self.marker.read_text().strip()
            time.sleep(0.1)
        return None

    def autostart(self, payload="{}"):
        return subprocess.run(["bash", str(PLUGIN / "hooks/kg-autostart.sh")], input=payload,
                              env=self.box.env, capture_output=True, text=True, timeout=30).stdout

    def test_claude_and_codex_hook_starts_kg(self):
        self.assertIn("did not start", self.autostart())
        self.assertEqual(self.wait_marker(), "start")

    def agy(self):
        return subprocess.run([sys.executable, str(PLUGIN / "hooks/kg-agy.py"), "SessionStart"],
                              input=json.dumps({"conversationId": "c1"}), env=self.box.env,
                              capture_output=True, text=True, timeout=30).stdout

    def test_without_kg_the_hooks_offer_the_install(self):
        (self.box.home / ".local/bin/kg").unlink()
        for out in (self.autostart(), self.agy()):
            self.assertIn("Offer to install it", out)
            self.assertIn("kg-ops skill", out)
            self.assertIn("uv tool install kg-memory", out)

    def test_a_cold_start_still_preloads(self):
        self.addCleanup(self.box.kg, "stop")
        self.box.write(".local/bin/kg", f"#!/bin/sh\nexec {sys.executable} {KG} \"$@\"\n")
        project = self.box.home / "project"
        project.mkdir()
        out = self.autostart(json.dumps({"cwd": str(project)}))
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("KG MEMORY PRELOADED", context)

    def test_claude_and_codex_hook_reports_the_kg_breadcrumb(self):
        self.box.write(str((self.box.state / "last_start_error").relative_to(self.box.home)),
                       "when: now\ncause: port 1 is held by another program\nlog: x\n")
        out = self.autostart()
        self.assertIn("port 1 is held by another program", out)
        self.assertIn("kg doctor", out)
        self.assertIsNone(self.wait_marker())

    def test_antigravity_hook_starts_kg(self):
        self.assertIn("is starting", self.agy())
        self.assertEqual(self.wait_marker(), "start")

if __name__ == "__main__":
    unittest.main(verbosity=2)
