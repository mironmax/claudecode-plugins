#!/bin/bash
# stdio↔HTTP bridge for Claude Desktop.
#
# Claude Desktop spawns local MCP servers over stdio; the knowledge-graph
# server is shared HTTP by design (one server, many clients). This wrapper
# makes the two meet: ensure the HTTP server is up (auto-starting it exactly
# like the Claude Code hook does), then exec mcp-remote to proxy stdio to it.
#
# $1 (optional): absolute path to npx. Written into the Desktop config by
# setup_desktop.py because Desktop spawns commands without a login shell —
# its PATH may lack npm's bin directories.

set -u
# readlink -f: this script is reached via a stable ~/.local/bin symlink so the
# Desktop config survives plugin updates — resolve to the real plugin dir.
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PORT="${KG_HTTP_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/"

NPX="${1:-}"
[ -n "$NPX" ] || NPX="$(command -v npx || true)"
for candidate in "$HOME/.npm/bin/npx" /opt/homebrew/bin/npx /usr/local/bin/npx /usr/bin/npx; do
    [ -n "$NPX" ] && break
    [ -x "$candidate" ] && NPX="$candidate"
done
if [ -z "$NPX" ]; then
    echo "knowledge-graph desktop bridge: npx not found — install Node.js >= 18" >&2
    exit 1
fi

# Desktop spawns us with a GUI-minimal PATH; npx's children need node and the
# mcp-remote shim resolvable, so rebuild PATH around the npx we were given.
export PATH="$(dirname "$NPX"):/usr/local/bin:/usr/bin:/bin${PATH:+:$PATH}"

# stdout belongs to the MCP stdio protocol — everything else goes to stderr.
if ! curl -sf -m 2 "${URL}health" > /dev/null 2>&1; then
    "$DIR/server/manage_server.sh" start 1>&2 || true
fi

exec "$NPX" -y mcp-remote "$URL" --allow-http
