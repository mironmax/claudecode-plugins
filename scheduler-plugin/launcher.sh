#!/usr/bin/env bash
# launcher.sh — Executed by systemd oneshot service.
# Opens a terminal with Claude Code resuming a scheduled session.
# Self-cleans: disables timer, removes unit files after firing.

set -euo pipefail

JOB_ID="${1:?Usage: launcher.sh <job_id>}"

SCHEDULER_DIR="$HOME/.claude/scheduler"
JOBS_FILE="$SCHEDULER_DIR/jobs.json"
CONFIG_FILE="$SCHEDULER_DIR/config.json"
SYSTEMD_DIR="$HOME/.config/systemd/user"
UNIT_PREFIX="claude-resume-"

# ---------------------------------------------------------------------------
# Read job metadata (python3 — no jq dependency)
# ---------------------------------------------------------------------------

read_job_field() {
    python3 -c "
import json, sys
jobs = json.load(open('$JOBS_FILE'))
job = next((j for j in jobs if j['job_id'] == '$JOB_ID'), None)
if not job:
    sys.exit(1)
print(job.get('$1', ''))
"
}

update_job_status() {
    python3 -c "
import json
from datetime import datetime
jobs = json.load(open('$JOBS_FILE'))
for j in jobs:
    if j['job_id'] == '$JOB_ID':
        j['status'] = '$1'
        j['${1}_at'] = datetime.now().isoformat()
        break
json.dump(jobs, open('$JOBS_FILE', 'w'), indent=2)
"
}

SESSION_ID=$(read_job_field session_id)
PROJECT_PATH=$(read_job_field project_path)
PROMPT=$(read_job_field prompt)
PERMISSION_MODE=$(read_job_field permission_mode)

if [ -z "$SESSION_ID" ]; then
    echo "ERROR: Job $JOB_ID not found or missing session_id" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Build Claude command
# ---------------------------------------------------------------------------

CLAUDE_CMD="cd $(printf '%q' "$PROJECT_PATH") && claude"

if [ "$SESSION_ID" = "latest" ]; then
    CLAUDE_CMD="$CLAUDE_CMD --continue"
else
    CLAUDE_CMD="$CLAUDE_CMD --resume $SESSION_ID"
fi

if [ -n "$PERMISSION_MODE" ]; then
    CLAUDE_CMD="$CLAUDE_CMD --permission-mode $PERMISSION_MODE"
fi

if [ -n "$PROMPT" ]; then
    CLAUDE_CMD="$CLAUDE_CMD --prompt $(printf '%q' "$PROMPT")"
fi

# Keep terminal open after Claude exits
CLAUDE_CMD="$CLAUDE_CMD; exec bash"

# ---------------------------------------------------------------------------
# Detect terminal emulator
# ---------------------------------------------------------------------------

get_configured_terminal() {
    if [ -f "$CONFIG_FILE" ]; then
        python3 -c "
import json, sys
cfg = json.load(open('$CONFIG_FILE'))
t = cfg.get('terminal', '')
if t:
    print(t)
" 2>/dev/null
    fi
}

detect_terminal() {
    # Check config first
    local configured
    configured=$(get_configured_terminal)
    if [ -n "$configured" ] && command -v "$configured" &>/dev/null; then
        echo "$configured"
        return
    fi

    # Probe available terminals in preference order
    for term in konsole kitty alacritty gnome-terminal wezterm xterm; do
        if command -v "$term" &>/dev/null; then
            echo "$term"
            return
        fi
    done

    # Fallback: xdg-terminal-exec (freedesktop standard)
    if command -v xdg-terminal-exec &>/dev/null; then
        echo "xdg-terminal-exec"
        return
    fi

    echo ""
}

TERMINAL=$(detect_terminal)

if [ -z "$TERMINAL" ]; then
    echo "ERROR: No terminal emulator found. Install one or set 'terminal' in $CONFIG_FILE" >&2
    update_job_status "failed"
    exit 1
fi

# ---------------------------------------------------------------------------
# Launch terminal
# ---------------------------------------------------------------------------

case "$TERMINAL" in
    konsole)
        konsole --noclose -e bash -c "$CLAUDE_CMD" &
        ;;
    kitty)
        kitty --hold bash -c "$CLAUDE_CMD" &
        ;;
    alacritty)
        alacritty -e bash -c "$CLAUDE_CMD" &
        ;;
    gnome-terminal)
        gnome-terminal -- bash -c "$CLAUDE_CMD" &
        ;;
    wezterm)
        wezterm start -- bash -c "$CLAUDE_CMD" &
        ;;
    xterm)
        xterm -hold -e bash -c "$CLAUDE_CMD" &
        ;;
    xdg-terminal-exec)
        xdg-terminal-exec bash -c "$CLAUDE_CMD" &
        ;;
    *)
        # Custom terminal — try generic -e flag
        "$TERMINAL" -e bash -c "$CLAUDE_CMD" &
        ;;
esac

# ---------------------------------------------------------------------------
# Update status & self-clean
# ---------------------------------------------------------------------------

update_job_status "fired"

# Remove systemd units (timer already triggered, no longer needed)
UNIT_NAME="${UNIT_PREFIX}${JOB_ID}"
systemctl --user disable --now "${UNIT_NAME}.timer" 2>/dev/null || true
rm -f "$SYSTEMD_DIR/${UNIT_NAME}.timer" "$SYSTEMD_DIR/${UNIT_NAME}.service"
systemctl --user daemon-reload 2>/dev/null || true

echo "Job $JOB_ID fired at $(date --iso-8601=seconds)"
