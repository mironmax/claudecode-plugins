#!/usr/bin/env bash
# PostToolUse hook (Read|WebFetch|WebSearch): report the tool event to the KG
# server; relay a capture nudge if — and only if — the server decides this
# target has proven itself worth remembering (uncovered + re-derived across
# sessions, throttled). The intelligence is entirely server-side: this script
# posts the raw hook payload and prints whatever hook output comes back.
# Silent on every failure — a hook must never slow or break the session.

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
