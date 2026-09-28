"""Resolve execution cwd without guessing from a Codex hook's session cwd.

Codex 0.158 PostToolUse ids match completed CommandExecution items already
readable in the rollout (including nested/parallel calls). Read one bounded
snapshot, never wrapper JavaScript. Missing evidence leaves relative paths
unresolved; absolute file operands remain usable.
"""

from collections import OrderedDict
from dataclasses import dataclass
import json
import os
import stat
import threading
import time
from urllib.parse import unquote, urlsplit

from .session_manager import safe_transcript_path

MAX_BYTES = 256 * 1024
MAX_RECORDS = 2048
MAX_SECONDS = 0.05
MAX_SNAPSHOTS = 16
_cache = OrderedDict()
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class ShellContext:
    cwd: str | None
    source: str
    reason: str
    elapsed_ms: float

    def diagnostics(self):
        return {"cwd_source": self.source, "cwd_reason": self.reason,
                "cwd_elapsed_ms": self.elapsed_ms}


def _local_cwd(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if value.startswith("file:"):
        try:
            uri = urlsplit(value)
            if uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
                return None
            value = unquote(uri.path, errors="strict")
        except (ValueError, UnicodeError):
            return None
    if "\x00" in value or not os.path.isabs(value):
        return None
    return value


def _stamp(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _snapshot(filename, deadline):
    """Cache by file identity and snapshot end offset, not by call/id alone.

    Every append/rewrite/replacement invalidates this entry. Parsing stays
    outside the cache lock. Never retain raw commands or output in the cache.
    """
    resolved = safe_transcript_path(filename)
    if not resolved:
        return None, "invalid_transcript"
    fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode):
            return None, "invalid_transcript"
        key = (resolved, _stamp(st))
        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None:
                _cache.move_to_end(key)
                return cached, "matched"
        start = max(0, st.st_size - MAX_BYTES)
        stream.seek(start)
        data = stream.read(st.st_size - start)
        if _stamp(os.fstat(stream.fileno())) != _stamp(st):
            return None, "transcript_changed"
    if start:
        data = data.partition(b"\n")[2]  # first line may begin outside the tail
    if data and not data.endswith(b"\n"):
        return None, "incomplete_record"
    lines = data.splitlines()
    if len(lines) > MAX_RECORDS:
        return None, "record_limit"
    records = {}
    for line in lines:
        if time.monotonic() > deadline:
            return None, "time_limit"
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            return None, "malformed_record"
        if not isinstance(row, dict) or row.get("type") != "event_msg":
            continue
        event = row.get("payload")
        if not isinstance(event, dict) or event.get("type") != "item_completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "CommandExecution":
            continue
        call_id = item.get("id")
        if isinstance(call_id, str) and call_id:
            records.setdefault(call_id, []).append((event.get("thread_id"), event.get("turn_id"),
                                                    item.get("status"), _local_cwd(item.get("cwd"))))
    if time.monotonic() > deadline:
        return None, "time_limit"
    with _cache_lock:
        _cache[key] = records
        _cache.move_to_end(key)
        while len(_cache) > MAX_SNAPSHOTS:
            _cache.popitem(last=False)
    return records, "matched"


def resolve_shell_context(payload: dict, *, shell_cwd_known: bool) -> ShellContext:
    started = time.monotonic()

    def result(cwd, source, reason):
        elapsed = time.monotonic() - started
        if elapsed > MAX_SECONDS and source == "transcript":
            cwd, reason = None, "time_limit"
        return ShellContext(cwd, source, reason, round(elapsed * 1000, 3))

    tool_input = payload.get("tool_input")
    workdir = tool_input.get("workdir") if isinstance(tool_input, dict) else None
    if workdir is not None:
        # An invalid explicit value must never turn into permission to use cwd.
        cwd = workdir if isinstance(workdir, str) and os.path.isabs(workdir) and "\x00" not in workdir else None
        return result(cwd, "explicit_workdir", "matched" if cwd else "invalid_workdir")
    if shell_cwd_known:
        cwd = _local_cwd(payload.get("cwd"))
        return result(cwd, "hook_cwd", "matched" if cwd else "invalid_cwd")

    call_id, session = payload.get("tool_use_id"), payload.get("session_id")
    filename = payload.get("transcript_path")
    if not isinstance(call_id, str) or not call_id or not isinstance(session, str) or not session:
        return result(None, "transcript", "missing_identity")
    if not isinstance(filename, str) or not filename:
        return result(None, "transcript", "missing_transcript")
    try:
        records, reason = _snapshot(filename, started + MAX_SECONDS)
    except (OSError, ValueError):
        return result(None, "transcript", "unreadable_transcript")
    if records is None:
        return result(None, "transcript", reason)
    candidates = records.get(call_id, [])
    if not candidates:
        return result(None, "transcript", "missing_record")
    if len(candidates) != 1:
        return result(None, "transcript", "ambiguous_call")
    thread, turn, status, cwd = candidates[0]
    if thread != session or (payload.get("turn_id") and turn != payload["turn_id"]):
        return result(None, "transcript", "identity_mismatch")
    if status not in ("completed", "failed") or not cwd:
        return result(None, "transcript", "invalid_record")
    return result(cwd, "transcript", "matched")
