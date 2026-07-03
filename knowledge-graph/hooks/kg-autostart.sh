#!/usr/bin/env bash
# SessionStart hook: make sure the KG memory server is up — and when it is,
# preload the session's memory.
#
# If the server answers /health, fetch the compact-core preload from
# /api/session_bootstrap and inject it as additionalContext: the top-scored
# gists are in context at turn 1, zero tool calls. Hook context stays inline
# only up to ~10K chars (measured — beyond that the harness persists it to a
# file the model sees a 2KB preview of), so the preload is a capped core and
# the loud kg_read renders the rest without repeating it. A systemMessage
# one-liner makes the preload visible to the user — memory loading should
# never be silent. If anything about the fetch fails, stay quiet; the
# kg-core skill's classic kg_read path is the fallback.
#
# If the server is down, kick off manage_server.sh start IN THE BACKGROUND
# (first run builds the Python venv, ~1 min — session start must not block on
# that) and tell Claude what's happening so a "connection refused" on the
# first kg_read is understood as "warming up, retry", not "broken".
#
# This hook only ever STARTS the server — never stops or restarts one the
# user is running.

PORT="${KG_HTTP_PORT:-8765}"
HOST="${KG_HTTP_HOST:-127.0.0.1}"

if curl -sf --max-time 2 "http://${HOST}:${PORT}/health" > /dev/null 2>&1; then
    python3 - "${CLAUDE_PROJECT_DIR:-$PWD}" "http://${HOST}:${PORT}" <<'PYEOF'
import json, sys, urllib.parse, urllib.request

cwd, base = sys.argv[1], sys.argv[2]
try:
    url = f"{base}/api/session_bootstrap?project_path={urllib.parse.quote(cwd)}"
    with urllib.request.urlopen(url, timeout=4) as resp:
        data = json.loads(resp.read())
    # New servers return the final injectable text (header included, budgeted
    # as one piece). Older servers return only the graph body — compose the
    # legacy header so a version-skewed pair still preloads.
    context = data.get("context") or (
        "KG MEMORY PRELOADED — the knowledge graph below is already in context; "
        "do NOT call kg_read for the full graph. session_id: "
        + data["session_id"]
        + " (pass it to every kg_* call). For node depth use "
        "kg_read(session_id, ids=[...]); for lookups use kg_search. "
        'Announce "I have recalled KG Memories" after scanning both sections.\n\n'
        + data["text"]
    )
    stats = data.get("stats") or {}
    if stats:
        message = (
            f"KG memory preloaded (session {data['session_id']}): "
            f"{stats.get('user_active', '?')} user + {stats.get('project_active', '?')} project "
            f"active nodes, {stats.get('shown_gists', '?')} gists inline — kg_read renders the rest."
        )
    else:
        message = f"KG memory preloaded (session {data['session_id']})."
    print(json.dumps({
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        },
    }))
except Exception:
    pass  # silent miss — kg_read remains the fallback path
PYEOF
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
