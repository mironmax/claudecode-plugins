#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.34 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0934.py

Covers the dependency guards added after the mcp 2.0.0 outage (2026-07-28):
  1. The dependency pin is bounded, and what is installed satisfies it
  2. The mcp surface this server is built on is present — Server accepts the
     on_list_tools and on_call_tool constructor callbacks (mcp 2.x)
  3. create_mcp_server() returns without raising. This is the real tripwire:
     it fails on ANY future API removal, not just the decorators

Read-only against the live checkout.
"""

import os
import re
import sys
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
    import inspect
    from mcp.server import Server
    init_params = inspect.signature(Server.__init__).parameters
    check("Server accepts on_list_tools", "on_list_tools" in init_params)
    check("Server accepts on_call_tool", "on_call_tool" in init_params)

    import mcp_streamable_server as mss
    built = None
    try:
        built = mss.create_mcp_server()
        raised = None
    except BaseException as exc:  # SystemExit included — preflight exits non-zero
        raised = exc
    check("create_mcp_server() returns without raising", raised is None, repr(raised))
    check("built server is an mcp Server", isinstance(built, Server) if built else False)

    print("tool handlers:")
    import asyncio
    from mcp import types
    listed = asyncio.run(built.get_request_handler("tools/list").handler(None, None))
    check("all ten tools listed", len(listed.tools) == 10 and listed.tools[0].name == "kg_read",
          [t.name for t in listed.tools])
    call = built.get_request_handler("tools/call").handler
    bad = asyncio.run(call(None, types.CallToolRequestParams(name="kg_useful", arguments={"ids": []})))
    check("arguments are validated against the tool schema, as mcp 1.x did",
          bad.is_error and "session_id" in bad.content[0].text, bad)
    unknown = asyncio.run(call(None, types.CallToolRequestParams(name="kg_nope", arguments={})))
    check("an unknown tool is answered, not raised",
          not unknown.is_error and "Unknown tool" in unknown.content[0].text, unknown)

    print("preflight helpers:")
    check("requirement is read from requirements.txt", mss._mcp_requirement() == spec,
          mss._mcp_requirement())
    check("preflight is silent on a healthy surface",
          mss._preflight_mcp_surface() is None)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
