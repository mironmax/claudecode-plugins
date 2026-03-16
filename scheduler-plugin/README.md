# Scheduler Plugin

Schedule automatic Claude Code session resumption using systemd user timers.

When you hit your usage limit or need to continue work later, the plugin schedules a timer that opens a new terminal with Claude resuming where you left off. Timers use `Persistent=true` — they fire even after sleep/hibernate (on next wake).

**Linux-only. Requires systemd with user session support.**

## Features

- **Manual scheduling** — Claude calls `schedule_session` when you ask, or at wrap-up
- **Auto-scheduling on limit hit** — A `PostToolUse` hook monitors your plan usage. At 95% daily (5h) or 98% weekly (7d), it auto-schedules a resume at the reset time
- **Usage checking** — `check_usage` tool queries live plan utilization and reset times
- **Sleep/hibernate resilient** — systemd `Persistent=true` fires on next wake

## Installation

```bash
cd scheduler-plugin
bash install.sh
```

### Add MCP server

```bash
claude mcp add scheduler -- ./server/venv/bin/python ./server/mcp_stdio_server.py
```

### Add usage monitor hook

Add this to `~/.claude/settings.json` (or project `.claude/settings.json`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "/path/to/scheduler-plugin/hooks/usage_monitor.sh"
      }
    ]
  }
}
```

Replace `/path/to/scheduler-plugin` with the actual path.

The hook:
- Runs after every tool call, throttled to once per 60 seconds
- Queries `api.anthropic.com/api/oauth/usage` using your Claude Code OAuth token
- At 95% daily or 98% weekly usage, auto-schedules a resume at the reset time
- Writes usage cache to `~/.claude/scheduler/usage_cache.json` for the MCP server

### Configure terminal (optional)

```bash
echo '{"terminal": "kitty"}' > ~/.claude/scheduler/config.json
```

Supported terminals (auto-detected in this order): konsole, kitty, alacritty, gnome-terminal, wezterm, xterm, xdg-terminal-exec.

## MCP Tools

### `schedule_session`

Schedule a session to resume at a specific time.

```
schedule_session(
    session_id="latest",
    project_path="/home/user/proj",
    at="in 2h",                    # "tomorrow 9:00", "monday 14:00", ISO 8601
    prompt="Continue auth refactor. Done: User model. Next: registration endpoint.",
    permission_mode="plan"         # optional
)
```

### `check_usage`

Query current plan utilization and reset times (live API call with cache fallback).

```
check_usage()
→ { "five_hour": { "utilization": 85.0, "resets_at": "..." },
     "seven_day": { "utilization": 35.0, "resets_at": "..." },
     "extra_usage": { "is_enabled": true, "utilization": 102.3, ... } }
```

### `list_schedules`

List all scheduled jobs with live systemd timer status.

### `cancel_schedule`

Cancel a pending job by ID. Removes timer and service files.

## How It Works

### Manual flow
1. Claude calls `schedule_session` → writes job + creates systemd timer
2. Timer fires → `launcher.sh` opens terminal → Claude resumes with `--continue`/`--resume`
3. Launcher self-cleans unit files after firing

### Auto-scheduling flow
1. `PostToolUse` hook checks OAuth usage every 60s
2. When 95% daily or 98% weekly threshold hit → hook writes systemd timer directly
3. Timer fires at `resets_at` time → terminal opens → new Claude session starts
4. Hook output notifies user: "Limit reached. Session resume scheduled at HH:MM."

### File Locations

| File | Purpose |
|------|---------|
| `~/.claude/scheduler/jobs.json` | Job queue |
| `~/.claude/scheduler/config.json` | Terminal preference (optional) |
| `~/.claude/scheduler/usage_cache.json` | Last usage poll result |
| `~/.config/systemd/user/claude-resume-*.timer` | systemd timer units |
| `~/.config/systemd/user/claude-resume-*.service` | systemd service units |

## Verification

```bash
# Check timer was created
systemctl --user list-timers | grep claude-resume

# Check timer status
systemctl --user status claude-resume-<job-id>.timer

# View job queue
cat ~/.claude/scheduler/jobs.json

# View cached usage
cat ~/.claude/scheduler/usage_cache.json
```

## Troubleshooting

**Timer doesn't fire after sleep:** Ensure `loginctl enable-linger $USER` is set.

**No terminal opens:** Set your terminal in `~/.claude/scheduler/config.json` or ensure one of the supported terminals is in `$PATH`.

**Hook not firing:** Check that the hook path in settings.json is absolute and the script is executable (`chmod +x`).

**Usage API returns nothing:** Verify you're logged in to Claude Code (`claude auth status`). The OAuth token in `~/.claude/.credentials.json` must be valid.
