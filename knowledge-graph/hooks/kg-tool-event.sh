#!/usr/bin/env bash
# PostToolUse hook (file tools, Bash, WebFetch, WebSearch): report the tool
# event to the KG server; relay what it decides — the memory covering the file
# just touched (file recall), or a capture nudge for an uncovered target that
# has proven itself worth remembering (re-derived across sessions, throttled).
# The intelligence is entirely server-side: this script posts the raw hook
# payload and prints whatever hook output comes back.
# Silent on every failure — a hook must never slow or break the session.

# A maintenance chore runs headless inside this same plugin: it needs no
# preload (its prompt names its targets), no recall, and above all no chore
# dispatch of its own. The runner exports KG_CHORE=1 — stay silent.
[ -n "${KG_CHORE:-}" ] && exit 0

STDIN_JSON=$(cat 2>/dev/null)
[ -z "$STDIN_JSON" ] && exit 0

HOST="${KG_HTTP_HOST:-127.0.0.1}"
PORT="${KG_HTTP_PORT:-8765}"

RESP=$(printf '%s' "$STDIN_JSON" | curl -sf --max-time 1 -X POST \
    -H 'Content-Type: application/json' --data-binary @- \
    "http://${HOST}:${PORT}/api/tool_event" 2>/dev/null)

case "$RESP" in
    *hookSpecificOutput*) printf '%s' "$RESP" ;;
esac
exit 0
