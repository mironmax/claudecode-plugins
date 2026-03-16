#!/usr/bin/env bash
# install.sh — One-time setup for the scheduler plugin.
# Creates venv, installs dependencies, ensures directories exist.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"
SCHEDULER_DIR="$HOME/.claude/scheduler"
SYSTEMD_DIR="$HOME/.config/systemd/user"

echo "=== Scheduler Plugin Setup ==="

# ---------------------------------------------------------------------------
# 1. Python venv + dependencies
# ---------------------------------------------------------------------------

if [ ! -d "$SERVER_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SERVER_DIR/venv"
fi

echo "Installing dependencies..."
"$SERVER_DIR/venv/bin/pip" install -q -r "$SERVER_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 2. Directory structure
# ---------------------------------------------------------------------------

mkdir -p "$SCHEDULER_DIR" "$SYSTEMD_DIR"

# Initialize jobs.json if absent
if [ ! -f "$SCHEDULER_DIR/jobs.json" ]; then
    echo "[]" > "$SCHEDULER_DIR/jobs.json"
    echo "Created $SCHEDULER_DIR/jobs.json"
fi

# ---------------------------------------------------------------------------
# 3. Permissions
# ---------------------------------------------------------------------------

chmod +x "$SCRIPT_DIR/launcher.sh"
chmod +x "$SCRIPT_DIR/hooks/usage_monitor.sh"

# ---------------------------------------------------------------------------
# 4. Verify systemd user session
# ---------------------------------------------------------------------------

if ! systemctl --user status >/dev/null 2>&1; then
    echo ""
    echo "WARNING: systemd user session not available."
    echo "Ensure 'loginctl enable-linger $(whoami)' is set for timers to work when logged out."
fi

echo ""
echo "Setup complete."
echo ""
echo "Setup complete."
echo ""
echo "Next steps:"
echo ""
echo "  1. Add this MCP server to your Claude settings:"
echo "     claude mcp add scheduler -- $SERVER_DIR/venv/bin/python $SERVER_DIR/mcp_stdio_server.py"
echo ""
echo "  2. Add the usage monitor hook to ~/.claude/settings.json:"
echo "     (see hooks section in README.md for the JSON snippet)"
echo ""
echo "  3. (Optional) Copy CLAUDE.md template to your config:"
echo "     cat $SCRIPT_DIR/templates/CLAUDE.md >> ~/.claude/CLAUDE.md"
echo ""
echo "  4. (Optional) Configure preferred terminal:"
echo "     echo '{\"terminal\": \"kitty\"}' > $SCHEDULER_DIR/config.json"
