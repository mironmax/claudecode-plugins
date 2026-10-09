"""Reproduce the lifecycle model's counterexamples against the real `kg` and server.

Every case runs the real kg.py and the real server in a sandbox: its own HOME,
port, state and storage directories, so no live server is touched. Stubbed,
and only to decide WHEN something happens:
  - the start window: kg's 15 s (20 s for the systemd unit) is cut to 0.3 s,
    standing in for a machine on which the server takes longer than that;
  - systemctl/journalctl: shell scripts on PATH, as tests/test_kg_cli.py does;
  - lsof/fuser: left off PATH in `timeout-nolsof`;
  - the pid a process gets: /proc/sys/kernel/ns_last_pid (needs root on Linux;
    otherwise the pid file is written as pid reuse leaves it, and the case says so).

Cases (python3 repro_lifecycle.py <case>; no argument runs them all):
  timeout         X1  a start that gives up leaves its server running, untracked;
                      it comes up under a breadcrumb that no later start clears;
                      after a crash the hook reports the stale failure, skips the start
  timeout-nolsof  X1  the same without lsof/fuser: kg stop cannot see that server,
                      kg start then blames "another program" on the port
  unit-timeout    X1  systemd: the unit's server comes up after kg gave up: stale breadcrumb
  pid-reuse       X2  a stale pid file whose pid now belongs to a server on another
                      port: kg start says "already running", kg stop kills that server
  stop-race       X3  kg start during kg stop: "already running" for a server on its
                      way out; when the stop ends nothing serves
Prints BUG lines while a finding is open, PASS once it is fixed.
"""
import fcntl, json, os, signal, socket, subprocess, sys, tempfile, time
import urllib.request
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3] / "knowledge-graph"
KG = PLUGIN / "cli" / "kg.py"
SERVER = PLUGIN / "server" / "mcp_streamable_server.py"
WINDOW = 0.3
results = []


def verdict(ok, good, bad):
    results.append(ok)
    print(("   ok: " + good) if ok else ("   BUG: " + bad))


def first(out):
    """First line of kg's output, without the sandbox log path."""
    return (out.stdout.strip().splitlines() or [""])[0].split(" Logs:")[0]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Box:
    def __init__(self, port=None, home=None, storage="storage"):
        self.home = Path(home or tempfile.mkdtemp(prefix="kg-life-"))
        self.port = port or free_port()
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin",
                    "KG_HTTP_PORT": str(self.port), "KG_STORAGE_ROOT": str(self.home / storage),
                    "XDG_STATE_HOME": str(self.home / ".local/state"),
                    "KG_AUTOCOMMIT_INTERVAL": "0", "KG_CHORES": "0"}
        self.state = self.home / f".local/state/knowledge-graph/port-{self.port}"
        self.crumb = self.state / "last_start_error"
        self.pidfile = self.state / "server.pid"

    def kg(self, *args, timeout=60):
        return subprocess.run([sys.executable, str(KG), *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def slow_start(self, window=WINDOW):
        """`kg start` on a machine where the server needs longer than kg waits."""
        code = (f"import sys; sys.path.insert(0, {str(KG.parent)!r}); import kg\n"
                "real = kg.wait\n"
                f"kg.wait = lambda pred, s: real(pred, {window} if s in (15, 20) else s)\n"
                "sys.exit(kg.start())\n")
        return subprocess.run([sys.executable, "-c", code], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def health(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def wait_health(self, up=True, seconds=20):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if (self.health() is not None) == up:
                return True
            time.sleep(0.1)
        return False

    def crumb_gone(self, seconds=3):
        """Gone within a moment of the server binding (when the server removes it)."""
        end = time.monotonic() + seconds
        while self.crumb.exists() and time.monotonic() < end:
            time.sleep(0.05)
        return not self.crumb.exists()

    def server_pids(self):
        out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
        mine = []
        for line in out.splitlines():
            pid, _, args = line.strip().partition(" ")
            if str(SERVER) in args and Path(f"/proc/{pid}/environ").exists():
                try:
                    env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
                except OSError:
                    continue
                if f"KG_HTTP_PORT={self.port}".encode() in env:
                    mine.append(int(pid))
        return mine

    def cleanup(self):
        self.kg("stop")
        for pid in self.server_pids():
            os.kill(pid, signal.SIGKILL)

    def hook(self):
        """The real SessionStart hook, with kg on PATH as installed."""
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "kg").write_text(f"#!/bin/sh\nexec {sys.executable} {KG} \"$@\"\n")
        (bin_dir / "kg").chmod(0o755)
        project = self.home / "project"
        project.mkdir(exist_ok=True)
        return subprocess.run(["bash", str(PLUGIN / "hooks/kg-autostart.sh")],
                              input=json.dumps({"cwd": str(project)}),
                              env=self.env, capture_output=True, text=True, timeout=60).stdout


def case_timeout():
    print("== timeout: a start that gives up after its window (X1)")
    box = Box()
    try:
        out = box.slow_start()
        print(f"   slow kg start: rc={out.returncode}: {first(out)}")
        print(f"   pid file kept: {box.pidfile.exists()}; breadcrumb: {box.crumb.exists()}")
        up = box.wait_health(True)
        print(f"   its server answers /health {'afterwards' if up else 'never'} (pids {box.server_pids()})")
        verdict(not up or box.crumb_gone(),
                "the breadcrumb is gone once the server serves",
                "the server serves under a breadcrumb that says its start failed")
        again = box.kg("start")
        print(f"   kg start again: rc={again.returncode}: {first(again)}")
        verdict(not box.crumb.exists(), "no breadcrumb after a start that found the server running",
                "kg start reported success; the failure breadcrumb stays")
        for pid in box.server_pids():   # the server crashes; the next session opens
            os.kill(pid, signal.SIGKILL)
        box.wait_health(False)
        hook = box.hook()
        said = "a preload (it started the server)" if "PRELOADED" in hook else hook[:110].strip() + "..."
        print(f"   after a crash, the SessionStart hook gives: {said}")
        verdict("PRELOADED" in hook,
                "the hook started the server and preloaded",
                "the hook reports the stale failure, starts nothing, skips the preload")
    finally:
        box.cleanup()


def case_timeout_nolsof():
    print("== timeout-nolsof: the same without lsof/fuser on PATH (X1)")
    box = Box()
    bin_dir = box.home / "bin"
    bin_dir.mkdir()
    os.symlink(subprocess.run(["sh", "-c", "command -v ps"], capture_output=True, text=True).stdout.strip(),
               bin_dir / "ps")
    box.env["PATH"] = str(bin_dir)
    try:
        out = box.slow_start()
        print(f"   slow kg start: rc={out.returncode}")
        up = box.wait_health(True)
        print(f"   its server answers /health: {up}")
        stop = box.kg("stop")
        print(f"   kg stop: {stop.stdout.strip().splitlines()[-1]}")
        gone = box.wait_health(False, 8)
        verdict(gone, "kg stop stopped it", "kg stop does not see the server it started; it still serves")
        if not gone:
            start = box.kg("start")
            print(f"   kg start: rc={start.returncode}: {first(start)}")
            verdict(start.returncode == 0, "kg start finds it",
                    "kg start blames another program for its own server")
    finally:
        box.env["PATH"] = "/usr/bin:/bin"
        box.cleanup()


def case_unit_timeout():
    print("== unit-timeout: the systemd unit's server comes up after kg stopped waiting (X1)")
    box = Box()
    bin_dir = box.home / "bin"
    bin_dir.mkdir()
    started = box.home / "unit.pid"
    # `systemctl start` starts `kg serve` in the background and returns, as systemd does.
    systemctl = ('case "$2" in is-enabled) echo enabled;; '
                 f'show) cat "{started}" 2>/dev/null;; '
                 f'start) setsid {sys.executable} {KG} serve >/dev/null 2>&1 & echo $! > "{started}";; '
                 'esac')
    for name, body in (("systemctl", systemctl), ("journalctl", "echo journal")):
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    box.env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    unit = box.home / ".config/systemd/user/kg-memory.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f'[Service]\nEnvironment="KG_HTTP_PORT={box.port}"\n')
    try:
        out = box.slow_start()
        print(f"   slow kg start (unit): rc={out.returncode}: {first(out)}")
        up = box.wait_health(True)
        gone = box.crumb_gone()
        print(f"   the unit's server answers /health: {up}; breadcrumb: {not gone}")
        verdict(not up or gone, "the breadcrumb is gone once the server serves",
                "the unit's server serves under a breadcrumb that says its start failed")
    finally:
        for pid in box.server_pids():
            os.kill(pid, signal.SIGKILL)


def spawn_with_pid(want, env):
    """Start a server process that gets pid `want`, as pid reuse would give it."""
    try:
        Path("/proc/sys/kernel/ns_last_pid").write_text(str(want - 1))
        proc = subprocess.Popen([sys.executable, str(SERVER)], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if proc.pid == want:
            return proc, "the kernel reused the pid"
        proc.kill()
    except OSError:
        pass
    proc = subprocess.Popen([sys.executable, str(SERVER)], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return proc, "pid reuse not available here: pid file rewritten as reuse would leave it"


def case_pid_reuse():
    print("== pid-reuse: a stale pid file naming a server on another port (X2)")
    a = Box()
    b = Box(home=a.home, storage="storage-b")
    try:
        assert a.kg("start").returncode == 0
        old = int(a.pidfile.read_text())
        os.kill(old, signal.SIGKILL)       # our server crashes; its pid file stays
        a.wait_health(False)
        time.sleep(2.5)                    # pid reuse comes later than the file write
        proc, how = spawn_with_pid(old, b.env)
        if proc.pid != old:
            a.pidfile.write_text(str(proc.pid))
        b.wait_health(True)
        print(f"   port {b.port}'s server runs as pid {proc.pid} ({how}); port {a.port}'s pid file says {a.pidfile.read_text()}")
        start = a.kg("start")
        print(f"   kg start (port {a.port}): rc={start.returncode}: {first(start)}")
        verdict(a.wait_health(True, 5), f"port {a.port} serves",
                f"kg start reported success and nothing serves port {a.port}")
        stop = a.kg("stop")
        print(f"   kg stop (port {a.port}): {' / '.join(stop.stdout.strip().splitlines())}")
        alive_b = proc.poll() is None and b.health() is not None
        verdict(alive_b, f"port {b.port}'s server untouched", f"kg stop on port {a.port} killed port {b.port}'s server")
    finally:
        a.cleanup()
        b.cleanup()


def case_stop_race():
    print("== stop-race: kg start while kg stop waits for the server to exit (X3)")
    box = Box()
    try:
        assert box.kg("start").returncode == 0
        # A request still in flight (here: its body not fully sent) keeps the
        # graceful shutdown waiting, as any long call does.
        held = socket.create_connection(("127.0.0.1", box.port))
        held.sendall(b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                     b"Accept: application/json, text/event-stream\r\nContent-Length: 1000\r\n\r\n{\"jsonrpc\"")
        time.sleep(0.5)
        t0 = time.monotonic()
        stopper = subprocess.Popen([sys.executable, str(KG), "stop"], env=box.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        box.wait_health(False, 5)
        t = time.monotonic()
        start = box.kg("start")
        print(f"   kg start during the stop: rc={start.returncode} after {time.monotonic() - t:.1f}s: "
              f"{first(start)}")
        said = ' / '.join(stopper.communicate(timeout=60)[0].strip().splitlines())
        print(f"   kg stop ({time.monotonic() - t0:.1f}s, SIGKILL after 6 s): {said}")
        held.close()
        served = box.wait_health(True, 5)
        verdict(not (start.returncode == 0 and not served), "after both, the server serves",
                "kg start reported success; once the stop ended nothing serves")
    finally:
        box.cleanup()


CASES = {"timeout": case_timeout, "timeout-nolsof": case_timeout_nolsof,
         "unit-timeout": case_unit_timeout, "pid-reuse": case_pid_reuse, "stop-race": case_stop_race}

if __name__ == "__main__":
    for name in (sys.argv[1:] or CASES):
        CASES[name]()
    print("RESULT:", "PASS" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
