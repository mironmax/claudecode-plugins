#!/usr/bin/env python3
"""
Scheduler MCP Server — stdio transport.

Schedules Claude Code session resumption via systemd user timers.
Persistent=true ensures timers fire after sleep/hibernate.
"""

import json
import os
import re
import subprocess
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEDULER_DIR = Path.home() / ".claude" / "scheduler"
JOBS_FILE = SCHEDULER_DIR / "jobs.json"
CONFIG_FILE = SCHEDULER_DIR / "config.json"
USAGE_CACHE_FILE = SCHEDULER_DIR / "usage_cache.json"
CREDS_FILE = Path.home() / ".claude" / ".credentials.json"
SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT_PREFIX = "claude-resume-"

# Where the plugin lives (resolved at import time)
PLUGIN_DIR = Path(__file__).resolve().parent.parent

# Environment variables to capture for systemd service
ENV_CAPTURE_KEYS = [
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "HOME",
    "PATH",
]

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _ensure_dirs():
    SCHEDULER_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)


def _load_jobs() -> list[dict]:
    if JOBS_FILE.exists():
        return json.loads(JOBS_FILE.read_text())
    return []


def _save_jobs(jobs: list[dict]):
    _ensure_dirs()
    JOBS_FILE.write_text(json.dumps(jobs, indent=2))


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


# ---------------------------------------------------------------------------
# Time parsing — stdlib only
# ---------------------------------------------------------------------------

_RELATIVE_RE = re.compile(
    r"^in\s+(\d+)\s*(m|min|mins|minutes?|h|hrs?|hours?|d|days?)$", re.IGNORECASE
)
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def _parse_time(at: str) -> datetime:
    """Parse human-friendly time string into an absolute datetime.

    Supported formats:
        - ISO 8601: 2025-12-31T14:00:00
        - Relative:  in 30m, in 2h, in 1d
        - Tomorrow:  tomorrow 9:00, tomorrow 14:30
        - Weekday:   monday 14:00, fri 09:30
    """
    at = at.strip()
    now = datetime.now()

    # ISO 8601
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(at, fmt)
        except ValueError:
            continue

    # Relative: in Xm/h/d
    m = _RELATIVE_RE.match(at)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)[0].lower()
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}
        return now + delta[unit]

    # Tomorrow HH:MM
    lower = at.lower()
    if lower.startswith("tomorrow"):
        time_part = lower.replace("tomorrow", "").strip()
        h, mn = _parse_hhmm(time_part)
        return (now + timedelta(days=1)).replace(hour=h, minute=mn, second=0, microsecond=0)

    # Weekday HH:MM
    parts = lower.split()
    if len(parts) == 2 and parts[0] in _WEEKDAYS:
        target_day = _WEEKDAYS[parts[0]]
        h, mn = _parse_hhmm(parts[1])
        days_ahead = (target_day - now.weekday()) % 7
        if days_ahead == 0:
            # Same weekday — schedule for next week if time already passed
            candidate = now.replace(hour=h, minute=mn, second=0, microsecond=0)
            if candidate <= now:
                days_ahead = 7
        target = (now + timedelta(days=days_ahead)).replace(hour=h, minute=mn, second=0, microsecond=0)
        return target

    raise ValueError(
        f"Cannot parse time '{at}'. "
        "Use: ISO 8601, 'in 30m', 'in 2h', 'tomorrow 14:00', or 'monday 09:00'."
    )


def _parse_hhmm(s: str) -> tuple[int, int]:
    """Parse HH:MM or H:MM string."""
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Expected HH:MM, got '{s}'")
    return int(parts[0]), int(parts[1])


def _to_systemd_calendar(dt: datetime) -> str:
    """Convert datetime to systemd OnCalendar format."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# systemd unit management
# ---------------------------------------------------------------------------


def _env_block() -> str:
    """Capture current environment variables for the systemd service."""
    lines = []
    for key in ENV_CAPTURE_KEYS:
        val = os.environ.get(key)
        if val:
            lines.append(f'Environment="{key}={val}"')
    return "\n".join(lines)


def _write_units(job_id: str, fire_at: datetime, job: dict):
    """Write .timer and .service unit files."""
    _ensure_dirs()
    name = f"{UNIT_PREFIX}{job_id}"
    launcher = PLUGIN_DIR / "launcher.sh"

    timer_content = f"""\
[Unit]
Description=Claude Code scheduled resume — {job_id}

[Timer]
OnCalendar={_to_systemd_calendar(fire_at)}
Persistent=true
AccuracySec=1s

[Install]
WantedBy=timers.target
"""

    service_content = f"""\
[Unit]
Description=Claude Code resume launcher — {job_id}

[Service]
Type=oneshot
{_env_block()}
ExecStart=/bin/bash {launcher} {job_id}
"""

    (SYSTEMD_DIR / f"{name}.timer").write_text(timer_content)
    (SYSTEMD_DIR / f"{name}.service").write_text(service_content)


def _enable_timer(job_id: str):
    name = f"{UNIT_PREFIX}{job_id}"
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{name}.timer"],
        check=True, capture_output=True,
    )


def _disable_timer(job_id: str):
    name = f"{UNIT_PREFIX}{job_id}"
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", f"{name}.timer"],
        capture_output=True,
    )
    # Remove unit files
    for suffix in (".timer", ".service"):
        path = SYSTEMD_DIR / f"{name}{suffix}"
        path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def _timer_status(job_id: str) -> str:
    """Query systemd for timer state. Returns 'active', 'inactive', or 'unknown'."""
    name = f"{UNIT_PREFIX}{job_id}"
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", f"{name}.timer"],
            capture_output=True, text=True,
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = FastMCP("scheduler")


@server.tool()
def schedule_session(
    session_id: str,
    project_path: str,
    at: str,
    prompt: Optional[str] = None,
    permission_mode: Optional[str] = None,
) -> dict:
    """Schedule a Claude Code session to resume at a specific time.

    Uses systemd user timers with Persistent=true — survives sleep/hibernate.

    Args:
        session_id: Session to resume. Use "latest" to continue the most recent session.
        project_path: Absolute path to the project directory.
        at: When to fire. Supports:
            - ISO 8601: "2025-12-31T14:00:00"
            - Relative: "in 30m", "in 2h", "in 1d"
            - Tomorrow: "tomorrow 9:00"
            - Weekday: "monday 14:00", "fri 09:30"
        prompt: Optional continuation prompt (summary of what to do next).
        permission_mode: Optional Claude permission mode (e.g. "plan", "full-auto").
    """
    fire_at = _parse_time(at)

    if fire_at <= datetime.now():
        return {"error": f"Scheduled time {fire_at.isoformat()} is in the past."}

    job_id = uuid.uuid4().hex[:8]
    job = {
        "job_id": job_id,
        "session_id": session_id,
        "project_path": project_path,
        "fire_at": fire_at.isoformat(),
        "prompt": prompt,
        "permission_mode": permission_mode,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    }

    # Persist job
    jobs = _load_jobs()
    jobs.append(job)
    _save_jobs(jobs)

    # Create and enable systemd units
    _write_units(job_id, fire_at, job)
    _enable_timer(job_id)

    return {
        "job_id": job_id,
        "fire_at": fire_at.isoformat(),
        "timer_status": _timer_status(job_id),
        "message": f"Scheduled session resume at {fire_at.strftime('%Y-%m-%d %H:%M')}.",
    }


@server.tool()
def list_schedules() -> dict:
    """List all scheduled Claude Code session resumptions with their status."""
    jobs = _load_jobs()
    result = []
    for job in jobs:
        entry = {**job}
        if job["status"] == "pending":
            entry["timer_status"] = _timer_status(job["job_id"])
        result.append(entry)
    return {"jobs": result, "total": len(result)}


@server.tool()
def cancel_schedule(job_id: str) -> dict:
    """Cancel a scheduled session resumption.

    Args:
        job_id: The job ID returned by schedule_session.
    """
    jobs = _load_jobs()
    found = False
    for job in jobs:
        if job["job_id"] == job_id:
            if job["status"] != "pending":
                return {"error": f"Job {job_id} is already {job['status']}, cannot cancel."}
            job["status"] = "cancelled"
            job["cancelled_at"] = datetime.now().isoformat()
            found = True
            break

    if not found:
        return {"error": f"Job {job_id} not found."}

    _save_jobs(jobs)
    _disable_timer(job_id)

    return {"job_id": job_id, "status": "cancelled", "message": f"Job {job_id} cancelled and timer removed."}


# ---------------------------------------------------------------------------
# Usage API
# ---------------------------------------------------------------------------


def _query_usage_api() -> dict | None:
    """Query Anthropic OAuth usage endpoint. Returns parsed JSON or None."""
    if not CREDS_FILE.exists():
        return None
    try:
        creds = json.loads(CREDS_FILE.read_text())
        token = creds["claudeAiOauth"]["accessToken"]
    except (KeyError, json.JSONDecodeError):
        return None

    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


@server.tool()
def check_usage() -> dict:
    """Check current Anthropic plan usage (5-hour and 7-day limits).

    Returns utilization percentages and reset times. Also reads
    cached data from the usage monitor hook if available.
    """
    # Try live query first
    live = _query_usage_api()
    if live:
        result = {
            "source": "live",
            "five_hour": live.get("five_hour"),
            "seven_day": live.get("seven_day"),
            "extra_usage": live.get("extra_usage"),
        }
        # Cache for hook to read
        _ensure_dirs()
        USAGE_CACHE_FILE.write_text(json.dumps({
            "five_hour": live.get("five_hour"),
            "seven_day": live.get("seven_day"),
            "checked_at": datetime.now().isoformat(),
        }, indent=2))
        return result

    # Fall back to cached data from hook
    if USAGE_CACHE_FILE.exists():
        try:
            cached = json.loads(USAGE_CACHE_FILE.read_text())
            cached["source"] = "cached"
            return cached
        except json.JSONDecodeError:
            pass

    return {"error": "Could not query usage API and no cached data available."}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server.run(transport="stdio")
