"""kg gauge: Claude Code's quota gauge, from inside the status line.

Claude Code pipes rate_limits only to the status-line command, so that is the
one place a session's quota can be recorded for agents and for the server
(budget notices, upkeep gates). With --wrap, the user's own status line runs
unchanged on the same input; without it, a minimal quota line is printed.
Same file and rules as recommended-setup/statusline.sh: a window missing from
this frame keeps its previous value and that value's own observation stamp.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

LIMITS = Path.home() / ".claude/last-limits.json"


def record(frame: dict, now: int) -> dict:
    limits = frame.get("rate_limits") or {}
    try:
        previous = json.loads(LIMITS.read_text())
    except (OSError, ValueError):
        previous = {}
    out = {}
    for window, key in (("five_hour", "five_hour"), ("seven_day", "seven_day")):
        live = limits.get(window) or {}
        if live.get("used_percentage") is not None:
            out[f"{key}_pct"] = live["used_percentage"]
            out[f"{key}_resets_at"] = live.get("resets_at")
            out[f"{key}_seen_at"] = now
        else:
            out[f"{key}_pct"] = previous.get(f"{key}_pct")
            out[f"{key}_resets_at"] = previous.get(f"{key}_resets_at")
            out[f"{key}_seen_at"] = previous.get(f"{key}_seen_at", previous.get("updated_at"))
    if out["five_hour_seen_at"] != now and out["seven_day_seen_at"] != now:
        return previous           # nothing live in this frame: never overwrite with old values
    out["context_pct"] = (frame.get("context_window") or {}).get("used_percentage") or 0
    out["updated_at"] = now
    LIMITS.parent.mkdir(parents=True, exist_ok=True)
    tmp = LIMITS.with_name(LIMITS.name + ".tmp")
    tmp.write_text(json.dumps(out) + "\n")
    os.replace(tmp, LIMITS)
    return out


def run(wrap: str | None) -> int:
    raw = sys.stdin.buffer.read()
    try:
        current = record(json.loads(raw or b"{}"), int(time.time()))
    except (ValueError, OSError):
        current = {}
    if wrap:
        result = subprocess.run(wrap, shell=True, input=raw, capture_output=True)
        sys.stdout.buffer.write(result.stdout)
        return result.returncode
    parts = [f"5h {current['five_hour_pct']:.0f}%" if current.get("five_hour_pct") is not None else "",
             f"week {current['seven_day_pct']:.0f}%" if current.get("seven_day_pct") is not None else ""]
    print(" · ".join(p for p in parts if p) or "kg")
    return 0
