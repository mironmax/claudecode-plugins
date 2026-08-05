#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.34 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0934.py

Covers the venv self-heal work driven by the mcp 2.0.0 outage (2026-07-28):
  1. The dependency pin is bounded, and what is installed satisfies it
  2. The mcp surface this server is built on is present — Server carries both
     list_tools and call_tool
  3. create_mcp_server() returns without raising. This is the real tripwire:
     it fails on ANY future API removal, not just the decorators, and it is
     the same check manage_server.sh runs before latching the deps marker
  4. The deps marker is content-addressed — it holds the sha256 of
     requirements.txt, so a changed pin re-runs pip instead of latching forever
  5. The session-start hook reports a recorded start failure instead of
     claiming the environment is warming up

Read-only against the live checkout except for a breadcrumb file, which is
written to a temp copy of the hook's tree, never to server/.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SERVER_DIR = Path(__file__).resolve().parent.parent
PLUGIN_DIR = SERVER_DIR.parent

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


def _mcp_spec_line():
    for line in (SERVER_DIR / "requirements.txt").read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("mcp") and not stripped.startswith("#"):
            return stripped
    return ""


def _ver(text):
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3])


def main():
    print("=== v0.9.34 venv self-heal + preflight tests ===")

    # ==================================================================
    # 1. The pin is bounded and satisfied
    # ==================================================================
    print("dependency pin:")
    spec = _mcp_spec_line()
    check("requirements.txt declares mcp", spec.startswith("mcp"), spec)
    check("mcp pin carries an upper bound", "<" in spec, spec)

    from importlib.metadata import version
    installed = version("mcp")
    lower = re.search(r">=\s*([0-9.]+)", spec)
    upper = re.search(r"<\s*([0-9.]+)", spec)
    check(
        "installed mcp satisfies the declared range",
        bool(lower) and bool(upper)
        and _ver(lower.group(1)) <= _ver(installed) < _ver(upper.group(1)),
        f"installed={installed} spec={spec}",
    )

    # ==================================================================
    # 2-3. The surface exists, and the wiring actually builds
    # ==================================================================
    print("mcp surface:")
    from mcp.server import Server
    check("Server exposes list_tools", hasattr(Server, "list_tools"))
    check("Server exposes call_tool", hasattr(Server, "call_tool"))

    import mcp_streamable_server as mss
    built = None
    try:
        built = mss.create_mcp_server()
        raised = None
    except BaseException as exc:  # SystemExit included — preflight exits non-zero
        raised = exc
    check("create_mcp_server() returns without raising", raised is None, repr(raised))
    check("built server is an mcp Server", isinstance(built, Server) if built else False)

    print("preflight helpers:")
    check("requirement is read from requirements.txt", mss._mcp_requirement() == spec,
          mss._mcp_requirement())
    check("preflight is silent on a healthy surface",
          mss._preflight_mcp_surface() is None)

    # ==================================================================
    # 4. The deps marker is content-addressed
    # ==================================================================
    print("deps marker:")
    marker = SERVER_DIR / "venv" / ".deps_ok"
    want = hashlib.sha256((SERVER_DIR / "requirements.txt").read_bytes()).hexdigest()
    if not marker.exists():
        print("  info marker absent — next start builds the venv and writes it")
    elif marker.read_text().strip() == want:
        check("marker holds the current requirements.txt hash", True)
    else:
        # Not a failure: a marker that does not match is precisely what makes
        # the next start re-resolve. Pre-0.9.34 markers are empty touch files,
        # so every existing install self-heals exactly once.
        print("  info marker is stale/legacy — next start re-resolves (the fix working)")

    manage = (SERVER_DIR / "manage_server.sh").read_text()
    check("ensure_venv compares the stored hash", 'cat "$DEPS_MARKER"' in manage)
    check("marker is written with the hash, not touched",
          'printf \'%s\\n\' "$want" > "$DEPS_MARKER"' in manage)
    check("install is smoke-tested before the marker is written",
          manage.index("venv_smoke") < manage.index('> "$DEPS_MARKER"'))

    # A healthy /health proves only that SOMETHING answers the port. Observed
    # 2026-08-05: a stale PID file left the real server listening, the new
    # process died on bind, and restart reported success while the server kept
    # running months-old code.
    print("start/restart honesty:")
    check("start requires the launched process to be alive",
          'wait_healthy 10 && ps -p "$launched"' in manage)
    check("start names a port collision as the cause",
          "already served by another process" in manage)
    check("restart falls back to stopping by port",
          "if ! wait_port_free 5; then" in manage and "stop_port" in manage)

    # ==================================================================
    # 5. The hook tells the truth about a recorded failure
    # ==================================================================
    print("session-start hook:")
    tmp = tempfile.mkdtemp(prefix="kg-test-hook-")
    try:
        shutil.copytree(PLUGIN_DIR / "hooks", Path(tmp) / "hooks")
        (Path(tmp) / "server").mkdir()
        shutil.copy(SERVER_DIR / "manage_server.sh", Path(tmp) / "server")
        (Path(tmp) / "server" / ".last_start_error").write_text(
            "when: 2026-08-05 12:00:00\n"
            "cause: installed mcp 2.0.0 is incompatible with this server\n"
            "log: /tmp/kg-test.log\n"
        )
        env = dict(os.environ, KG_HTTP_PORT="8399")  # nothing listens here
        out = subprocess.run(
            ["bash", str(Path(tmp) / "hooks" / "kg-autostart.sh")],
            input="", capture_output=True, text=True, timeout=30, env=env,
        ).stdout
        check("reports the recorded cause", "incompatible with this server" in out, out[:120])
        check("takes the failure branch", "last start attempt FAILED" in out, out[:120])
        # The warming-up SENTENCE must be gone. The failure text may still name
        # "/mcp Reconnect" — it does, inside an instruction not to offer it —
        # so match the claim itself, not the words it warns about.
        check("does not claim the environment is warming up",
              "starting it in the background now" not in out
              and "sets up its Python environment" not in out, out[:120])
        check("surfaces the log path", "/tmp/kg-test.log" in out, out[:120])
        check("does not spawn a start attempt",
              "warming up, retry" not in out, out[:120])

        # No breadcrumb → the original warming-up path must survive untouched.
        # MANAGE is removed first so the hook cannot spawn a real server.
        (Path(tmp) / "server" / ".last_start_error").unlink()
        (Path(tmp) / "server" / "manage_server.sh").unlink()
        out2 = subprocess.run(
            ["bash", str(Path(tmp) / "hooks" / "kg-autostart.sh")],
            input="", capture_output=True, text=True, timeout=30, env=env,
        ).stdout
        check("without a breadcrumb the failure text is gone",
              "last start attempt FAILED" not in out2, out2[:120])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
