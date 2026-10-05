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
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kg-cli-")
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
        box = Sandbox()
        out = box.kg("version")
        want = re.search(r'__version__ = "([^"]+)"', (PLUGIN / "server/version.py").read_text())[1]
        self.assertEqual(out.stdout.strip(), want)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

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

    def test_a_held_port_refuses_and_leaves_the_cause(self):
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", self.box.port))
            holder.listen()
            out = self.box.kg("start")
        self.assertEqual(out.returncode, 1)
        crumb = self.box.home / ".local/state/knowledge-graph/last_start_error"
        self.assertIn(f"port {self.box.port} is held", crumb.read_text())


class DoctorTests(unittest.TestCase):
    def test_bare_machine_names_the_server_and_skips_absent_harnesses(self):
        box = Sandbox()
        out = box.kg("doctor")
        self.assertEqual(out.returncode, 1)
        self.assertIn("✗ not running", out.stdout)
        self.assertEqual(out.stdout.count("– not installed"), 3)

    def test_stale_plugins_untrusted_hooks_and_old_permissions(self):
        box = Sandbox()
        for name in ("claude", "codex", "agy"):
            fake = box.write(f".local/bin/{name}", "#!/bin/sh\n")
            fake.chmod(0o755)
        box.write(".claude/plugins/installed_plugins.json", json.dumps(
            {"plugins": {"knowledge-graph@maxim-plugins": [{"version": "0.0.1"}]}}))
        box.write(".claude/settings.json", json.dumps(
            {"permissions": {"allow": ["mcp__plugin_memory_kg__kg_read"]}}))
        (box.home / ".codex/plugins/cache/maxim-plugins/knowledge-graph/0.0.1").mkdir(parents=True)
        box.write(".codex/config.toml", "[hooks.state]\n")
        box.write(".gemini/antigravity-cli/settings.json", "{}")
        out = box.kg("doctor").stdout
        self.assertIn("plugin 0.0.1, server", out)
        self.assertIn("marketplace auto-update off", out)
        self.assertIn("10 kg tools ask on every call", out)
        self.assertIn("1 permissions name an old plugin", out)
        self.assertIn("no quota gauge", out)
        self.assertIn("hooks not trusted: session_start, user_prompt_submit, post_tool_use", out)
        self.assertIn("✗ plugin not installed", out)  # Antigravity

    def test_auto_mode_approves_without_an_allow_list(self):
        box = Sandbox()
        fake = box.write(".local/bin/claude", "#!/bin/sh\n")
        fake.chmod(0o755)
        box.write(".claude/plugins/installed_plugins.json", json.dumps(
            {"plugins": {"knowledge-graph@maxim-plugins": [{"version": "0.0.1"}]}}))
        box.write(".claude/settings.json", json.dumps({"permissions": {"defaultMode": "auto"}}))
        self.assertIn("kg tools approved by auto mode", box.kg("doctor").stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
