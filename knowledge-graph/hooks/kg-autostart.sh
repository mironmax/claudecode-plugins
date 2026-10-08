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
# If the server is down, start it through `kg` first — seconds, since kg is
# installed with its environment — so even a cold start gets its preload.
# Without kg there is no server to start: ask the agent to offer the install.
#
# This hook only ever STARTS the server — never stops or restarts one the
# user is running.

# A maintenance chore runs headless inside this same plugin: it needs no
# preload (its prompt names its targets), no recall, and above all no chore
# dispatch of its own. The runner exports KG_CHORE=1 — stay silent.
[ -n "${KG_CHORE:-}" ] && exit 0

PORT="${KG_HTTP_PORT:-8765}"
HOST="${KG_HTTP_HOST:-127.0.0.1}"

# The hook's stdin JSON carries the Claude session identity (session_id,
# source, transcript_path) — the server binds the KG session to it so that
# resume/compact reuse the existing seen-state instead of minting a fresh
# session that re-nags and re-injects everything.
STDIN_JSON=$(cat 2>/dev/null)

healthy() { curl -sf --max-time 2 "http://${HOST}:${PORT}/health" > /dev/null 2>&1; }

if ! healthy; then
    KG_BIN="$(command -v kg 2>/dev/null)"
    [ -z "$KG_BIN" ] && [ -x "$HOME/.local/bin/kg" ] && KG_BIN="$HOME/.local/bin/kg"
    if [ -z "$KG_BIN" ]; then
        echo "KG memory is not set up on this machine: the knowledge-graph plugin needs the \`kg\` command, which runs the memory server for every harness. Offer to install it now. If the user agrees, follow the Install section of the kg-ops skill: \`uv tool install kg-memory\`, then \`kg setup --plan\`, applying only the items the user accepts. Memory loads from the next session; until then the kg_* tools are unavailable, so proceed without memory."
        exit 0
    fi
    # A start that failed said why, and fails the same way until fixed:
    # report it instead of retrying every session.
    STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/knowledge-graph"
    [ "$PORT" != 8765 ] && STATE_DIR="$STATE_DIR/port-$PORT"   # as kg keeps it
    BREADCRUMB="$STATE_DIR/last_start_error"
    if [ -f "$BREADCRUMB" ]; then
        CAUSE=$(grep -m1 '^cause: ' "$BREADCRUMB" 2>/dev/null | sed 's/^cause: //')
        WHEN=$(grep -m1 '^when: ' "$BREADCRUMB" 2>/dev/null | sed 's/^when: //')
        echo "KG memory server is DOWN and its last start attempt FAILED (${WHEN:-unknown time}): ${CAUSE:-cause not recorded}. It will fail the same way until fixed, so do not tell the user to wait or retry. Report the cause and offer the remedy: run \`kg doctor\`, then \`kg start\`. The kg_* tools are offline for this session; proceed without memory."
        exit 0
    fi
    START_OUT=$("$KG_BIN" start 2>&1)
    if ! healthy; then
        echo "KG memory server did not start: ${START_OUT:-no output from kg start}. Tell the user to run \`kg doctor\`. The kg_* tools are offline for this session; proceed without memory."
        exit 0
    fi
fi

# Payload rides an env var: the heredoc below already occupies stdin, and
# SessionStart payloads are small (ids + paths, no prompt text).
HOOK_JSON="$STDIN_JSON" python3 - "${CLAUDE_PROJECT_DIR:-$PWD}" "http://${HOST}:${PORT}" <<'PYEOF'
import json, os, sys, urllib.parse, urllib.request

cwd, base = sys.argv[1], sys.argv[2]
try:
    try:
        hook = json.loads(os.environ.get("HOOK_JSON") or "{}")
    except Exception:
        hook = {}
    cwd = hook.get("cwd") or cwd
    params = {"project_path": cwd}
    for src, dst in (("session_id", "claude_session_id"), ("source", "source"),
                     ("transcript_path", "transcript_path")):
        if hook.get(src):
            params[dst] = hook[src]
    url = f"{base}/api/session_bootstrap?{urllib.parse.urlencode(params)}"
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
    verb = "session resumed, seen-state preserved" if data.get("reused") else "preloaded"
    if stats:
        message = (
            f"KG memory {verb} (session {data['session_id']}): "
            f"{stats.get('user_active', '?')} user + {stats.get('project_active', '?')} project "
            f"active nodes, {stats.get('shown_gists', '?')} gists inline — kg_read renders the rest."
        )
    else:
        message = f"KG memory {verb} (session {data['session_id']})."
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
