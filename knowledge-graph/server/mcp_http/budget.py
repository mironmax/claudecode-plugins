"""Budget notices: the session's own quota, said when it crosses a line.

An agent told where its quota lives still has to remember to look, and in
long autonomous runs it does not: Codex sessions reached 90-97% of the
five-hour window without a single reading. The hooks already reach the
model on every prompt and tool call, so the server reads the harness's own
gauge there and speaks once per threshold per window: first plan the
wrap-up, then wrap up. Silent when no gauge is readable.

Gauges: Claude Code's status-line file, the Codex session's own rollout,
Antigravity's `/usage` (live, cached here, refreshed off the hook thread).
"""

import os
import threading
import time
from datetime import datetime
from pathlib import Path

from . import chore_dispatch, harness

# (warn, stop) used-% per window. Five hours: the day's work; past 90% the
# work must wrap up or ask. A spent week stops work for days, so its lines
# sit higher and closer together.
THRESHOLDS = {"five_hour": (80, 90), "seven_day": (90, 95)}
LABELS = {"five_hour": "5-hour", "seven_day": "weekly"}
AGY_CACHE_SECONDS = 300

_agy = {"at": 0.0, "data": None, "refreshing": False}
_agy_lock = threading.Lock()


def _agy_reading(now: float) -> dict | None:
    """The cached reading; a stale cache refreshes in the background."""
    with _agy_lock:
        data, fresh = _agy["data"], now - _agy["at"] < AGY_CACHE_SECONDS
        if fresh or _agy["refreshing"]:
            return data
        _agy["refreshing"] = True

    def refresh():
        cfg = chore_dispatch._config()
        try:
            reading = chore_dispatch.AntigravityRunner().gauge(cfg, time.time())
        except Exception:
            reading = None
        with _agy_lock:
            _agy.update(at=time.time(), data=reading, refreshing=False)

    threading.Thread(target=refresh, daemon=True, name="kg-agy-usage").start()
    return data


def reading_for(name: str, transcript_path: str | None, now: float) -> dict | None:
    """Gauge keys for this harness's session, or None."""
    try:
        if name == harness.CODEX:
            if not transcript_path:
                return None
            return chore_dispatch.codex_limits(Path(transcript_path), now)
        if name == harness.ANTIGRAVITY:
            return _agy_reading(now)
        data = chore_dispatch.ClaudeRunner().gauge(chore_dispatch._config(), now)
    except Exception:
        return None
    # The status line may not have rendered since a window reset.
    for window in THRESHOLDS:
        resets = data.get(f"{window}_resets_at")
        if resets and resets < now:
            data[f"{window}_pct"] = 0
    return data


def _when(window: str, resets: float | None) -> str:
    if not resets:
        return "reset time unknown"
    at = datetime.fromtimestamp(resets)
    return "resets " + (at.strftime("%H:%M") if window == "five_hour" else at.strftime("%a %H:%M"))


def notice(session_manager, sid: str | None, name: str,
           transcript_path: str | None, now: float | None = None) -> str | None:
    """The notice this session has not heard yet, or None."""
    if (not sid or os.environ.get("KG_BUDGET_NOTICES") == "0"
            or not chore_dispatch._config().get("budget_notices", True)):
        return None
    now = time.time() if now is None else now
    data = reading_for(name, transcript_path, now)
    if not data:
        return None
    lines = []
    for window, (warn, stop) in THRESHOLDS.items():
        pct = data.get(f"{window}_pct")
        if pct is None:
            continue
        level = 2 if pct >= stop else 1 if pct >= warn else 0
        resets = data.get(f"{window}_resets_at")
        if not level or not session_manager.note_budget_level(sid, window, level, resets):
            continue
        head = f"Budget: {LABELS[window]} quota at {pct:.0f}% ({_when(window, resets)})."
        lines.append(head + (" Wrap up now: commit or checkpoint, write the handover "
                             "and memory updates, then stop or ask the user."
                             if level == 2 else
                             " Plan the wrap-up: finish the current step, reach a "
                             "commit point, keep the next steps small."))
    return "\n".join(lines) or None
