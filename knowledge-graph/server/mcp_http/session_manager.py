"""Session management for HTTP MCP server with project path tracking."""

import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from functools import wraps
from pathlib import Path
from core.constants import (SESSION_ID_LENGTH, SESSION_TTL_SECONDS, memory_project_root,
                            safe_project_path, sessions_file_path)

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


def safe_transcript_path(transcript_path: str) -> str | None:
    """A Claude Code transcript: a .jsonl file under the user's home."""
    home = str(Path.home().resolve())
    resolved = os.path.realpath(transcript_path)
    if not (resolved + "/").startswith(home + "/") or not resolved.endswith(".jsonl"):
        return None
    return resolved


def _scan_kg_sid(resolved: str, start: int = 0) -> tuple[str | None, int]:
    """(last KG session id our renders left after byte `start`, resume offset).

    The offset stops before an unfinished last line, so a later scan of the
    same file rereads it once complete instead of skipping it.
    """
    last = None
    offset = start
    try:
        with open(resolved, "rb") as f:
            f.seek(start)
            for raw in f:
                if raw.endswith(b"\n"):
                    offset += len(raw)
                if b"ession" not in raw:
                    continue
                line = raw.decode("utf-8", errors="replace")
                for pat in _KG_SID_PATTERNS:
                    for m in pat.finditer(line):
                        last = m.group(1)
    except OSError:
        return None, start
    return last, offset


def recover_kg_sid_from_transcript(transcript_path: str) -> str | None:
    """Last KG session id our renders left in a (possibly forked) transcript."""
    resolved = safe_transcript_path(transcript_path)
    return _scan_kg_sid(resolved)[0] if resolved else None


def _locked(method):
    """Run a session-manager method under the instance lock."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class HTTPSessionManager:
    """Manages sessions with project_path tracking for multi-project support.

    Sessions store project root paths (not graph file paths).
    The store layer resolves project roots to centralized graph paths.

    A session registered without a project is user-only (scope "user"): it
    reads and writes the user graph alone. A record recreated by
    ensure_session also has no project but no scope either: it lost its
    project, and kg_read asks for the cwd again instead of carrying on
    without it.

    Thread-safe: request handlers, the store's saver thread and the chore
    dispatcher all touch _sessions, and an unlocked iteration racing a
    registration raises mid-loop. Every method takes the reentrant _lock;
    the store's lock, when held, is always taken first.
    """

    def __init__(self, session_ttl: int = SESSION_TTL_SECONDS):
        self.session_ttl = session_ttl
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        # harness sid -> (transcript, bytes scanned, last KG sid found): an
        # unbound session's hooks rescan only what the transcript gained.
        self._transcript_scans: dict[str, tuple[str, int, str | None]] = {}
        self._sessions_file = sessions_file_path()
        self._load_sessions()

    @_locked
    def register(self, project_path: str | None = None, claude_sid: str | None = None,
                 harness: str | None = None) -> dict:
        """
        Register a new session with optional project root path.
        Returns {"session_id": str, "start_ts": float, "project_path": str | None}.

        Args:
            project_path: Absolute path to the project root directory. None,
                or a folder memory_project_root reads as no project, makes
                the session user-only.
            claude_sid: Claude Code session id to bind — ambient hooks resolve
                their KG session through this binding, so recall dedup follows
                the actual session instead of "newest in project". Codex sends
                its own session id in the same field; ids are UUIDs, so the
                two harnesses cannot collide.
            harness: which harness registered it (mcp_http.harness), recorded
                so the server can tell whether that harness's hooks run here.
        """
        session_id = uuid.uuid4().hex[:SESSION_ID_LENGTH]
        ts = time.time()

        root = memory_project_root(project_path) if project_path else None

        self._sessions[session_id] = {
            "start_ts": ts,
            "project_path": root,
            "last_activity": ts,
            "op_count": 0,
        }
        if root is None:
            self._sessions[session_id]["scope"] = "user"
        if harness:
            self._sessions[session_id]["harness"] = harness
        if claude_sid:
            self.bind_claude_sid(session_id, claude_sid, save=False)

        logger.info(f"Session registered: {session_id} (project: {root or 'none, user-only'})")
        self.save_sessions()  # Persist immediately so project_path survives restarts
        return {"session_id": session_id, "start_ts": ts, "project_path": root}

    @_locked
    def attach_project(self, session_id: str, cwd: str) -> str | None:
        """Give a session without a project the one cwd selects; returns it.

        The session keeps its id and everything it has already seen: a
        user-only conversation that turns to a project, or one whose record a
        restart lost, carries on rather than starting over. A cwd that selects
        no project leaves it user-only. Raises ValueError outside home.
        """
        root = memory_project_root(cwd)
        session = self._sessions[session_id]
        session["project_path"] = root
        if root:
            session.pop("scope", None)
        else:
            session["scope"] = "user"
        self.save_sessions()
        return root

    @staticmethod
    def scope_matches(data: dict, cwd: str | None) -> bool:
        """Is this session's memory scope the one cwd selects?"""
        try:
            root = memory_project_root(cwd) if cwd else None
        except ValueError:
            return False
        return data.get("project_path") == root if root else data.get("scope") == "user"

    @_locked
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

    @_locked
    def fork(self, parent_sid: str, claude_sid: str) -> str | None:
        """Clone a KG session for a Claude session forked off it; returns the new id.

        The fork's context is a copy of the parent's, so it inherits the
        parent's seen/preload/read state as of now. The parent may still be
        alive, so it keeps its own record and binding: moving the binding
        left the parent's hooks resolving newest-by-path to another session.
        """
        parent = self._sessions.get(parent_sid)
        if parent is None:
            return None
        clone = copy.deepcopy(parent)
        ts = time.time()
        clone.update(start_ts=ts, last_activity=ts, op_count=0, forked_from=parent_sid,
                     last_synced_ts=parent.get("last_synced_ts", parent["start_ts"]))
        clone.pop("claude_sid", None)
        # Pending output belongs to its original conversation, not a fork.
        for key in ("agy_pending", "agy_delivery", "agy_hooks_seen", "agy_prompt_key"):
            clone.pop(key, None)
        session_id = uuid.uuid4().hex[:SESSION_ID_LENGTH]
        self._sessions[session_id] = clone
        self.bind_claude_sid(session_id, claude_sid)
        logger.info(f"Session forked: {parent_sid} -> {session_id}")
        return session_id

    @_locked
    def find_by_claude_sid(self, claude_sid: str) -> tuple[str, dict] | None:
        """KG session bound to this Claude Code session id, or None."""
        if not claude_sid:
            return None
        for sid, data in self._sessions.items():
            if data.get("claude_sid") == claude_sid:
                return (sid, data)
        return None

    def resolve_hook_session(self, harness_sid: str | None, cwd: str | None,
                             transcript_path: str | None = None) -> tuple[str, dict] | None:
        """The KG session a hook event belongs to, or None.

        A supplied harness id resolves to its own binding only, or to a
        session its transcript proves it used: the id our kg_read footer left
        there when no preload made the binding. Falling back to the newest
        session in the project handed one conversation another's seen,
        full-read and throttle state. Events without an id, from hooks older
        than the binding, still resolve by project.
        """
        if not harness_sid:
            return self.find_by_project_path(cwd) if cwd else None
        hit = self.find_by_claude_sid(harness_sid)
        if hit or not transcript_path:
            return hit
        cand = self._transcript_kg_sid(harness_sid, transcript_path)
        with self._lock:
            data = self._sessions.get(cand) if cand else None
            # Bound elsewhere, or another scope: an id quoted from some other
            # conversation, not this one's own session.
            if data is None or data.get("claude_sid") or not self.scope_matches(data, cwd):
                return None
            self.bind_claude_sid(cand, harness_sid)
            return cand, data

    def _transcript_kg_sid(self, harness_sid: str, transcript_path: str) -> str | None:
        """Last KG sid in the transcript, scanning only bytes new since last time."""
        resolved = safe_transcript_path(transcript_path)
        if resolved is None:
            return None
        with self._lock:
            prev = self._transcript_scans.get(harness_sid)
        start, last = (prev[1], prev[2]) if prev and prev[0] == resolved else (0, None)
        try:
            if os.path.getsize(resolved) < start:     # rewritten, not appended
                start, last = 0, None
        except OSError:
            return None
        found, offset = _scan_kg_sid(resolved, start)
        last = found or last
        with self._lock:
            if len(self._transcript_scans) > 256:
                self._transcript_scans.clear()
            self._transcript_scans[harness_sid] = (resolved, offset, last)
        return last

    @_locked
    def lookup(self, session_id: str) -> dict | None:
        """Return the session record if it exists — no auto-recovery, no mutation.

        kg_read uses this to decide whether a caller-supplied session_id can be
        reused (it must exist AND carry a project_path or user-only scope).
        ensure_session would silently create a path-less session here, which
        breaks project reads.
        """
        return self._sessions.get(session_id)

    @_locked
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

    @_locked
    def get_project_path(self, session_id: str) -> str | None:
        """Get project root path for a session. Auto-recovers lost sessions."""
        self.ensure_session(session_id)
        self._update_activity(session_id)
        return self._sessions[session_id]["project_path"]

    @_locked
    def get_start_ts(self, session_id: str) -> float:
        """Get session start timestamp. Auto-recovers lost sessions."""
        self.ensure_session(session_id)
        return self._sessions[session_id]["start_ts"]

    @_locked
    def _update_activity(self, session_id: str):
        """Update last activity timestamp for a session."""
        if session_id in self._sessions:
            self._sessions[session_id]["last_activity"] = time.time()

    @_locked
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

    @_locked
    def mark_seen(self, session_id: str, node_ids, via: str,
                  at: float | None = None, full: bool = False) -> None:
        """Record node ids whose GIST this session has already been shown.

        Feeds search dedup: a hit the session has already seen renders as a
        one-line gist reminder, never a repeated notes dump. Stored as a list
        (JSON-serializable), deduped on insert. `via` is kept per node for the
        FIRST route only (preload, full_read, ambient, file, search, read) — the
        endorsement log reads it to tell surfaced nodes from dug-up ones.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        seen = session.setdefault("seen_ids", [])
        seen_via = session.setdefault("seen_via", {})
        seen_set = set(seen)
        node_ids = list(node_ids)
        for nid in node_ids:
            if nid not in seen_set:
                seen.append(nid)
                seen_set.add(nid)
            seen_via.setdefault(nid, via)
        if at is not None:
            self._note(session, node_ids, at, full)

    @staticmethod
    def _note(session: dict, node_ids, at: float, full: bool) -> None:
        for key in ("seen_at", "read_at") if full else ("seen_at",):
            times = session.setdefault(key, {})
            for nid in node_ids:
                if at > times.get(nid, 0):
                    times[nid] = at

    @_locked
    def note_viewed(self, session_id: str, node_ids, at: float, full: bool = False) -> None:
        """Record that this session saw these nodes as they stood at `at`.

        `at` must be taken BEFORE the content was read: a write that lands
        between the read and this call is then newer than the view, as it
        should be. `full` means notes and touches were shown too, not only the
        gist. put_node compares these times with a node's last content write.
        """
        session = self._sessions.get(session_id)
        if session is not None:
            self._note(session, node_ids, at, full)

    @_locked
    def viewed_at(self, session_id: str, node_id: str, full: bool = False) -> float | None:
        """When this session last saw the node (in full, with full=True)."""
        session = self._sessions.get(session_id) or {}
        return (session.get("read_at" if full else "seen_at") or {}).get(node_id)

    @_locked
    def mark_promoted(self, session_id: str, node_ids) -> None:
        """Record nodes this session pulled out of the archive by reading them."""
        session = self._sessions.get(session_id)
        if session is None or not node_ids:
            return
        promoted = session.setdefault("promoted_ids", [])
        promoted.extend(nid for nid in node_ids if nid not in promoted)

    @_locked
    def get_seen(self, session_id: str) -> set:
        """Set of node ids this session has already seen gists for."""
        session = self._sessions.get(session_id)
        return set(session.get("seen_ids", [])) if session else set()

    @_locked
    def recently_seen_ids(self, max_age_seconds: float) -> set:
        """Every node id a recently-active session holds in context.

        Maintenance chores subtract this: rewriting a gist or renaming a node
        while a live session has it in context makes that session's memory
        quietly wrong, which is the one way in-session gardening could be
        worse than none at all.
        """
        cutoff = time.time() - max_age_seconds
        out: set = set()
        for session in self._sessions.values():
            if (session.get("last_activity") or 0) < cutoff:
                continue
            out.update(session.get("seen_ids", []))
            out.update(session.get("preloaded_ids", []))
        return out

    @_locked
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

    @_locked
    def get_preloaded(self, session_id: str) -> set:
        """Node ids whose gists the session-start preload put in context."""
        session = self._sessions.get(session_id)
        return set(session.get("preloaded_ids", [])) if session else set()

    @_locked
    def rename_node_ref(self, old_id: str, new_id: str) -> int:
        """Carry a renamed node through every session's seen/preload/promoted state.

        Without this a session that has already seen the node under its old
        name loses dedup: the renamed node reads as unseen and gets re-dumped
        with its notes, and a preloaded anchor line reappears in full. Cheap
        to do, invisible when skipped until it is annoying.
        """
        touched = 0
        for session in self._sessions.values():
            for field in ("seen_ids", "preloaded_ids", "promoted_ids"):
                ids = session.get(field)
                if not ids or old_id not in ids:
                    continue
                session[field] = list(dict.fromkeys(
                    new_id if nid == old_id else nid for nid in ids
                ))
                touched += 1
            seen_via = session.get("seen_via")
            if seen_via and old_id in seen_via:
                seen_via.setdefault(new_id, seen_via.pop(old_id))
            for key in ("seen_at", "read_at"):
                times = session.get(key)
                if times and old_id in times:
                    times[new_id] = max(times.pop(old_id), times.get(new_id, 0))
        return touched

    @_locked
    def mark_full_read(self, session_id: str) -> None:
        """Record that this session has made the loud full-graph kg_read.

        The kg-remind hook keys its deterministic nudge on this flag: until it
        flips, every prompt reminds the model that the preload is a partial
        view. Stored as a timestamp for observability, read as a boolean.
        """
        self.ensure_session(session_id)
        self._sessions[session_id]["full_read_ts"] = time.time()

    @_locked
    def has_full_read(self, session_id: str) -> bool:
        """Has this session rendered the full graph at least once?"""
        session = self._sessions.get(session_id)
        return bool(session and session.get("full_read_ts"))

    @_locked
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

    @_locked
    def hooks_seen(self, project_path: str, harness: str) -> bool:
        """Has a hook from this harness ever preloaded a live session here?

        The session-start hook is the only path that sets preloaded_ids, so a
        session carrying both is proof the harness runs the plugin's hooks.
        """
        for data in self._sessions.values():
            if (data.get("project_path") == project_path and data.get("harness") == harness
                    and "preloaded_ids" in data):
                return True
        return False

    @_locked
    def mark_synced(self, session_id: str, at: float | None = None) -> None:
        """Update last_synced_ts so kg_sync only returns changes after this point."""
        if session_id in self._sessions:
            data = self._sessions[session_id]
            data["last_synced_ts"] = max(data.get("last_synced_ts", 0),
                                         at if at is not None else time.time())

    @_locked
    def queue_context(self, session_id: str, text: str, kind: str, effects=()) -> bool:
        from .delivery import enqueue
        data = self._sessions.get(session_id)
        if data is None or not enqueue(data, text, kind, list(effects)):
            return False
        self.save_sessions()
        return True

    @_locked
    def has_pending_context(self, session_id: str, kind: str | None = None) -> bool:
        queue = (self._sessions.get(session_id) or {}).get("agy_pending") or []
        return any(item["kind"] == kind for item in queue) if kind else bool(queue)

    @_locked
    def prepare_context(self, session_id: str) -> dict | None:
        from .delivery import prepare
        data = self._sessions.get(session_id)
        if data is None:
            return None
        packet = prepare(data, session_id)
        if packet:
            self.save_sessions()
        return packet

    @_locked
    def acknowledge_context(self, session_id: str, delivery_id: str) -> list | None:
        """Commit a delivered packet's session effects. Returns its graph
        effects for the caller to apply through the store, or None when the
        acknowledgement does not match the outstanding packet."""
        from .delivery import acknowledge
        data = self._sessions.get(session_id)
        graph = acknowledge(self, data, delivery_id) if data is not None else None
        if graph is not None:
            self.save_sessions()
        return graph

    @_locked
    def note_antigravity_hook(self, session_id: str, prompt_key: str | None = None) -> bool:
        """Record evidence for this conversation; dedup repeated prompt hooks."""
        data = self._sessions[session_id]
        data["agy_hooks_seen"] = True
        self._update_activity(session_id)
        if prompt_key is None:
            return False
        fresh = data.get("agy_prompt_key") != prompt_key
        data["agy_prompt_key"] = prompt_key
        return fresh

    @_locked
    def get_sync_ts(self, session_id: str) -> float:
        """Get effective sync timestamp: last_synced_ts if present, else start_ts."""
        self.ensure_session(session_id)
        session = self._sessions[session_id]
        return session.get("last_synced_ts", session["start_ts"])

    @_locked
    def increment_ops(self, session_id: str) -> None:
        """Increment operation count for a session. Auto-recovers lost sessions."""
        self.ensure_session(session_id)
        self._sessions[session_id]["op_count"] = self._sessions[session_id].get("op_count", 0) + 1
        self._update_activity(session_id)

    @_locked
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

    @_locked
    def count(self) -> int:
        """Return number of active sessions."""
        return len(self._sessions)

    # ========================================================================
    # Session persistence (survive server restarts)
    # ========================================================================

    @_locked
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

    @_locked
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
