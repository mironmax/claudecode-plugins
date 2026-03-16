#!/usr/bin/env bash
# usage_monitor.sh — PostToolUse hook
#
# Polls Anthropic OAuth usage endpoint after each tool call.
# When thresholds are hit (95% daily, 98% weekly), auto-schedules
# a session resumption at the reset time via systemd timer.
#
# Throttled: only checks every 60 seconds to avoid API spam.

set -euo pipefail
exec 2>/dev/null  # suppress stderr — hook errors must not disrupt Claude

SCHEDULER_DIR="$HOME/.claude/scheduler"
JOBS_FILE="$SCHEDULER_DIR/jobs.json"
CREDS_FILE="$HOME/.claude/.credentials.json"
LOCK_FILE="$SCHEDULER_DIR/.usage_check_ts"
SYSTEMD_DIR="$HOME/.config/systemd/user"
UNIT_PREFIX="claude-resume-"
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Thresholds
DAILY_THRESHOLD=95
WEEKLY_THRESHOLD=98

# Throttle: check at most once per 60 seconds
CHECK_INTERVAL=60

# ---------------------------------------------------------------------------
# Read hook input from stdin
# ---------------------------------------------------------------------------

HOOK_INPUT=$(cat)
SESSION_ID=$(echo "$HOOK_INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")
PROJECT_PATH=$(echo "$HOOK_INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null || echo "")

# ---------------------------------------------------------------------------
# Throttle check
# ---------------------------------------------------------------------------

now=$(date +%s)
if [ -f "$LOCK_FILE" ]; then
    last_check=$(cat "$LOCK_FILE" 2>/dev/null || echo "0")
    elapsed=$((now - last_check))
    if [ "$elapsed" -lt "$CHECK_INTERVAL" ]; then
        exit 0
    fi
fi

mkdir -p "$SCHEDULER_DIR"
echo "$now" > "$LOCK_FILE"

# ---------------------------------------------------------------------------
# Query usage API
# ---------------------------------------------------------------------------

if [ ! -f "$CREDS_FILE" ]; then
    exit 0
fi

TOKEN=$(python3 -c "import json; print(json.load(open('$CREDS_FILE'))['claudeAiOauth']['accessToken'])" 2>/dev/null || exit 0)

USAGE_JSON=$(curl -sf --max-time 5 \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -H "anthropic-beta: oauth-2025-04-20" \
    "https://api.anthropic.com/api/oauth/usage" 2>/dev/null || exit 0)

# ---------------------------------------------------------------------------
# Parse usage
# ---------------------------------------------------------------------------

read -r DAILY_UTIL DAILY_RESET WEEKLY_UTIL WEEKLY_RESET <<< $(python3 -c "
import json, sys
d = json.loads('''$USAGE_JSON''')
fh = d.get('five_hour', {})
wd = d.get('seven_day', {})
print(
    fh.get('utilization', 0),
    fh.get('resets_at', ''),
    wd.get('utilization', 0),
    wd.get('resets_at', '')
)
" 2>/dev/null || echo "0 '' 0 ''")

# Write latest usage for MCP server to read
python3 -c "
import json
data = {
    'five_hour': {'utilization': $DAILY_UTIL, 'resets_at': '$DAILY_RESET'},
    'seven_day': {'utilization': $WEEKLY_UTIL, 'resets_at': '$WEEKLY_RESET'},
    'checked_at': $now
}
json.dump(data, open('$SCHEDULER_DIR/usage_cache.json', 'w'), indent=2)
" 2>/dev/null

# ---------------------------------------------------------------------------
# Check thresholds and auto-schedule
# ---------------------------------------------------------------------------

schedule_resume() {
    local reason="$1"
    local reset_at="$2"
    local job_id

    # Don't schedule if already scheduled for this reset time
    if [ -f "$JOBS_FILE" ]; then
        existing=$(python3 -c "
import json
jobs = json.load(open('$JOBS_FILE'))
pending = [j for j in jobs if j['status'] == 'pending' and j.get('trigger') == '$reason']
print(len(pending))
" 2>/dev/null || echo "0")
        if [ "$existing" != "0" ]; then
            return
        fi
    fi

    job_id=$(python3 -c "import uuid; print(uuid.uuid4().hex[:8])")

    # Convert ISO reset time to systemd calendar format
    local calendar_time
    calendar_time=$(python3 -c "
from datetime import datetime
dt = datetime.fromisoformat('$reset_at')
print(dt.strftime('%Y-%m-%d %H:%M:%S'))
" 2>/dev/null || exit 0)

    # Add job to jobs.json
    python3 -c "
import json
from datetime import datetime

jobs = json.load(open('$JOBS_FILE')) if __import__('os').path.exists('$JOBS_FILE') else []
jobs.append({
    'job_id': '$job_id',
    'session_id': '$SESSION_ID' or 'latest',
    'project_path': '$PROJECT_PATH',
    'fire_at': '$reset_at',
    'prompt': 'Auto-scheduled: $reason limit hit. Session resumed after reset.',
    'permission_mode': None,
    'status': 'pending',
    'trigger': '$reason',
    'created_at': datetime.now().isoformat()
})
json.dump(jobs, open('$JOBS_FILE', 'w'), indent=2)
" 2>/dev/null

    # Write systemd units
    local unit_name="${UNIT_PREFIX}${job_id}"
    local launcher="$PLUGIN_DIR/launcher.sh"

    # Capture env for terminal display
    local env_lines=""
    for var in DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS HOME PATH; do
        val="${!var:-}"
        if [ -n "$val" ]; then
            env_lines="${env_lines}Environment=\"${var}=${val}\"
"
        fi
    done

    cat > "$SYSTEMD_DIR/${unit_name}.timer" <<EOF
[Unit]
Description=Claude Code auto-resume — $reason limit ($job_id)

[Timer]
OnCalendar=$calendar_time
Persistent=true
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF

    cat > "$SYSTEMD_DIR/${unit_name}.service" <<EOF
[Unit]
Description=Claude Code resume launcher — $job_id

[Service]
Type=oneshot
${env_lines}ExecStart=/bin/bash $launcher $job_id
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now "${unit_name}.timer"

    # Signal to user via stdout (hook stdout goes to Claude's context)
    echo "Limit reached ($reason at ${DAILY_UTIL:-$WEEKLY_UTIL}%). Session resume scheduled at $(date -d "$reset_at" '+%H:%M %Z' 2>/dev/null || echo "$reset_at"). Job: $job_id"
}

# Check daily (5-hour) threshold
daily_int=${DAILY_UTIL%.*}
if [ "${daily_int:-0}" -ge "$DAILY_THRESHOLD" ] && [ -n "$DAILY_RESET" ]; then
    schedule_resume "five_hour" "$DAILY_RESET"
fi

# Check weekly threshold
weekly_int=${WEEKLY_UTIL%.*}
if [ "${weekly_int:-0}" -ge "$WEEKLY_THRESHOLD" ] && [ -n "$WEEKLY_RESET" ]; then
    schedule_resume "seven_day" "$WEEKLY_RESET"
fi

exit 0
