"""kg — the one command for the knowledge-graph memory.

Runs the shared memory server for every harness from one installed copy, and
checks the setup around it. Lives at kg_memory/cli/kg.py in the installed
package and at knowledge-graph/cli/kg.py in a checkout; both resolve the
server relative to this file.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = ROOT / "server" / "mcp_streamable_server.py"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "knowledge-graph"
LOG_FILE = STATE_DIR / "mcp_server.log"
PID_FILE = STATE_DIR / "server.pid"
# The SessionStart hook starts the server in the background and never sees the
# outcome; this file is how the next session learns why a start failed.
BREADCRUMB = STATE_DIR / "last_start_error"
STORAGE_ROOT = Path(os.environ.get("KG_STORAGE_ROOT") or Path.home() / ".knowledge-graph")
HOST = os.environ.get("KG_HTTP_HOST", "127.0.0.1")
PORT = int(os.environ.get("KG_HTTP_PORT", "8765"))
# Matches legacy servers started from a plugin cache too, so stop/restart can
# take over from them.
SERVER_MARK = "mcp_streamable_server"


def version() -> str:
    text = (ROOT / "server" / "version.py").read_text()
    return text.split('__version__ = "', 1)[1].split('"', 1)[0]


def health() -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=2) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def process_args(pid: int) -> str:
    out = subprocess.run(["ps", "-p", str(pid), "-o", "args="],
                         capture_output=True, text=True)
    return out.stdout.strip()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_owners() -> list[int]:
    """PIDs listening on the port that are our server, by lsof or fuser."""
    for cmd in (["lsof", "-ti", f"tcp:{PORT}", "-sTCP:LISTEN"], ["fuser", f"{PORT}/tcp"]):
        if not shutil.which(cmd[0]):
            continue
        out = subprocess.run(cmd, capture_output=True, text=True)
        pids = [int(p) for p in (out.stdout + " " + out.stderr).replace(":", " ").split()
                if p.isdigit() and int(p) != PORT]
        return [p for p in pids if SERVER_MARK in process_args(p)]
    return []


def running_pid() -> int | None:
    """Our server's PID: the pid file when it is still ours, else the port owner."""
    try:
        pid = int(PID_FILE.read_text())
        if alive(pid) and SERVER_MARK in process_args(pid):
            return pid
    except (OSError, ValueError):
        pass
    owners = port_owners()
    return owners[0] if owners else None


def port_free() -> bool:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, PORT))
        except OSError:
            return False
    return True


def wait(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return predicate()


def write_breadcrumb(cause: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BREADCRUMB.write_text(f"when: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                          f"cause: {cause}\nlog: {LOG_FILE}\n")


def failure_cause() -> str:
    """One sentence from the log: the server's own preflight line, else the
    last exception line."""
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()[-40:]
    except OSError:
        lines = []
    for line in lines:
        if "KG PREFLIGHT:" in line:
            return line.split("KG PREFLIGHT:", 1)[1].strip()
    errors = [l for l in lines if l.split(":", 1)[0].endswith(("Error", "Exception"))]
    return errors[-1] if errors else "server did not answer /health within 15s"


UNIT = "kg-memory.service"


def service_enabled() -> bool:
    """A systemd user unit owns the server: lifecycle goes through systemctl,
    or a stop here would fight its supervisor."""
    if not shutil.which("systemctl"):
        return False
    try:   # another KG_HTTP_PORT means another server, which the unit does not own
        unit = (Path.home() / ".config/systemd/user" / UNIT).read_text()
    except OSError:
        return False
    unit_port = next((int(line.split("=")[-1].strip('"')) for line in unit.splitlines()
                      if line.startswith('Environment="KG_HTTP_PORT=')), 8765)
    if unit_port != PORT:
        return False
    out = subprocess.run(["systemctl", "--user", "is-enabled", UNIT], capture_output=True, text=True)
    return out.stdout.strip() == "enabled"


def systemctl(action: str) -> int:
    out = subprocess.run(["systemctl", "--user", action, UNIT], capture_output=True, text=True)
    if out.returncode:
        print(f"systemctl --user {action} {UNIT} failed: {out.stderr.strip()}")
        return 1
    if action != "stop" and not wait(lambda: health() is not None, 20):
        print(f"{UNIT} is {action}ed but the server does not answer: journalctl --user -u {UNIT}")
        return 1
    print(f"Server {action}ed by {UNIT} (version {version()}).")
    return 0


def stop_strays() -> None:
    """Servers this unit does not own (started by an older plugin or by hand)."""
    main = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value", UNIT],
                          capture_output=True, text=True).stdout.strip()
    for pid in port_owners():
        if str(pid) != main:
            os.kill(pid, signal.SIGTERM)
    wait(lambda: all(str(p) == main for p in port_owners()), 6)


def start() -> int:
    if service_enabled():
        stop_strays()
        return systemctl("start")
    pid = running_pid()
    if pid:
        print(f"Server already running (PID {pid}).")
        return 0
    if not port_free():
        cause = f"port {PORT} is held by another program (set KG_HTTP_PORT to use another)"
        write_breadcrumb(cause)
        print(f"Cannot start the server: {cause}.")
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "wb") as log:
        proc = subprocess.Popen([sys.executable, str(SERVER_SCRIPT)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True)
    PID_FILE.write_text(str(proc.pid))
    # Healthy is not proof that OUR process came up: an incumbent on the port
    # keeps answering while ours dies on bind. Require both.
    if wait(lambda: proc.poll() is not None or health() is not None, 15) and proc.poll() is None:
        BREADCRUMB.unlink(missing_ok=True)
        print(f"Server started (PID {proc.pid}, version {version()}). Logs: {LOG_FILE}")
        return 0
    cause = failure_cause()
    write_breadcrumb(cause)
    PID_FILE.unlink(missing_ok=True)
    print(f"Failed to start the server: {cause}\nSee {LOG_FILE}")
    return 1


def commit_storage() -> None:
    if not (STORAGE_ROOT / ".git").is_dir():
        return
    git = ["git", "-C", str(STORAGE_ROOT)]
    if not subprocess.run(git + ["status", "--porcelain"], capture_output=True, text=True).stdout:
        return
    subprocess.run(git + ["add", "-A"], capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", f"Auto-save {time.strftime('%Y-%m-%d %H:%M')}"],
                   capture_output=True)


def stop() -> int:
    if service_enabled():
        systemctl("stop")
    pids = {p for p in [running_pid(), *port_owners()] if p}
    if not pids:
        PID_FILE.unlink(missing_ok=True)
        print("Server is not running.")
        return 0
    for pid in pids:
        print(f"Stopping server (PID {pid})...")
        os.kill(pid, signal.SIGTERM)
    if not wait(lambda: not any(alive(p) for p in pids), 6):
        for pid in pids:
            if alive(pid) and SERVER_MARK in process_args(pid):
                os.kill(pid, signal.SIGKILL)
    wait(lambda: health() is None, 5)
    PID_FILE.unlink(missing_ok=True)
    commit_storage()
    print("Server stopped.")
    return 0


def restart() -> int:
    if service_enabled():
        stop_strays()
        return systemctl("restart")
    stop()
    return start()


def status() -> int:
    data = health()
    pid = running_pid()
    if not data:
        print("Server is not running.")
        if BREADCRUMB.exists():
            print(BREADCRUMB.read_text().strip())
        return 1
    print(f"Server is running: version {data.get('version')}, "
          f"{data.get('active_sessions')} sessions, PID {pid or 'unknown'}")
    if pid:
        print(f"  {process_args(pid)}")
    if data.get("version") != version():
        print(f"  This kg is {version()}: run `kg restart` to serve it.")
    return 0


def logs(follow: bool) -> int:
    if not LOG_FILE.exists():
        print(f"No log yet at {LOG_FILE}")
        return 1
    os.execvp("tail", ["tail", "-f" if follow else "-n50", str(LOG_FILE)])


def serve() -> int:
    """Foreground server, for a service manager."""
    os.execv(sys.executable, [sys.executable, str(SERVER_SCRIPT)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kg", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("start", "start the memory server in the background"),
                       ("stop", "stop the memory server"),
                       ("restart", "restart it on this kg's version"),
                       ("status", "is it running, which version, from where"),
                       ("serve", "run the server in the foreground (service managers)"),
                       ("doctor", "check every piece the memory depends on"),
                       ("version", "print this kg's version"),
                       ("commit", "commit the storage git repository now")):
        sub.add_parser(name, help=text)
    sub.add_parser("logs", help="show the server log").add_argument(
        "-f", "--follow", action="store_true", help="keep following it")
    setup = sub.add_parser("setup", help="set up every installed harness (asks before each change)")
    setup.add_argument("--yes", action="store_true", help="apply without asking")
    setup.add_argument("--only", help="comma-separated step keys, as `kg setup --plan` lists them")
    setup.add_argument("--plan", action="store_true", help="show what setup would do, change nothing")
    update = sub.add_parser("update", help="upgrade kg, then the server and every installed plugin")
    update.add_argument("--after-upgrade", action="store_true", help=argparse.SUPPRESS)
    remove = sub.add_parser("uninstall", help="reverse what setup did (your memory stays)")
    remove.add_argument("--yes", action="store_true", help="apply without asking")
    remove.add_argument("--plan", action="store_true", help="show what it would do, change nothing")
    gauge = sub.add_parser("gauge", help="Claude Code status line: record the quota gauge")
    gauge.add_argument("--wrap", help="your own status-line command, run unchanged after recording")
    args = parser.parse_args(argv)

    if args.command in ("doctor", "setup", "update", "uninstall"):
        try:
            from . import doctor
        except ImportError:  # run as a script from a checkout
            import doctor
        if args.command == "doctor":
            return doctor.run()
        if args.command == "update":
            return doctor.update(args.after_upgrade)
        if args.command == "uninstall":
            return doctor.uninstall(args.yes, args.plan)
        only = set(args.only.split(",")) if args.only else None
        return doctor.setup(args.yes, only, args.plan)
    if args.command == "gauge":
        try:
            from . import gauge
        except ImportError:
            import gauge
        return gauge.run(args.wrap)
    if args.command == "version":
        print(version())
        return 0
    if args.command == "commit":
        commit_storage()
        return 0
    if args.command == "logs":
        return logs(args.follow)
    return {"start": start, "stop": stop, "restart": restart,
            "status": status, "serve": serve}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
