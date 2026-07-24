"""Session management for HTTP MCP server with project path tracking."""

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from core.constants import SESSION_ID_LENGTH, SESSION_TTL_SECONDS, sessions_file_path, safe_project_path

logger = logging.getLogger(__name__)

# Our own renders leave the KG session id in the transcript — the preload
# header, the kg_read footer, and (pre-0.9.28) the recall header. A resumed
# Claude session forks the transcript under a NEW Claude session id and
# rewrites the per-record sessionId fields, so these markers are the only
# durable link back to the KG session whose seen-state the copied context
# still reflects.
_KG_SID_PATTERNS = (
    re.compile(r"session_id: ([0-9a-f]{8}) \(pass"),
    re.compile(r"Session: ([0-9a-f]{8})"),
    re.compile(r"session_id='([0-9a-f]{8})'"),
)


def recover_kg_sid_from_transcript(transcript_path: str) -> str | None:
    """Last KG session id our renders left in a (possibly forked) transcript."""
    last = None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "ession" not in line:
                    continue
                for pat in _KG_SID_PATTERNS:
                    for m in pat.finditer(line):
                        last = m.group(1)
    except OSError:
        return None
    return last


class HTTPSessionManager:
    """Manages sessions with project_path tracking for multi-project support.

    Sessions store project root paths (not graph file paths).
    The store layer resolves project roots to centralized graph paths.
    """

    def __init__(self, session_ttl: int = SESSION_TTL_SECONDS):
        self.session_ttl = session_ttl
        self._sessions: dict[str, dict] = {}
        self._sessions_file = sessions_file_path()
        self._load_sessions()

    def register(self, project_path: str | None = None, claude_sid: str | None = None) -> dict:
        """
        Register a new session with optional project root path.
        Returns {"session_id": str, "start_ts": float}.

        Args:
            project_path: Absolute path to the project root directory.
            claude_sid: Claude Code session id to bind — ambient hooks resolve
                their KG session through this binding, so recall dedup follows
                the actual session instead of "newest in project".
        """
        session_id = uuid.uuid4().hex[:SESSION_ID_LENGTH]
        ts = time.time()

        resolved_project_path = str(safe_project_path(project_path)) if project_path else None

        self._sessions[session_id] = {
            "start_ts": ts,
            "project_path": resolved_project_path,
            "last_activity": ts,
            "op_count": 0,
        }
        if claude_sid:
            self.bind_claude_sid(session_id, claude_sid, save=False)

        logger.info(f"Session registered: {session_id} (project: {resolved_project_path or 'none'})")
        self.save_sessions()  # Persist immediately so project_path survives restarts
        return {"session_id": session_id, "start_ts": ts}

    def bind_claude_sid(self, session_id: str, claude_sid: str, save: bool = True) -> None:
        """Bind a Claude Code session id to a KG session (rebind on resume).

        A Claude sid points to at most one KG session: any prior binding of
        the same claude_sid is cleared (its Claude session is dead — resume
        forks mint a new one).
        """
        if session_id not in self._sessions:
            return
        for data in self._sessions.values():
            if data.get("claude_sid") == claude_sid:
                data.pop("claude_sid", None)
        self._sessions[session_id]["claude_sid"] = claude_sid
        if save:
            self.save_sessions()

    def find_by_claude_sid(self, claude_sid: str) -> tuple[str, dict] | None:
        """KG session bound to this Claude Code session id, or None."""
        if not claude_sid:
            return None
        for sid, data in self._sessions.items():
            if data.get("claude_sid") == claude_sid:
                return (sid, data)
        return None

    def lookup(self, session_id: str) -> dict | None:
        """Return the session record if it exists — no auto-recovery, no mutation.

        kg_read uses this to decide whether a caller-supplied session_id can be
        reused (it must exist AND carry a project_path). ensure_session would
        silently create a path-less session here, which breaks project reads.
        """
        return self._sessions.get(session_id)

    def ensure_session(self, session_id: str) -> None:
        """
        Re-register a session if it was lost (e.g. server restart).
        Silently creates a new session entry preserving the original ID.
        No-op if session already exists.
        """
        if session_id in self._sessions:
            return

        ts = time.time()
        self._sessions[session_id] = {
            "start_ts": ts,
            "project_path": None,
            "last_activity": ts,
            "op_count": 0,
        }
        logger.info(f"Session auto-recovered: {session_id} (no project_path — was lost on restart)")

    def get_project_path(self, session_id: str) -> str | None:
        """Get project root path for a session. Auto-recovers lost sessions."""
        self.ensure_session(session_id)
        self._update_activity(session_id)
        return self._sessions[session_id]["project_path"]

    def get_start_ts(self, session_id: str) -> float:
        """Get session start timestamp. Auto-recovers lost sessions."""
        self.ensure_session(session_id)
        return self._sessions[session_id]["start_ts"]

    def _update_activity(self, session_id: str):
        """Update last activity timestamp for a session."""
        if session_id in self._sessions:
            self._sessions[session_id]["last_activity"] = time.time()

    def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count of removed sessions."""
        current_time = time.time()
        expired = [
            sid for sid, data in self._sessions.items()
            if current_time - data["last_activity"] > self.session_ttl
        ]

        for sid in expired:
            del self._sessions[sid]
            logger.info(f"Session expired: {sid}")

        return len(expired)

    def mark_seen(self, session_id: str, node_ids) -> None:
        """Record node ids whose GIST this session has already been shown.

        Feeds search dedup: a hit the session has already seen renders as a
        one-line gist reminder, never a repeated notes dump. Stored as a list
        (JSON-serializable), deduped on insert.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        seen = session.setdefault("seen_ids", [])
        seen_set = set(seen)
        for nid in node_ids:
            if nid not in seen_set:
                seen.append(nid)
                seen_set.add(nid)

    def get_seen(self, session_id: str) -> set:
        """Set of node ids this session has already seen gists for."""
        session = self._sessions.get(session_id)
        return set(session.get("seen_ids", [])) if session else set()

    def set_preloaded(self, session_id: str, node_ids) -> None:
        """Record which node gists the session-start preload actually rendered.

        Feeds kg_read dedup: the loud full-graph read shows these as id-only
        anchors and spends its budget on everything the compact preload had to
        drop. Set, not append — a preload defines the session's baseline.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["preloaded_ids"] = list(node_ids)

    def get_preloaded(self, session_id: str) -> set:
        """Node ids whose gists the session-start preload put in context."""
        session = self._sessions.get(session_id)
        return set(session.get("preloaded_ids", [])) if session else set()

    def mark_full_read(self, session_id: str) -> None:
        """Record that this session has made the loud full-graph kg_read.

        The kg-remind hook keys its deterministic nudge on this flag: until it
        flips, every prompt reminds the model that the preload is a partial
        view. Stored as a timestamp for observability, read as a boolean.
        """
        self.ensure_session(session_id)
        self._sessions[session_id]["full_read_ts"] = time.time()

    def has_full_read(self, session_id: str) -> bool:
        """Has this session rendered the full graph at least once?"""
        session = self._sessions.get(session_id)
        return bool(session and session.get("full_read_ts"))

    def find_by_project_path(self, project_path: str) -> tuple[str, dict] | None:
        """Most recently started live session for a project path, or None.

        Concurrent sessions in one project resolve to the newest — the remind
        hook that calls this runs inside the newest session by construction.
        """
        try:
            resolved = str(safe_project_path(project_path))
        except ValueError:
            return None
        best = None
        for sid, data in self._sessions.items():
            if data.get("project_path") != resolved:
                continue
            if best is None or data.get("start_ts", 0) > best[1].get("start_ts", 0):
                best = (sid, data)
        return best

    def mark_synced(self, session_id: str) -> None:
        """Update last_synced_ts so kg_sync only returns changes after this point."""
        if session_id in self._sessions:
            self._sessions[session_id]["last_synced_ts"] = time.time()

    def get_sync_ts(self, session_id: str) -> float:
        """Get effective sync timestamp: last_synced_ts if present, else start_ts."""
        self.ensure_session(session_id)
        session = self._sessions[session_id]
        return session.get("last_synced_ts", session["start_ts"])

    def increment_ops(self, session_id: str) -> None:
        """Increment operation count for a session. Auto-recovers lost sessions."""
        self.ensure_session(session_id)
        self._sessions[session_id]["op_count"] = self._sessions[session_id].get("op_count", 0) + 1
        self._update_activity(session_id)

    def get_stats(self, session_id: str) -> dict:
        """Get session stats: duration, op count, graph sizes. Auto-recovers lost sessions."""
        self.ensure_session(session_id)
        session = self._sessions[session_id]
        now = time.time()
        return {
            "session_id": session_id,
            "duration_seconds": round(now - session["start_ts"]),
            "op_count": session.get("op_count", 0),
            "project_path": session.get("project_path"),
            "started_at": session["start_ts"],
        }

    def count(self) -> int:
        """Return number of active sessions."""
        return len(self._sessions)

    # ========================================================================
    # Session persistence (survive server restarts)
    # ========================================================================

    def _load_sessions(self) -> None:
        """Load sessions from disk on startup."""
        if not self._sessions_file.exists():
            return

        try:
            with open(self._sessions_file) as f:
                saved = json.load(f)

            now = time.time()
            restored = 0
            for sid, data in saved.items():
                # Skip expired sessions
                age = now - data.get("last_activity", 0)
                if age > self.session_ttl:
                    continue
                self._sessions[sid] = data
                restored += 1

            if restored:
                logger.info(f"Restored {restored} session(s) from disk")

        except Exception as e:
            logger.warning(f"Failed to load sessions from {self._sessions_file}: {e}")

    def save_sessions(self) -> None:
        """Save active sessions to disk. Called periodically by store's save loop."""
        try:
            self._sessions_file.parent.mkdir(parents=True, exist_ok=True)

            temp_path = self._sessions_file.with_suffix(".tmp")
            with open(temp_path, 'w') as f:
                json.dump(self._sessions, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            temp_path.replace(self._sessions_file)

        except Exception as e:
            logger.warning(f"Failed to save sessions: {e}")
            temp_path = self._sessions_file.with_suffix(".tmp")
            if temp_path.exists():
                temp_path.unlink()
