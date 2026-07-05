#!/usr/bin/env python3
"""Register the knowledge-graph MCP server in Claude Desktop.

Desktop's connector UI accepts only public https URLs — those connections
originate from Anthropic's cloud and can never reach a local server. Local
servers go into claude_desktop_config.json as stdio commands instead. This
script writes that entry, pointing Desktop at desktop_bridge.sh, which
auto-starts the shared HTTP server and proxies stdio to it via mcp-remote.

All paths are resolved absolute here: Desktop spawns commands without a
shell, so `~` and $PATH are not reliably expanded.

Usage: python3 setup_desktop.py [--port 8765] [--remove]
"""

import argparse
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

SERVER_KEY = "knowledge-graph"


def config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            sys.exit("APPDATA is not set — cannot locate the Claude Desktop config")
        return Path(appdata) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def find_npx() -> str | None:
    found = shutil.which("npx")
    if found:
        return found
    candidates = [
        Path.home() / ".npm/bin/npx",
        Path("/opt/homebrew/bin/npx"),
        Path("/usr/local/bin/npx"),
        Path("/usr/bin/npx"),
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def build_entry(port: int) -> dict:
    plugin_dir = Path(__file__).resolve().parent
    npx = find_npx()
    if not npx:
        sys.exit(
            "npx not found — the Desktop bridge runs on mcp-remote, which needs "
            "Node.js >= 18. Install Node, then rerun this script."
        )
    url = f"http://127.0.0.1:{port}/"
    if platform.system() == "Windows":
        # No bash on a stock Windows install: skip the auto-start wrapper and
        # bridge directly. The server must be started by a Claude Code session
        # (or manually) before Desktop connects.
        entry = {"command": npx, "args": ["-y", "mcp-remote", url, "--allow-http"]}
    else:
        bridge = plugin_dir / "desktop_bridge.sh"
        bridge.chmod(bridge.stat().st_mode | 0o111)
        # Plugin cache dirs are versioned — a direct path dies on every plugin
        # update. Point the config at a stable ~/.local/bin symlink instead
        # (same pattern as kg-memory/kg-visual); setup and install_command.sh
        # both refresh it.
        link = Path.home() / ".local/bin/kg-desktop-bridge"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(bridge)
        entry = {"command": str(link), "args": [npx]}
    if port != 8765:
        entry["env"] = {"KG_HTTP_PORT": str(port)}
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("KG_HTTP_PORT", "8765")))
    parser.add_argument("--remove", action="store_true", help="remove the entry instead of adding it")
    args = parser.parse_args()

    cfg_file = config_path()
    if not cfg_file.parent.is_dir():
        sys.exit(f"Claude Desktop does not look installed — missing {cfg_file.parent}")

    data = {}
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text())
        except json.JSONDecodeError as exc:
            sys.exit(f"{cfg_file} is not valid JSON ({exc}) — fix or remove it, then rerun")

    servers = data.setdefault("mcpServers", {})

    if args.remove:
        if SERVER_KEY not in servers:
            print(f"'{SERVER_KEY}' is not configured in {cfg_file} — nothing to remove.")
            return
        del servers[SERVER_KEY]
        if not servers:
            del data["mcpServers"]
    else:
        entry = build_entry(args.port)
        if servers.get(SERVER_KEY) == entry:
            print(f"'{SERVER_KEY}' is already configured in {cfg_file} — nothing to do.")
            return
        servers[SERVER_KEY] = entry

    if cfg_file.exists():
        backup = cfg_file.with_name(cfg_file.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(cfg_file, backup)
        print(f"Backed up existing config to {backup}")

    cfg_file.write_text(json.dumps(data, indent=2) + "\n")
    action = "removed from" if args.remove else "written to"
    print(f"'{SERVER_KEY}' {action} {cfg_file}")
    if not args.remove:
        print("Fully quit Claude Desktop (not just the window) and reopen it —")
        print("the knowledge-graph tools appear once Desktop respawns its MCP servers.")


if __name__ == "__main__":
    main()
