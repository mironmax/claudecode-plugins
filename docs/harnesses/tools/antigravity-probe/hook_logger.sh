#!/bin/bash
# Antigravity hook probe: hook_logger.sh <EVENT>
#
# Saves the hook's stdin payload and its working directory to
# $PROBE_LOG/<ns>-<EVENT>.stdin.json and .cwd, then prints a reply:
# $PROBE_REPLY/<EVENT>.py (fed the payload on stdin) if present, else
# $PROBE_REPLY/<EVENT>.json if present, else {}.
#
# Answering {} to PreToolUse denies the tool: give PreToolUse a reply file
# such as {"decision": "ask"} when probing it.
#
# The full environment is deliberately not saved: a hook inherits the CLI's
# environment, which can hold credentials.
ev="$1"
log="${PROBE_LOG:?set PROBE_LOG}"
reply="${PROBE_REPLY:-$log/replies}"
n=$(date +%s%N)
payload=$(cat)
mkdir -p "$log"
printf '%s' "$payload" > "$log/$n-$ev.stdin.json"
{ echo "cwd=$PWD"; env | grep -E '^(ANTIGRAVITY_|PLUGIN_|KG_)' ; } > "$log/$n-$ev.cwd"
if [ -f "$reply/$ev.py" ]; then
    printf '%s' "$payload" | python3 "$reply/$ev.py"
elif [ -f "$reply/$ev.json" ]; then
    cat "$reply/$ev.json"
else
    echo '{}'
fi
