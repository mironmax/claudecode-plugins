#!/usr/bin/env python3
"""Server lifecycle races found by the formal pass (formal/lifecycle/).

Each test runs the real kg and server in a sandbox HOME with its own port,
state and storage. Stubbed only to decide when things happen: kg's start
window (15 s, 20 s for the unit) is cut short, as on a machine where the
server takes longer than that; systemctl is a shell script.
"""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

PLUGIN = Path(__file__).resolve().parents[2]
KG = PLUGIN / "cli" / "kg.py"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Box:
    def __init__(self, test: unittest.TestCase, home: Path | None = None, storage: str = ".knowledge-graph"):
        if home is None:
            tmp = tempfile.TemporaryDirectory(prefix="kg-life-")
            test.addCleanup(tmp.cleanup)
            home = Path(tmp.name)
        self.home = home
        self.port = free_port()
        self.env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "KG_HTTP_PORT": str(self.port),
                    "KG_STORAGE_ROOT": str(home / storage),
                    "XDG_STATE_HOME": str(home / ".local/state"),
                    "KG_AUTOCOMMIT_INTERVAL": "0", "KG_CHORES": "0"}
        self.state = home / f".local/state/knowledge-graph/port-{self.port}"
        self.crumb = self.state / "last_start_error"
        self.pidfile = self.state / "server.pid"
        test.addCleanup(self.kg, "stop")

    def kg(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(KG), *args], env=self.env,
                              capture_output=True, text=True, timeout=90)

    def slow_start(self) -> subprocess.CompletedProcess:
        """kg start where the server needs longer than kg waits for it."""
        code = (f"import sys; sys.path.insert(0, {str(KG.parent)!r}); import kg\n"
                "real = kg.wait\n"
                "kg.wait = lambda pred, s: real(pred, 0.3 if s in (15, 20) else s)\n"
                "sys.exit(kg.start())\n")
        return subprocess.run([sys.executable, "-c", code], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as resp:
                return json.loads(resp.read()).get("status") == "ok"
        except Exception:
            return False

    def wait(self, up: bool, seconds: float = 20) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.healthy() == up:
                return True
            time.sleep(0.1)
        return self.healthy() == up

    def crumb_gone(self, seconds: float = 3) -> bool:
        """The server removes the breadcrumb just after it binds."""
        end = time.monotonic() + seconds
        while self.crumb.exists() and time.monotonic() < end:
            time.sleep(0.05)
        return not self.crumb.exists()


class LifecycleRaceTests(unittest.TestCase):
    def test_a_start_that_runs_out_of_time_keeps_its_server_findable(self):
        box = Box(self)
        out = box.slow_start()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertTrue(box.crumb.exists())          # the window ran out: a failed start
        self.assertTrue(box.wait(True))              # ...but its server comes up
        self.assertTrue(box.crumb_gone(), "the server serves under a breadcrumb saying it failed")
        self.assertTrue(box.pidfile.exists(), "kg no longer tracks the server it started")
        self.assertEqual(box.kg("stop").returncode, 0)
        self.assertTrue(box.wait(False, 10))

    def test_a_start_that_finds_the_server_clears_the_breadcrumb(self):
        box = Box(self)
        self.assertEqual(box.kg("start").returncode, 0)
        box.crumb.write_text("when: earlier\ncause: an old failure\n")
        self.assertEqual(box.kg("start").returncode, 0)   # already running
        self.assertFalse(box.crumb.exists())

    def test_the_units_server_clears_the_breadcrumb_once_it_serves(self):
        box = Box(self)
        bin_dir = box.home / "bin"
        bin_dir.mkdir()
        pids = box.home / "unit.pid"
        units = {"systemctl": ('case "$2" in is-enabled) echo enabled;; '
                               f'show) cat "{pids}" 2>/dev/null;; '
                               f'start) setsid {sys.executable} {KG} serve >/dev/null 2>&1 & echo $! > "{pids}";; '
                               f'stop) kill "$(cat "{pids}")";; esac'),
                 "journalctl": "echo journal"}
        for name, body in units.items():
            (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
            (bin_dir / name).chmod(0o755)
        box.env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
        unit = box.home / ".config/systemd/user/kg-memory.service"
        unit.parent.mkdir(parents=True)
        unit.write_text(f'[Service]\nEnvironment="KG_HTTP_PORT={box.port}"\n')
        out = box.slow_start()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertTrue(box.wait(True))
        self.assertTrue(box.crumb_gone(), "the unit's server serves under a failure breadcrumb")

    def test_a_reused_pid_is_not_our_server(self):
        box = Box(self)
        other = Box(self, home=box.home, storage="other-storage")
        self.assertEqual(other.kg("start").returncode, 0)
        # Our server is gone and its pid now belongs to another port's server:
        # the pid file is older than the process it names.
        box.state.mkdir(parents=True, exist_ok=True)
        box.pidfile.write_text(other.pidfile.read_text())
        past = time.time() - 3600
        os.utime(box.pidfile, (past, past))
        self.assertEqual(box.kg("start").returncode, 0)
        self.assertTrue(box.wait(True, 5), "kg start took another port's server for its own")
        self.assertEqual(box.kg("stop").returncode, 0)
        self.assertTrue(other.healthy(), "kg stop killed another port's server")

    def test_a_start_during_a_stop_waits_for_it(self):
        box = Box(self)
        self.assertEqual(box.kg("start").returncode, 0)
        # A call still in flight keeps the server's graceful shutdown waiting.
        held = socket.create_connection(("127.0.0.1", box.port))
        self.addCleanup(held.close)
        held.sendall(b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                     b"Accept: application/json, text/event-stream\r\nContent-Length: 1000\r\n\r\n{")
        time.sleep(0.5)
        stopper = subprocess.Popen([sys.executable, str(KG), "stop"], env=box.env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertTrue(box.wait(False, 5))
        started = box.kg("start")
        stopper.wait(60)
        self.assertEqual(started.returncode, 0, started.stdout)
        self.assertTrue(box.wait(True, 5), "kg start reported success; nothing serves after the stop")


if __name__ == "__main__":
    unittest.main(verbosity=2)
