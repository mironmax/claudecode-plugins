"""kg editor — the visual graph editor in the browser.

Starts the editor (and the memory server it reads from) when they are down,
then prints its address and, in a terminal, opens it.
"""

import os
import signal
import subprocess
import sys
import urllib.request
import webbrowser

try:
    from . import kg
except ImportError:  # run as a script from a checkout
    import kg

EDITOR_SCRIPT = kg.ROOT / "visual-editor" / "backend" / "server.py"
PORT = int(os.environ.get("EDITOR_PORT", "8766"))
URL = f"http://localhost:{PORT}"
LOG_FILE = kg.STATE_DIR / "visual_editor.log"
PID_FILE = kg.STATE_DIR / "editor.pid"


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2):
            return True
    except Exception:
        return False


def editor_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text())
    except (OSError, ValueError):
        return None
    return pid if kg.alive(pid) and str(EDITOR_SCRIPT) in kg.process_args(pid) else None


def start() -> int:
    if kg.health() is None and kg.start():
        return 1
    if not healthy():
        kg.STATE_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, MCP_SERVER_URL=f"http://{kg.HOST}:{kg.PORT}")
        with open(LOG_FILE, "wb") as log:
            proc = subprocess.Popen([sys.executable, str(EDITOR_SCRIPT)], env=env,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    start_new_session=True)
        PID_FILE.write_text(str(proc.pid))
        if not (kg.wait(lambda: proc.poll() is not None or healthy(), 15) and proc.poll() is None):
            PID_FILE.unlink(missing_ok=True)
            print(f"The editor did not start. See {LOG_FILE}")
            return 1
    print(f"Editor: {URL}")
    if sys.stdout.isatty():
        webbrowser.open(URL)
    return 0


def stop() -> int:
    pid = editor_pid()
    if not pid:
        PID_FILE.unlink(missing_ok=True)
        print("The editor is not running.")
        return 0
    os.kill(pid, signal.SIGTERM)
    if not kg.wait(lambda: not kg.alive(pid), 5):
        os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    print("Editor stopped.")
    return 0


def run(action: str) -> int:
    return stop() if action == "stop" else start()
