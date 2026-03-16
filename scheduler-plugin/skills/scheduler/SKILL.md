---
name: scheduler
description: Schedule automatic Claude Code session resumption using systemd timers
---

# Scheduler Skill

Schedule future Claude Code sessions via systemd user timers. Timers survive sleep/hibernate (`Persistent=true`).

## Auto-Scheduling (Usage Limits)

A `PostToolUse` hook monitors plan usage automatically. When thresholds are hit:
- **95% daily (5-hour window)** → auto-schedules resume at reset time
- **98% weekly (7-day window)** → auto-schedules resume at reset time

The hook notifies you when this happens. No action needed from Claude unless the user wants to customize the scheduled job.

## When to Offer Manual Scheduling

- **User asks** — "schedule this for tomorrow", "continue this later"
- **Long-running tasks** — break into scheduled phases
- **Time-specific work** — "remind me to review this tomorrow morning"
- **Before extra usage kicks in** — use `check_usage` to see remaining capacity, offer to schedule at reset time instead of burning expensive extra tokens

## Tools

### `check_usage`

Query current plan utilization. Call this when:
- User asks about remaining usage/limits
- You want to advise whether to continue or schedule for later
- Checking if auto-scheduling has already been triggered

Returns: `five_hour.utilization`, `seven_day.utilization`, `resets_at` timestamps, `extra_usage` status.

### `schedule_session`

| Param | Required | Description |
|-------|----------|-------------|
| `session_id` | yes | Session to resume. Use `"latest"` for most recent. |
| `project_path` | yes | Absolute path to project directory. |
| `at` | yes | When to fire (see formats below). |
| `prompt` | no | Continuation prompt — what to do when session resumes. |
| `permission_mode` | no | e.g. `"plan"`, `"full-auto"` |

**Time formats:**

```
"in 30m"              # 30 minutes from now
"in 2h"               # 2 hours from now
"in 1d"               # tomorrow, same time
"tomorrow 9:00"       # next day at 9 AM
"monday 14:00"        # next Monday at 2 PM
"2025-12-31T14:00:00" # exact ISO 8601
```

### `list_schedules` / `cancel_schedule`

List all jobs or cancel by `job_id`.

## Crafting Continuation Prompts

Good prompts include:
- What was done so far
- What to do next (specific files/functions)
- Any blockers or decisions made

```
"Continuing refactor of auth module. Completed: migrated User model,
updated login endpoint. Next: migrate registration endpoint, then
update tests in tests/test_auth.py."
```

## Example Flows

**User-initiated:**
1. User: "I need to stop, but this refactor isn't done"
2. Claude: Summarize progress, call `schedule_session` with continuation prompt
3. Timer fires → terminal opens → Claude resumes with context

**Limit-aware:**
1. Call `check_usage` → daily at 80%, resets at 14:30
2. Tell user: "You're at 80% of your 5-hour limit. Want to schedule continuation at 14:30 when it resets?"
3. User agrees → `schedule_session(at="2026-02-09T14:30:00")`

**Automatic (hook-driven):**
1. Hook detects 95% daily → auto-schedules at reset time
2. User sees: "Limit reached (five_hour at 95%). Session resume scheduled at 14:30."
3. No Claude action needed — hook handled it directly
