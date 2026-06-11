#!/usr/bin/env bash
# SessionStart hook: make sure the KG memory server is up.
#
# If the server already answers /health, stay silent and add nothing to the
# session. If it's down, kick off manage_server.sh start IN THE BACKGROUND
# (first run builds the Python venv, ~1 min — session start must not block on
# that) and tell Claude what's happening so a "connection refused" on the
# first kg_read is understood as "warming up, retry", not "broken".
#
# This hook only ever STARTS the server — never stops or restarts one the
# user is running.

PORT="${KG_HTTP_PORT:-8765}"
HOST="${KG_HTTP_HOST:-127.0.0.1}"

if curl -sf --max-time 2 "http://${HOST}:${PORT}/health" > /dev/null 2>&1; then
    exit 0
fi

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGE="$HOOK_DIR/../server/manage_server.sh"

if [ ! -f "$MANAGE" ]; then
    echo "KG memory server is not running and its start script was not found — start it manually (see plugin docs)."
    exit 0
fi

nohup bash "$MANAGE" start > /dev/null 2>&1 &
disown 2>/dev/null

echo "KG memory server was down — starting it in the background now (a first run sets up its Python environment, ~1 min). Because it was down when this session connected, the kg_* MCP tools are likely offline for this session. When that is the case: (1) verify the server is up with \`curl -sf http://${HOST}:${PORT}/health\` (retry until it responds), then (2) tell the user to run /mcp, select plugin:knowledge-graph:kg, and hit Reconnect — only the user can do this step. After reconnect, call kg_read as usual."
exit 0
