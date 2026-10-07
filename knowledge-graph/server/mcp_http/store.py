"""Multi-project knowledge graph store for HTTP MCP server."""

import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field

from core import (
    CharEstimator,
    NodeScorer,
    Compactor,
    GraphPersistence,
    Graph,
    FRESH_BUDGET_RATIO,
    ORPHAN_GRACE_DAYS,
    MAX_CHARS_PER_LEVEL,
    heal_node_fields,
    gist_is_malformed,
    is_archived,
    version_key_node,
    version_key_edge,
    NodeNotFoundError,
    SessionNotFoundError,
    KGError,
    validate_level,
    MAINTAIN_NAMESPACE,
    maintain_graph_path,
    validate_node_id,
    validate_new_node_id,
    validate_rel,
    validate_edge_ref,
    get_storage_root,
    project_graph_path,
    user_graph_path,
    safe_project_path,
    project_namespace,
    project_slug,
    is_project_namespace,
)
from core.search import (
    _FIELD_W_ID, _FIELD_W_GIST, _FIELD_W_REST, _stem, search_terms,
    rank_nodes, connection_paths,
)
from core.exceptions import NodeConflictError
from core import recurrence
from core.constants import (
    WRITTEN_FIELD, GIST_TS_FIELD, GIST_TS_MAX, IDF_SHARPNESS, NEAR_DUP_MIN_SCORE, NEAR_DUP_RATIO,
    PROGRESS_TRAIL_KEY, PROGRESS_TRAIL_LIST_ITEMS, PROGRESS_TRAIL_MAX,
    PROGRESS_TRAIL_VALUE_CHARS, CHORE_TASK_ID, LIFT_EDGE_REL,
    USEFUL_LOG_MAX_BYTES, USEFUL_LOG_NAME,
)
from core.persistence import (
    append_jsonl, references, rename_would_capture, rename_would_capture_on_disk,
    rewrite_edge_refs_on_disk,
)
from .session_manager import HTTPSessionManager

logger = logging.getLogger(__name__)


def _trail_entry(state: dict) -> dict:
    """A compact, size-bounded copy of one stamp for the progress trail.

    Values are clipped rather than dropped: a trail is only useful if it is
    readable at a glance, and an unbounded copy of every stamp would grow the
    graph file without bound for a task that stamps often.
    """
    entry: dict = {"_ts": round(time.time(), 3)}
    for k, v in state.items():
        if k == PROGRESS_TRAIL_KEY:
            continue
        if isinstance(v, str):
            v = v[:PROGRESS_TRAIL_VALUE_CHARS]
        elif isinstance(v, (list, tuple)):
            v = [str(x)[:PROGRESS_TRAIL_VALUE_CHARS]
                 for x in list(v)[:PROGRESS_TRAIL_LIST_ITEMS]]
        elif isinstance(v, dict):
            v = str(v)[:PROGRESS_TRAIL_VALUE_CHARS]
        elif not isinstance(v, (int, float, bool)) and v is not None:
            v = str(v)[:PROGRESS_TRAIL_VALUE_CHARS]
        entry[k] = v
    return entry


@dataclass
class GraphConfig:
    """Configuration for knowledge graph."""
    max_chars: int = MAX_CHARS_PER_LEVEL
    orphan_grace_days: int = ORPHAN_GRACE_DAYS
    save_interval: int = 30
    storage_root: Path = field(default_factory=get_storage_root)
    user_path: Path = field(default_factory=user_graph_path)


class MultiProjectGraphStore:
    """
    Multi-project knowledge graph store.

    Structure:
    - graphs["user"] = shared user graph
    - graphs["project:<project_root>"] = project-specific graphs

    Storage:
    - User graph: ~/.knowledge-graph/user.json
    - Project graphs: ~/.knowledge-graph/projects/<slug>/graph.json
    """

    def __init__(self, config: GraphConfig, session_manager: HTTPSessionManager, broadcast_callback=None):
        self.config = config
        self.session_manager = session_manager
        self.broadcast_callback = broadcast_callback

        # Initialize components
        self.estimator = CharEstimator()
        self.scorer = NodeScorer(int(config.max_chars * FRESH_BUDGET_RATIO))
        self.compactor = Compactor(self.scorer, self.estimator, config.max_chars)

        # Graph storage: key = "user" or "project:<project_root>"
        self.graphs: dict[str, Graph] = {}
        self._versions: dict[str, dict] = {}
        self._progress: dict[str, dict] = {}
        # Sessions doing maintenance (a pass or a chore): their full reads are
        # judgement, not use, so they neither stamp recency nor promote.
        self._maintenance_sessions: set[str] = set()
        self._persistence: dict[str, GraphPersistence] = {}

        # Thread safety
        self.lock = threading.RLock()
        self.dirty: dict[str, bool] = {}
        # Per-graph write generation: bumped on every write-through, prune and
        # load, so derived indexes (file recall's touches index) know when to
        # rebuild without being told about each mutation path.
        self.write_gen: dict[str, int] = {}

        # Background saver
        self.running = True
        self._stop_event = threading.Event()  # wakes the saver thread for fast shutdown
        self.saver_thread = threading.Thread(target=self._periodic_save, daemon=True)

        # Load user graph
        self._load_user_graph()
        self.saver_thread.start()

        logger.info("Multi-project graph store initialized")

    def _load_with_fallback(self, persistence: GraphPersistence) -> tuple:
        """Load graph, falling back to .prev backup if primary file is corrupt."""
        try:
            return persistence.load()
        except Exception as e:
            prev_path = persistence.path.with_suffix(".prev")
            if prev_path.exists():
                logger.error(
                    f"Graph file corrupt ({persistence.path}): {e}. "
                    f"Attempting recovery from {prev_path}"
                )
                backup_persistence = GraphPersistence(prev_path)
                try:
                    result = backup_persistence.load()
                    logger.warning(f"Recovered graph from {prev_path} — primary file needs inspection")
                    return result
                except Exception as e2:
                    logger.error(f"Backup recovery also failed: {e2}")
            raise RuntimeError(
                f"Cannot load graph from {persistence.path}: {e}. "
                f"Inspect the file manually or restore from backup."
            ) from e

    def _load_user_graph(self):
        """Load the shared user graph."""
        with self.lock:
            user_key = "user"
            persistence = GraphPersistence(self.config.user_path)
            graph, versions, progress = self._load_with_fallback(persistence)

            # Clean up orphaned edges (edges pointing to non-existent nodes)
            self._clean_orphaned_edges(graph)
            # Heal nodes whose gist swallowed their notes (one-time repair, idempotent)
            healed = self._heal_corrupt_nodes(graph)
            credited = self._reconcile_recurrence(graph, None, user_key)

            self.graphs[user_key] = graph
            self._bump_gen(user_key)
            self._versions[user_key] = versions
            self._progress[user_key] = progress
            self._persistence[user_key] = persistence
            self.dirty[user_key] = False

            # Persist the heal so the repair sticks and the next load is a no-op
            if healed or credited:
                self.dirty[user_key] = True
                self._write_through(user_key)

            logger.info(f"Loaded user graph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")

    def _ensure_project_loaded(self, project_root: str, force_reload: bool = False):
        """
        Load a project graph if not already loaded. Caller must hold lock.
        project_root: Absolute path to project root directory.
        force_reload: If True, reload from disk even if already cached.
        """
        project_key = project_namespace(project_root)

        if project_key in self.graphs and not force_reload:
            return

        # Resolve project root to centralized graph path
        # (handles renames via alias lookup and auto-migration)
        graph_path = project_graph_path(project_root)

        # Load from disk (project_path is stamped into _meta for rename detection)
        persistence = GraphPersistence(graph_path, project_path=project_root)
        graph, versions, progress = self._load_with_fallback(persistence)

        # Clean up orphaned edges (edges pointing to non-existent nodes)
        self._clean_orphaned_edges(graph)
        # Heal nodes whose gist swallowed their notes (one-time repair, idempotent)
        healed = self._heal_corrupt_nodes(graph)
        user_nodes = self.graphs.get("user", {}).get("nodes")
        credited = self._reconcile_recurrence(graph, user_nodes, project_key)

        self.graphs[project_key] = graph
        self._bump_gen(project_key)
        self._versions[project_key] = versions
        self._progress[project_key] = progress
        self._persistence[project_key] = persistence
        self.dirty[project_key] = False

        # Persist the heal so the repair sticks and the next load is a no-op
        if healed or credited:
            self.dirty[project_key] = True
            self._write_through(project_key)
        if any(c["cross_level"] for c in credited):
            self.dirty["user"] = True
            self._write_through("user")

        logger.info(f"Loaded project graph for {project_root}: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges (path: {graph_path})")

    def _ensure_maintain_loaded(self):
        """Load the maintenance-lessons graph if not already loaded.

        Lazy on purpose: this graph is the chore agent's own memory, touched
        only by maintenance work, so the overwhelming majority of sessions
        never pay for it. Caller must hold the lock.
        """
        if MAINTAIN_NAMESPACE in self.graphs:
            return
        persistence = GraphPersistence(maintain_graph_path())
        graph, versions, progress = self._load_with_fallback(persistence)
        self._clean_orphaned_edges(graph)
        self.graphs[MAINTAIN_NAMESPACE] = graph
        self._versions[MAINTAIN_NAMESPACE] = versions
        self._progress[MAINTAIN_NAMESPACE] = progress
        self._persistence[MAINTAIN_NAMESPACE] = persistence
        self.dirty[MAINTAIN_NAMESPACE] = False
        logger.info(f"Loaded maintain graph: {len(graph['nodes'])} lesson(s)")

    def _get_graph_key(self, level: str, session_id: str | None) -> str:
        """Get the graph storage key for a level and session."""
        validate_level(level)

        if level == "user":
            return "user"
        elif level == "maintain":
            # No session or project needed — one maintenance memory per machine,
            # shared by every chore in every project. That sharing is the point:
            # a lesson learned gardening one graph is worth nothing if it cannot
            # reach the next.
            self._ensure_maintain_loaded()
            return MAINTAIN_NAMESPACE
        else:  # level == "project"
            if not session_id:
                raise ValueError("session_id required for project-level operations")

            project_root = self.session_manager.get_project_path(session_id)
            if not project_root:
                # Never redirected to the user graph: the caller chose a level.
                raise ValueError(
                    f"Session {session_id} has no project. To write project memory, first "
                    f"attach one: kg_read(session_id='{session_id}', cwd='<project root>'). "
                    "Cross-project knowledge belongs at level='user'.")

            return project_namespace(project_root)

    def _resolve_graph_key(self, level: str, session_id: str | None,
                           project_path: str | None) -> tuple[str, str]:
        """Resolve (level, graph_key) for an explicit level, loading the project
        graph if needed. Caller must hold lock.

        project_path resolves a project graph directly and takes PRECEDENCE over
        session_id whenever both are present: the visual editor sends both, but
        its WebSocket session has no project_path registered, so a session-based
        lookup would fail. Used by every node/edge operation so read, write and
        delete all accept the same addressing.
        """
        if level == "project" and project_path:
            project_root = str(safe_project_path(project_path))
            self._ensure_project_loaded(project_root)
            return "project", project_namespace(project_root)

        graph_key = self._get_graph_key(level, session_id)
        if is_project_namespace(graph_key):
            project_root = graph_key.split(":", 1)[1]
            self._ensure_project_loaded(project_root)
        return level, graph_key

    def _bump_version(self, graph_key: str, key: str, session_id: str | None = None) -> dict:
        """Increment version for a key and return new version. Caller must hold lock.

        ts is when the key last changed (sync and stale-write guards need every
        change). A maintenance session's change is not use, so it carries the
        previous activity time forward as used_ts, which recency prefers.
        """
        ts = time.time()
        current = self._versions[graph_key].get(key, {"v": 0})
        new_ver = {"v": current["v"] + 1, "ts": ts, "session": session_id}
        if session_id in self._maintenance_sessions:
            new_ver["used_ts"] = current.get("used_ts", current.get("ts", 0))
        self._versions[graph_key][key] = new_ver
        return new_ver

    def _broadcast(self, message: dict, level: str, session_id: str | None = None):
        """Broadcast a change notification. Thread-safe."""
        if not self.broadcast_callback:
            return

        project_path = None
        if level == "project" and session_id:
            try:
                project_path = self.session_manager.get_project_path(session_id)
            except Exception:
                pass

        # Schedule on event loop
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            asyncio.create_task(
                self.broadcast_callback(project_path, message, session_id)
            )
        except RuntimeError:
            logger.warning("Cannot broadcast: no event loop running")
        except Exception as e:
            logger.error(f"Error broadcasting: {e}")

    def _bump_gen(self, graph_key: str):
        """Mark a graph changed for derived indexes. Caller must hold lock."""
        self.write_gen[graph_key] = self.write_gen.get(graph_key, 0) + 1

    def _write_through(self, graph_key: str):
        """Immediately save a graph to disk after mutation. Caller must hold lock.

        A failed save leaves the graph dirty, so the saver retries it and
        shutdown flushes it — clearing the flag would drop the write silently.
        """
        self._bump_gen(graph_key)
        if graph_key in self._persistence:
            self.dirty[graph_key] = not self._save_to_disk(graph_key)

    # ========================================================================
    # Public API
    # ========================================================================

    def _warn_reload_discards(self, graph_key: str):
        """Log a disk-wins reload that is about to drop unsaved changes. Caller holds lock."""
        if self.dirty.get(graph_key):
            logger.warning(f"Reload of {graph_key} from disk discards unsaved in-memory changes")

    def reload_user_graph(self):
        """Force reload user graph from disk. Thread-safe."""
        with self.lock:
            self._load_user_graph()
            logger.info("User graph reloaded from disk")

    def reload_project_graph(self, project_root: str):
        """Force reload a specific project graph from disk. Thread-safe."""
        with self.lock:
            self._ensure_project_loaded(project_root, force_reload=True)
            logger.info(f"Project graph reloaded from disk: {project_root}")

    def read_graphs(self, session_id: str | None = None, project_path: str | None = None, force_reload: bool = False) -> dict:
        """
        Read all accessible graphs for a session or project.

        Args:
            session_id: Session ID (uses session's registered project path)
            project_path: Direct project root path (alternative to session_id)
            force_reload: If True, reload graphs from disk before returning.
                          Use when data may have been modified externally.
                          Disk wins: that is what makes a restore from .prev
                          take effect, so unsaved memory is dropped, and logged.

        Returns dict with "user" and "project" keys.
        """
        with self.lock:
            # Reload user graph from disk if requested
            if force_reload:
                self._warn_reload_discards("user")
                self._load_user_graph()

            # Shallow-copy each node/edge dict: callers serialize the result after
            # the lock is released, and the background maintenance thread mutates
            # these dicts in place (archival flags, healing) — live references
            # would race with that serialization.
            def snapshot(graph: dict) -> dict:
                return {
                    "nodes": [dict(n) for n in graph["nodes"].values()],
                    "edges": [dict(e) for e in graph["edges"].values()],
                }

            result = {
                "user": snapshot(self.graphs["user"]),
                "project": {"nodes": [], "edges": []}
            }

            # Determine project root
            project_root = None

            logger.info(f"read_graphs called with session_id={session_id}, project_path={project_path}, force_reload={force_reload}")

            if session_id:
                try:
                    project_root = self.session_manager.get_project_path(session_id)
                except Exception as e:
                    logger.warning(f"Could not get project path for session {session_id}: {e}")

            elif project_path:
                # Direct project path provided (e.g., from visual editor)
                project_root = str(safe_project_path(project_path))

            # Load project graph if we have a path
            if project_root:
                try:
                    project_key = project_namespace(project_root)
                    if force_reload:
                        self._warn_reload_discards(project_key)
                    self._ensure_project_loaded(project_root, force_reload=force_reload)

                    result["project"] = snapshot(self.graphs[project_key])
                except Exception as e:
                    logger.warning(f"Could not load project graph for {project_root}: {e}")

            return result

    def mark_useful(self, node_ids: list, session_id: str) -> dict:
        """Record explicit usefulness endorsements ("likes") on nodes.

        The usefulness signal that feeds the scorer, and the only writer of
        _useful_ts. Two kinds of endorsement share it: a node that HELPED (it
        was on the surface and the work went differently for it, judged at
        wrap-up) and a node that was MISSING (it existed, the session needed
        it, and nothing surfaced it — sent as soon as the gap is established).
        The second is what lets a wrong archival decision be corrected at all;
        without it the scorer only ever hears about its hits.

        Endorsement, not traffic — but the budget is guidance backed by a
        flood stop, not a wall: LIKES_GUIDANCE_PER_SESSION is what the doctrine
        asks for, MAX_LIKES_PER_SESSION is where it actually refuses, and the
        gap between them exists because a session that keeps finding real
        signal must be able to report it. One vote per node per session; the
        per-session ledger lives on the session record, the decaying timestamps
        on the node (_useful_ts). Reads deliberately don't feed this signal.

        A like is not a content write: node versions are untouched, so liking
        never resets recency or sync state.

        Every id, accepted or refused, also appends a line to useful.jsonl
        naming how the node first reached the session (constants: USEFUL_LOG_NAME).

        Returns {"accepted": [ids], "rejected": {id: reason},
                 "remaining": endorsements left before the hard cap,
                 "over_guidance": how far past the guidance this session is}.
        """
        from core.constants import (LIKES_GUIDANCE_PER_SESSION, MAX_LIKES_PER_SESSION,
                                    SURFACE_VIAS, USEFUL_LOG_MAX_BYTES, USEFUL_LOG_NAME)

        with self.lock:
            session = self.session_manager.lookup(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            liked = session.setdefault("liked_ids", [])
            seen_via = session.get("seen_via", {})
            seen = set(session.get("seen_ids", []))
            promoted = set(session.get("promoted_ids", []))

            accepted: list = []
            rejected: dict = {}
            records: list = []
            now = time.time()

            def record(node_id, refused=None, level=None, node=None):
                # Seen with no route: the sighting predates route tracking
                # (a session that spans the upgrade). Not the same as never shown.
                via = seen_via.get(node_id) or ("unknown" if node_id in seen else None)
                rec = {
                    "ts": round(now, 3),
                    "kg_session": session_id,
                    "claude_session": session.get("claude_sid"),
                    "project": session.get("project_path"),
                    "id": node_id,
                    "level": level,
                    "via": via,
                    "surfaced": via in SURFACE_VIAS,
                    "promoted": node_id in promoted,
                    "archived": node is not None and (is_archived(node) or "_orphaned_ts" in node),
                    "session_likes": len(liked),
                }
                if refused:
                    rec["refused"] = refused
                records.append(rec)

            for node_id in node_ids:
                if node_id in liked:
                    rejected[node_id] = "already liked this session"
                    record(node_id, "duplicate")
                    continue
                if len(liked) >= MAX_LIKES_PER_SESSION:
                    rejected[node_id] = (
                        f"hard cap reached — {MAX_LIKES_PER_SESSION} nodes already "
                        f"endorsed this session"
                    )
                    record(node_id, "cap")
                    continue
                found = self.find_node_level(node_id, session_id)
                if not found:
                    rejected[node_id] = "not found"
                    record(node_id, "not_found")
                    continue
                level, graph_key = found
                node = self.graphs[graph_key]["nodes"][node_id]
                node.setdefault("_useful_ts", []).append(now)
                liked.append(node_id)
                accepted.append(node_id)
                record(node_id, level=level, node=node)
                self.dirty[graph_key] = True
                self._write_through(graph_key)

        log_path = get_storage_root() / USEFUL_LOG_NAME
        for rec in records:
            append_jsonl(log_path, rec, USEFUL_LOG_MAX_BYTES)

        return {
            "accepted": accepted,
            "rejected": rejected,
            "remaining": max(0, MAX_LIKES_PER_SESSION - len(liked)),
            "over_guidance": max(0, len(liked) - LIKES_GUIDANCE_PER_SESSION),
        }

    def scores_for_read(self, session_id: str | None = None) -> dict:
        """Node scores per level for kg_read's degradation ladder.

        Returns {"user": {node_id: score}, "project": {node_id: score}} scored
        with archived nodes included (one comparable pool). Fresh-tier nodes are
        absent — the ladder treats missing as "keep" for active nodes, and as
        0.0 (first to drop) for an archived one, a transient state from before
        the tier that refill and rebalance restore first.
        """
        with self.lock:
            result = {"user": {}, "project": {}}
            keys = [("user", "user")]
            if session_id:
                project_root = self.session_manager.get_project_path(session_id)
                if project_root:
                    keys.append(("project", project_namespace(project_root)))
            for label, graph_key in keys:
                graph = self.graphs.get(graph_key)
                if not graph:
                    continue
                result[label] = self.scorer.score_all(
                    graph["nodes"], graph["edges"],
                    self._versions.get(graph_key, {}),
                    include_archived=True,
                )
            return result

    def node_score(self, node_id: str, level: str = "user", session_id: str | None = None,
                   project_path: str | None = None) -> dict:
        """Read-only explanation using the live scorer, versions and config.

        This does not read/recall a node or mark it seen. Take the explanation
        under the same lock as compaction so all factors use one stable graph.
        """
        with self.lock:
            _level, key = self._resolve_graph_key(level, session_id, project_path)
            graph = self.graphs[key]
            if node_id not in graph["nodes"]:
                raise NodeNotFoundError(level, node_id)
            return self.scorer.explain(node_id, graph["nodes"], graph["edges"],
                                       self._versions.get(key, {}))

    def maintain_lessons(self, include_archived: bool = False) -> list[dict]:
        """The maintenance memory's lessons, best-scored first.

        Read by the chore dispatcher, which renders them INTO the chore prompt
        rather than making the chore fetch them: a lesson that costs a tool
        call is a lesson that gets skipped, and this memory only earns its
        keep if every chore starts with it already in front of them.
        """
        with self.lock:
            self._ensure_maintain_loaded()
            graph = self.graphs[MAINTAIN_NAMESPACE]
            scores = self.scorer.score_all(
                graph["nodes"], graph["edges"],
                self._versions.get(MAINTAIN_NAMESPACE, {}),
                include_archived=True,
            )
            nodes = [
                dict(n) for n in graph["nodes"].values()
                if include_archived or not n.get("_archived")
            ]
        nodes.sort(key=lambda n: -scores.get(n["id"], 0.0))
        return nodes

    def maintain_snapshot(self) -> dict:
        """{"nodes": [...best-scored first...], "edges": [...]} for rendering."""
        nodes = self.maintain_lessons(include_archived=True)
        with self.lock:
            self._ensure_maintain_loaded()
            edges = [dict(e) for e in self.graphs[MAINTAIN_NAMESPACE]["edges"].values()]
        return {"nodes": nodes, "edges": edges}

    def put_node(
        self,
        level: str,
        node_id: str,
        gist: str,
        notes: list[str] | None = None,
        touches: list[str] | None = None,
        session_id: str | None = None,
        project_path: str | None = None,
        guard: bool = True,
    ) -> dict:
        """Create or update a node.

        project_path resolves a project graph directly (visual editor) — see
        _resolve_graph_key. guard=False skips the stale-view refusal for a
        human edit made on a screen showing the whole node (the editor); it
        still stamps the write, so agents holding an older view are refused.
        """
        validate_node_id(node_id)
        with self.lock:
            level, graph_key = self._resolve_graph_key(level, session_id, project_path)

            nodes = self.graphs[graph_key]["nodes"]

            # Heal on write: occasionally a client serializes the whole node
            # (gist + notes + tool-call markup) into the gist string. Repair it
            # before storing so corruption never lands and notes survive as a
            # structured field. No-op for well-formed input.
            gist, notes, touches = heal_node_fields(gist, notes, touches)

            # Create or update node. The id length rule applies only to a
            # CREATE: an update, promotion or rewire of a node named before the
            # rule existed must stay writable, or the graph's own history
            # becomes read-only.
            is_new = node_id not in nodes
            if is_new:
                validate_new_node_id(node_id)
            elif session_id and guard:
                self._refuse_stale_write(level, node_id, nodes[node_id], session_id,
                                         notes, touches)
            node = nodes.get(node_id, {"id": node_id})
            changed = (is_new or node.get("gist") != gist
                       or (notes is not None and node.get("notes") != notes)
                       or (touches is not None and node.get("touches") != touches))
            # A real gist change is a rewrite; the churn guard counts these
            # (core.chores.is_churning). Not the version counter: that also
            # bumps when a read promotes the node out of the archive, and a
            # put that re-sends the same gist changes nothing.
            if not is_new and node.get("gist") != gist:
                stamps = list(node.get(GIST_TS_FIELD) or []) + [time.time()]
                node[GIST_TS_FIELD] = stamps[-GIST_TS_MAX:]
            # A case added to a lesson another session wrote is that lesson
            # recognised again — measured: 44% of such updates carried no
            # kg_useful, so the recognition went uncounted.
            note_credit = (not is_new and bool(session_id) and notes is not None
                           and len(notes) > len(node.get("notes") or [])
                           and (node.get(WRITTEN_FIELD) or {}).get("by") not in (None, session_id))
            node["gist"] = gist
            if notes is not None:
                node["notes"] = notes
            if touches is not None:
                node["touches"] = touches

            # Stamp creation time once — never reset by subsequent updates
            now = time.time()
            if is_new:
                node["_created_ts"] = now
            if changed:
                node[WRITTEN_FIELD] = {"ts": now, "by": session_id}

            # If updating archived node, unarchive it
            if "_archived" in node:
                del node["_archived"]
            if "_orphaned_ts" in node:
                del node["_orphaned_ts"]

            nodes[node_id] = node

            # Update version
            ver_key = version_key_node(node_id)
            self._bump_version(graph_key, ver_key, session_id)

            self.dirty[graph_key] = True

            # Write-through: save immediately
            self._write_through(graph_key)
            if session_id:
                # What a session wrote, it has seen in full.
                self.session_manager.note_viewed(session_id, [node_id], at=now, full=True)

            # Run compaction if needed
            self._maybe_compact(graph_key)

            # Broadcast change
            self._broadcast(
                {"type": "node_updated", "level": level, "node": node, "source_session": session_id},
                level,
                session_id
            )

            near_dup = self._near_duplicate(graph_key, node_id, gist) if is_new else None
            credited = note_credit and self._note_credit(graph_key, node_id, node, level, session_id, now)

            logger.debug(f"Put node '{node_id}' in {level} graph")
            return {"node": node, "level": level, "near_duplicate": near_dup,
                    "note_credited": bool(credited)}

    def _note_credit(self, graph_key: str, node_id: str, node: dict, level: str, session_id: str,
                     now: float) -> bool:
        """Count a case added to another session's lesson as this session's
        endorsement of it: one _useful_ts stamp, the node joins the session's
        likes (one vote per node per session, so a later kg_useful on it is a
        duplicate), logged to useful.jsonl via "note". Caller holds the lock."""
        from core.constants import MAX_LIKES_PER_SESSION, USEFUL_LOG_MAX_BYTES, USEFUL_LOG_NAME
        session = self.session_manager.lookup(session_id)
        if session is None:
            return False
        liked = session.setdefault("liked_ids", [])
        if node_id in liked or len(liked) >= MAX_LIKES_PER_SESSION:
            return False
        node.setdefault("_useful_ts", []).append(now)
        liked.append(node_id)
        self.dirty[graph_key] = True
        self._write_through(graph_key)
        append_jsonl(get_storage_root() / USEFUL_LOG_NAME, {
            "ts": round(now, 3), "kg_session": session_id, "claude_session": session.get("claude_sid"),
            "project": session.get("project_path"), "id": node_id, "level": level, "via": "note",
            "session_likes": len(liked)}, USEFUL_LOG_MAX_BYTES)
        return True

    def _refuse_stale_write(self, level: str, node_id: str, node: dict, session_id: str,
                            notes, touches) -> None:
        """Raise NodeConflictError for a write built on a stale or partial view.

        An agent edits by read-modify-write: it sends back what it saw plus its
        change, and notes and touches replace the stored lists wholesale. Two
        ways that loses someone's work (formal/FINDINGS.md, F11):
          - another session changed the node after this session last saw it;
          - the write replaces stored notes or touches this session has never
            read in their current form (it saw only the gist, in a preload or
            a recall), whoever wrote them.
        A session's own write and a refusal both count as a full read, so the
        retry goes through. Caller holds the lock. View times are recorded
        from before each render, so a write landing mid-render is never
        mistaken for seen.
        """
        written = node.get(WRITTEN_FIELD) or {}
        w_ts = written.get("ts")
        by_other = bool(written) and written.get("by") != session_id
        seen = self.session_manager.viewed_at(session_id, node_id)
        if by_other and w_ts and seen is not None and seen < w_ts:
            raise NodeConflictError(level, node_id, dict(node),
                                    "changed by another session after this session last saw it")
        read = self.session_manager.viewed_at(session_id, node_id, full=True)
        unread = read is None or (by_other and w_ts and read < w_ts)
        for field, sent in (("notes", notes), ("touches", touches)):
            stored = node.get(field) or []
            if sent is not None and stored and sent != stored and unread:
                raise NodeConflictError(level, node_id, dict(node),
                                        f"this write replaces {field} this session has not read")

    def _near_duplicate(self, graph_key: str, node_id: str, gist: str) -> dict | None:
        """Best near-duplicate candidate for a freshly CREATED node, or None.

        Caller holds the lock; the write has already happened — this is a
        nudge for the tool layer to surface, never a block. Probes the same
        term pipeline search uses (subtokens + stems + bigrams,
        field-weighted, IDF) with the new node's id + gist against the rest
        of its own graph.
        """
        nodes = self.graphs[graph_key]["nodes"]
        fields = {
            nid: (
                nid.lower(),
                node.get("gist", "").lower(),
                " ".join(node.get("notes", []) + node.get("touches", [])).lower(),
            )
            for nid, node in nodes.items() if nid != node_id
        }
        if len(fields) < 10:
            return None  # tiny graph — everything resembles everything
        unigrams, bigrams = search_terms(f"{node_id} {gist}")
        n_total = len(fields)
        scores: dict[str, float] = {}
        # The probe's own theoretical maximum: every term at rank 0. Raw
        # scores grow with probe length, so the flag is the RATIO best/self —
        # a node restating an existing one shares most of its rare terms; a
        # node with its own distinctive vocabulary dilutes the ratio however
        # much it brushes against neighbours.
        self_denom = 0.0

        def field_count(f: tuple, stem: str) -> int:
            return (_FIELD_W_ID * f[0].count(stem)
                    + _FIELD_W_GIST * f[1].count(stem)
                    + _FIELD_W_REST * f[2].count(stem))

        def pair_count(f: tuple, pair: tuple) -> int:
            a = field_count(f, pair[0])
            if not a:
                return 0
            b = field_count(f, pair[1])
            return min(a, b) if b else 0

        for terms, counter in ((unigrams, field_count), (bigrams, pair_count)):
            for term in terms:
                matches = [(nid, counter(f, term)) for nid, f in fields.items()]
                matches = [(nid, c) for nid, c in matches if c]
                if not matches:
                    self_denom += 1.0 / 60  # unique to the new node: idf ≈ 1
                    continue
                base = math.log(n_total / len(matches)) / math.log(n_total)
                if base <= 0:
                    continue
                idf_w = base ** IDF_SHARPNESS
                self_denom += idf_w / 60
                matches.sort(key=lambda x: x[1], reverse=True)
                for rank, (nid, _c) in enumerate(matches):
                    scores[nid] = scores.get(nid, 0.0) + idf_w / (60 + rank)

        if scores and self_denom > 0:
            best_id = max(scores, key=scores.get)
            best = scores[best_id]
            if best >= NEAR_DUP_MIN_SCORE and best / self_denom >= NEAR_DUP_RATIO:
                return {
                    "kind": "duplicate",
                    "id": best_id,
                    "gist": nodes[best_id].get("gist", "")[:120],
                    "score": round(best / self_denom, 3),
                }
        # Not a duplicate — but does the gist re-describe an entity the graph
        # already names? Prose mentions are how vocabulary smears (measured:
        # "oxygen" in 48 node texts, its hub holding 4 edges); the durable
        # alternative is an edge to the owning node.
        return self._hub_mention(graph_key, node_id, unigrams, fields, nodes)

    _DATED_ID_RE = re.compile(r"20\d\d-\d\d")

    def _hub_mention(self, graph_key: str, node_id: str, probe_stems: list,
                     fields: dict, nodes: dict) -> dict | None:
        """The most-smeared entity this new node mentions, with its hub.

        A stem qualifies on the DEBT detector's terms (len ≥5, not stoplisted,
        not a token of the project's own slug, held by ≥ max(6, 8%) of nodes'
        id+gist) and is no more common than a quarter of the graph — beyond
        that it is the graph's domain vocabulary, not an entity ('stone' in a
        Go graph named an unrelated node on most writes). Some undated node id
        must carry it as a token — that node is the suggested edge target.
        One suggestion max; caller renders it as a nudge.
        """
        from core.debt import _SMEAR_STOP, smear_ceiling, smear_floor
        floor, ceiling = smear_floor(len(fields)), smear_ceiling(len(fields))
        slug_tokens: set[str] = set()
        if is_project_namespace(graph_key):
            slug_tokens = set(
                graph_key.split(":", 1)[1].rstrip("/").rsplit("/", 1)[-1]
                .lower().replace("_", "-").split("-")) - {""}

        best = None
        for stem in probe_stems:
            if len(stem) < 5 or stem in _SMEAR_STOP or any(
                    stem.startswith(t) or t.startswith(stem) for t in slug_tokens):
                continue
            holders = [nid for nid, f in fields.items() if stem in f[0] or stem in f[1]]
            if not floor <= len(holders) <= ceiling:
                continue
            hubs = [nid for nid in holders
                    if not self._DATED_ID_RE.search(nid)
                    and any(t == stem or t.startswith(stem) for t in nid.split("-"))]
            if not hubs:
                continue
            if best is None or len(holders) > best[1]:
                best = (stem, len(holders), hubs[0])
        if not best:
            return None
        stem, _df, hub = best
        return {
            "kind": "mention",
            "term": stem,
            "id": hub,
            "gist": nodes[hub].get("gist", "")[:120],
        }

    def put_edge(
        self,
        level: str,
        from_ref: str,
        to_ref: str,
        rel: str,
        notes: list[str] | None = None,
        session_id: str | None = None,
        project_path: str | None = None,
    ) -> dict:
        """Create or update an edge.

        project_path resolves a project graph directly (visual editor) — see
        _resolve_graph_key.

        An instance-of edge from a node created after its target credits the
        target once, like an endorsement dated the day the case was written
        (core.recurrence; graph loads reconcile the same rule over all edges).
        """
        validate_edge_ref(from_ref)
        validate_edge_ref(to_ref)
        validate_rel(rel)
        credit = None
        with self.lock:
            level, graph_key = self._resolve_graph_key(level, session_id, project_path)

            edges = self.graphs[graph_key]["edges"]
            edge_key = (from_ref, to_ref, rel)
            if rel == LIFT_EDGE_REL:
                credit = self._recurrence_credit(graph_key, from_ref, to_ref, session_id)

            # Create or update edge
            edge = edges.get(edge_key, {"from": from_ref, "to": to_ref, "rel": rel})
            if notes is not None:
                edge["notes"] = notes

            edges[edge_key] = edge

            # Update version
            ver_key = version_key_edge(from_ref, to_ref, rel)
            self._bump_version(graph_key, ver_key, session_id)

            self.dirty[graph_key] = True

            # Write-through: save immediately
            self._write_through(graph_key)

            # Broadcast change
            self._broadcast(
                {"type": "edge_updated", "level": level, "edge": edge, "source_session": session_id},
                level,
                session_id
            )

            logger.debug(f"Put edge {from_ref}->{to_ref}:{rel} in {level} graph")
            result = {"edge": edge, "level": level}
        if credit:
            append_jsonl(get_storage_root() / USEFUL_LOG_NAME, credit, USEFUL_LOG_MAX_BYTES)
        return result

    def _recurrence_credit(self, graph_key: str, from_ref: str, to_ref: str,
                           session_id: str | None) -> dict | None:
        """Apply core.recurrence to the edge being written. Caller holds lock.

        Returns the useful.jsonl record to append after the lock, or None.
        """
        user_nodes = self.graphs.get("user", {}).get("nodes") if graph_key != "user" else None
        done = recurrence.credit_edge({"from": from_ref, "to": to_ref, "rel": LIFT_EDGE_REL},
                                      self.graphs[graph_key]["nodes"], user_nodes)
        if not done:
            return None
        target_key = "user" if done["cross_level"] else graph_key
        self.dirty[target_key] = True
        if target_key != graph_key:
            self._write_through(target_key)
        session = self.session_manager.lookup(session_id) if session_id else None
        return self._recurrence_record(done, target_key, session_id,
                                       (session or {}).get("project_path"), "write")

    def _reconcile_recurrence(self, graph: dict, user_nodes: dict | None, graph_key: str) -> list:
        """Credit every uncredited recurrence of a graph being loaded and log
        them: the backfill of history and the catch-up for edges that arrived
        outside put_edge. Idempotent (core.recurrence)."""
        credits = recurrence.reconcile(graph["nodes"], graph["edges"], user_nodes)
        if credits:
            project = graph_key.split(":", 1)[1] if ":" in graph_key else None
            for done in credits:
                target_key = "user" if done["cross_level"] else graph_key
                append_jsonl(get_storage_root() / USEFUL_LOG_NAME,
                             self._recurrence_record(done, target_key, None, project, "load"),
                             USEFUL_LOG_MAX_BYTES)
            logger.info(f"Recurrence credits on load of {graph_key}: {len(credits)}")
        return credits

    @staticmethod
    def _recurrence_record(done: dict, target_key: str, session_id, project, at: str) -> dict:
        return {
            "ts": round(time.time(), 3),
            "kg_session": session_id,
            "project": project,
            "id": done["id"],
            "level": "user" if target_key == "user" else "project",
            "via": "recurrence",
            "instance": done["instance"],
            "case_ts": done["case_ts"],
            "at": at,
        }

    def delete_node(self, node_id: str, level: str | None = None, session_id: str | None = None,
                    project_path: str | None = None) -> dict:
        """Delete a node and its connected edges. Level auto-resolved if not provided.

        project_path resolves a project graph directly (used by the visual editor,
        whose session has no registered project path) — mirrors read_node. The editor
        sends both session_id and project_path, so project_path takes precedence.
        """
        with self.lock:
            if level:
                resolved_level, graph_key = self._resolve_graph_key(level, session_id, project_path)
            else:
                result = self.find_node_level(node_id, session_id)
                if not result:
                    raise NodeNotFoundError("both", node_id)
                resolved_level, graph_key = result

            nodes = self.graphs[graph_key]["nodes"]
            edges = self.graphs[graph_key]["edges"]

            if node_id not in nodes:
                raise NodeNotFoundError(resolved_level, node_id)

            # Delete connected edges
            edges_to_delete = [
                key for key, edge in edges.items()
                if edge["from"] == node_id or edge["to"] == node_id
            ]

            for key in edges_to_delete:
                del edges[key]

            # Delete node
            del nodes[node_id]

            self.dirty[graph_key] = True

            # Write-through: save immediately
            self._write_through(graph_key)

            # Broadcast change
            self._broadcast(
                {"type": "node_deleted", "level": resolved_level, "node_id": node_id, "source_session": session_id},
                resolved_level,
                session_id
            )

            logger.info(f"Deleted node '{node_id}' and {len(edges_to_delete)} edges from {resolved_level} graph")
            return {"deleted": node_id, "level": resolved_level, "edges_deleted": len(edges_to_delete)}

    def rename_node(self, old_id: str, new_id: str, level: str | None = None,
                    session_id: str | None = None, project_path: str | None = None) -> dict:
        """Rename a node, carrying every reference with it.

        put_node(new) + delete_node(old) is NOT a rename. It drops _created_ts,
        _useful_ts, _gist_ts, _last_read_ts and the version history, and it strips the
        node of every edge. Worse, cross-level edges live in PROJECT graphs and
        point up to user nodes (see _clean_orphaned_edges) — those graphs are
        not loaded during the write, so nothing rewrites them and the next load
        of each one deletes the dangling edge with a log warning nobody reads.
        Measured 2026-08-28: 28 user nodes were referenced that way from 10
        project graphs. A rename therefore sweeps every graph on DISK, not only
        the loaded ones, and rewrites session seen/preload state so dedup keeps
        working for sessions already in flight.
        """
        validate_new_node_id(new_id)
        if old_id == new_id:
            raise KGError(f"Node id {new_id!r} is unchanged — nothing to rename")

        with self.lock:
            if level:
                resolved_level, graph_key = self._resolve_graph_key(level, session_id, project_path)
            else:
                found = self.find_node_level(old_id, session_id)
                if not found:
                    raise NodeNotFoundError("both", old_id)
                resolved_level, graph_key = found

            nodes = self.graphs[graph_key]["nodes"]
            if old_id not in nodes:
                raise NodeNotFoundError(resolved_level, old_id)
            if new_id in nodes:
                raise KGError(
                    f"Node {new_id!r} already exists in the {resolved_level} graph. "
                    f"Rename to a free id, or merge deliberately with kg_put_node "
                    f"then kg_delete_node."
                )
            self._refuse_capturing_rename(resolved_level, graph_key, old_id, new_id)

            # Move the node — every underscore field rides along, which is the
            # whole point: creation time, endorsement, archival state, scores.
            node = nodes.pop(old_id)
            node["id"] = new_id
            nodes[new_id] = node

            # Edges naming the node. Only a user node is referenced from other
            # graphs — project graphs point up to it; project and maintain ids
            # are visible in their own graph alone. A project carrying its own
            # node by the old name is referring to that node, not to this one.
            rewired = self._rewrite_edge_refs(graph_key, self.graphs[graph_key],
                                              old_id, new_id, session_id)
            touched = {graph_key}
            if resolved_level == "user":
                for gk in self._project_keys():
                    graph = self.graphs[gk]
                    if old_id in graph["nodes"]:
                        continue
                    n = self._rewrite_edge_refs(gk, graph, old_id, new_id, session_id)
                    if n:
                        rewired += n
                        touched.add(gk)

            # Version history follows the name.
            versions = self._versions[graph_key]
            old_ver = versions.pop(version_key_node(old_id), None)
            self._bump_version(graph_key, version_key_node(new_id), session_id)
            if old_ver:
                versions[version_key_node(new_id)]["v"] = old_ver.get("v", 0) + 1

            for gk in touched:
                self.dirty[gk] = True
                self._write_through(gk)

            # Project graphs not loaded right now — the silent-loss path.
            swept, skipped = (self._sweep_disk_rename(old_id, new_id)
                              if resolved_level == "user" else (0, []))
            rewired += swept

            # Sessions already holding the old id in their seen/preload sets.
            self.session_manager.rename_node_ref(old_id, new_id)

            self._broadcast(
                {"type": "node_renamed", "level": resolved_level, "old_id": old_id,
                 "node": node, "source_session": session_id},
                resolved_level,
                session_id
            )

            logger.info(
                f"Renamed '{old_id}' -> '{new_id}' in {resolved_level} graph, "
                f"{rewired} edge ref(s) rewired"
            )
            return {
                "renamed": {"from": old_id, "to": new_id},
                "level": resolved_level,
                "edges_rewired": rewired,
                "skipped_graphs": skipped,
                "node": node,
            }

    def _rewrite_edge_refs(self, graph_key: str, graph: dict, old_id: str, new_id: str,
                           session_id: str | None) -> int:
        """Repoint every edge in one loaded graph. Caller must hold the lock.

        Edges are keyed by (from, to, rel), so a rename is a re-key, not a
        field write — the old key has to go or the graph carries a ghost.
        """
        edges = graph["edges"]
        versions = self._versions.get(graph_key, {})
        hits = [k for k, e in edges.items() if e["from"] == old_id or e["to"] == old_id]
        for key in hits:
            edge = edges.pop(key)
            versions.pop(version_key_edge(*key), None)
            if edge["from"] == old_id:
                edge["from"] = new_id
            if edge["to"] == old_id:
                edge["to"] = new_id
            new_key = (edge["from"], edge["to"], edge["rel"])
            if new_key in edges:
                # Two edges collapsed onto one key — keep the survivor and union
                # the notes rather than dropping a relationship on the floor.
                survivor = edges[new_key]
                merged = list(dict.fromkeys(survivor.get("notes", []) + edge.get("notes", [])))
                if merged:
                    survivor["notes"] = merged
            else:
                edges[new_key] = edge
                self._bump_version(graph_key, version_key_edge(*new_key), session_id)
        return len(hits)

    def _project_keys(self) -> list[str]:
        """Keys of the loaded project graphs. Caller holds lock."""
        return [gk for gk in self.graphs if is_project_namespace(gk)]

    def _unloaded_project_graphs(self) -> list[Path]:
        """graph.json files of projects that are not loaded. Caller holds lock."""
        live_paths = {
            str(self._persistence[gk].path) for gk in self.graphs if gk in self._persistence
        }
        projects_dir = self.config.storage_root / "projects"
        if not projects_dir.is_dir():
            return []
        return [p for p in sorted(projects_dir.glob("*/graph.json")) if str(p) not in live_paths]

    def _refuse_capturing_rename(self, level: str, graph_key: str, old_id: str, new_id: str) -> None:
        """Refuse a rename that would re-point an edge at a different node.

        Edge endpoints are bare ids resolved local node first, then user node,
        and the same id may exist at both levels. A rename is sound only if
        every edge that reached the node still reaches it and no other edge
        starts to. Caller holds lock.
        """
        if level == "project":
            # This project's edges to the user's new_id would resolve to the
            # renamed node instead.
            if new_id in self.graphs["user"]["nodes"] and references(
                    self.graphs[graph_key]["edges"], new_id):
                raise KGError(
                    f"This project has edges to the user node {new_id!r}; renaming "
                    f"{old_id!r} to {new_id!r} would re-point them at the renamed "
                    f"node. Choose another id."
                )
            return
        if level != "user":
            return
        # A project that points at user old_id and owns a local new_id: after
        # the rename its edges would resolve to its own node.
        capturing = [project_slug(gk[len("project:"):]) for gk in self._project_keys()
                     if rename_would_capture(self.graphs[gk], old_id, new_id)]
        for graph_path in self._unloaded_project_graphs():
            try:
                if rename_would_capture_on_disk(graph_path, old_id, new_id):
                    capturing.append(graph_path.parent.name)
            except Exception as e:
                logger.error(f"Rename precheck failed for {graph_path}: {e}")
                capturing.append(graph_path.parent.name)
        if capturing:
            raise KGError(
                f"Renaming user node {old_id!r} to {new_id!r} would re-point edges "
                f"in {len(capturing)} project graph(s) ({', '.join(capturing)}): they "
                f"link to {old_id!r} and have their own node {new_id!r}, which their "
                f"edges would then reach instead. Choose another id."
            )

    def _sweep_disk_rename(self, old_id: str, new_id: str) -> tuple[int, list]:
        """Rewrite refs in project graphs that are not loaded. Caller holds lock."""
        swept, skipped = 0, []
        for graph_path in self._unloaded_project_graphs():
            try:
                n, status = rewrite_edge_refs_on_disk(graph_path, old_id, new_id)
            except Exception as e:
                logger.error(f"Rename sweep failed for {graph_path}: {e}")
                skipped.append({"graph": graph_path.parent.name, "reason": "error"})
                continue
            swept += n
            if status.startswith("skip:"):
                skipped.append({"graph": graph_path.parent.name, "reason": status[5:]})
        return swept, skipped

    def find_edge_level(self, from_ref: str, to_ref: str, rel: str, session_id: str | None = None) -> tuple[str, str] | None:
        """Find which graph contains an edge. Returns (level, graph_key) or None. Caller must hold lock."""
        edge_key = (from_ref, to_ref, rel)
        if edge_key in self.graphs["user"]["edges"]:
            return ("user", "user")
        if session_id:
            try:
                project_root = self.session_manager.get_project_path(session_id)
                if project_root:
                    project_key = project_namespace(project_root)
                    self._ensure_project_loaded(project_root)
                    if project_key in self.graphs and edge_key in self.graphs[project_key]["edges"]:
                        return ("project", project_key)
            except Exception:
                pass
        return None

    def delete_edge(
        self,
        from_ref: str,
        to_ref: str,
        rel: str,
        level: str | None = None,
        session_id: str | None = None,
        project_path: str | None = None,
    ) -> dict:
        """Delete an edge. Level auto-resolved if not provided.

        project_path resolves a project graph directly (visual editor) — see
        _resolve_graph_key.
        """
        with self.lock:
            if level:
                resolved_level, graph_key = self._resolve_graph_key(level, session_id, project_path)
            else:
                result = self.find_edge_level(from_ref, to_ref, rel, session_id)
                if result:
                    resolved_level, graph_key = result
                else:
                    return {"deleted": False, "level": "unknown"}

            edges = self.graphs[graph_key]["edges"]
            edge_key = (from_ref, to_ref, rel)

            if edge_key in edges:
                del edges[edge_key]
                self.dirty[graph_key] = True

                # Write-through: save immediately
                self._write_through(graph_key)

                # Broadcast change
                self._broadcast(
                    {"type": "edge_deleted", "level": resolved_level, "from": from_ref, "to": to_ref, "rel": rel, "source_session": session_id},
                    resolved_level,
                    session_id
                )

                logger.debug(f"Deleted edge {from_ref}->{to_ref}:{rel} from {resolved_level} graph")
                return {"deleted": True, "level": resolved_level}
            else:
                return {"deleted": False, "level": resolved_level}

    def find_node_level(self, node_id: str, session_id: str | None = None) -> tuple[str, str] | None:
        """
        Find which graph contains a node. Returns (level, graph_key) or None.
        Caller must hold lock.
        """
        # Check user graph first
        if node_id in self.graphs["user"]["nodes"]:
            return ("user", "user")

        # Check project graph if session has one
        if session_id:
            try:
                project_root = self.session_manager.get_project_path(session_id)
                if project_root:
                    project_key = project_namespace(project_root)
                    self._ensure_project_loaded(project_root)
                    if project_key in self.graphs and node_id in self.graphs[project_key]["nodes"]:
                        return ("project", project_key)
            except Exception:
                pass

        return None

    def read_node(self, node_id: str, level: str | None = None, session_id: str | None = None,
                  project_path: str | None = None, record: bool = True) -> dict:
        """
        Read a single node's full content (gist + notes + touches).
        Stamps the read and promotes an archived node, unless record is False.

        Args:
            node_id: Node ID to read
            level: Optional level hint. If None, auto-resolves by searching both graphs.
            session_id: Session ID (resolves project graph via the session's registered path)
            project_path: Direct project root path — an alternative to session_id for
                resolving a project-level node. The visual editor uses this because its
                WebSocket session is not registered against any project path, so a
                session-only lookup would fail to find (and thus could not recall) a
                project node. Mirrors read_graphs' project_path handling.
            record: False renders without effects (no read stamp, no promotion);
                a deferred delivery applies them later through record_read.

        Returns dict with "node" and "level" keys.
        """
        with self.lock:
            # Resolve which graph the node is in
            if level:
                resolved_level, graph_key = self._resolve_graph_key(level, session_id, project_path)
            else:
                result = self.find_node_level(node_id, session_id)
                if not result:
                    raise NodeNotFoundError("both", node_id)
                resolved_level, graph_key = result

            nodes = self.graphs[graph_key]["nodes"]
            edges = self.graphs[graph_key]["edges"]

            if node_id not in nodes:
                raise NodeNotFoundError(resolved_level, node_id)

            node = nodes[node_id]
            was_archived = is_archived(node) or "_orphaned_ts" in node
            if record:
                self._record_read(graph_key, resolved_level, node_id, session_id)

            # The node's own edges — crumbs for follow-up reads. Snapshot dicts:
            # the caller renders after the lock is released.
            node_edges = [
                dict(e) for e in edges.values()
                if e["from"] == node_id or e["to"] == node_id
            ]

            return {
                "node": dict(node),
                "level": resolved_level,
                "was_archived": was_archived,
                # A maintenance read never promotes (see _record_read); a
                # deferred read promotes when its delivery is recorded.
                "promoted": was_archived and session_id not in self._maintenance_sessions,
                "edges": node_edges,
            }

    def record_read(self, node_id: str, level: str, session_id: str | None = None) -> None:
        """Apply a delivered read's graph effects. A node renamed or deleted
        since the render is skipped: there is nothing left to stamp."""
        with self.lock:
            resolved_level, graph_key = self._resolve_graph_key(level, session_id, None)
            if node_id in self.graphs[graph_key]["nodes"]:
                self._record_read(graph_key, resolved_level, node_id, session_id)

    def _record_read(self, graph_key: str, resolved_level: str, node_id: str,
                     session_id: str | None) -> None:
        """Read stamp and promotion of one full node read. Caller holds lock.

        A maintenance session's read has no effect: recency and promotion say
        "this was used", and a pass reads dozens of nodes to judge them. Counted,
        the 10-07 pass lifted every node it inspected into the top of the
        ranking and promoted nodes ranked 120th into the active tier.
        """
        if session_id in self._maintenance_sessions:
            return
        nodes = self.graphs[graph_key]["nodes"]
        edges = self.graphs[graph_key]["edges"]
        node = nodes[node_id]
        # Stamp read time on every full node read (feeds recency scoring)
        node["_last_read_ts"] = time.time()
        self.dirty[graph_key] = True

        # If archived or orphaned, promote to active
        was_archived = is_archived(node) or "_orphaned_ts" in node
        if was_archived:
            # Use pop(): an orphaned node may lack _archived (defensive), and a
            # missing flag should be a no-op, not a KeyError that 500s recall.
            node.pop("_archived", None)
            node.pop("_orphaned_ts", None)

            # Update version
            ver_key = version_key_node(node_id)
            self._bump_version(graph_key, ver_key, session_id)

            self.dirty[graph_key] = True

            # Promotion chain: pull adjacent orphaned nodes back to archived
            # so their IDs+edges become visible again as crumbs.
            rescued = []
            for edge in edges.values():
                neighbor_id = None
                if edge["from"] == node_id:
                    neighbor_id = edge["to"]
                elif edge["to"] == node_id:
                    neighbor_id = edge["from"]
                if neighbor_id and neighbor_id in nodes:
                    neighbor = nodes[neighbor_id]
                    if "_orphaned_ts" in neighbor:
                        del neighbor["_orphaned_ts"]
                        rescued.append(neighbor_id)
                        logger.debug(f"Rescued orphaned node '{neighbor_id}' via chain from '{node_id}'")

            self._write_through(graph_key)

            self._broadcast(
                {"type": "node_recalled", "level": resolved_level, "node": node,
                 "rescued_from_orphan": rescued, "source_session": session_id},
                resolved_level,
                session_id
            )

            logger.info(f"Recalled archived node '{node_id}' in {resolved_level} graph"
                        + (f"; rescued {len(rescued)} orphaned neighbor(s)" if rescued else ""))

    def get_sync_diff(self, session_id: str, start_ts: float) -> dict:
        """
        Get changes since a timestamp for a session.
        Returns dict with "user" and "project" diffs.
        """
        with self.lock:
            def get_updates(graph_key: str) -> dict:
                versions = self._versions[graph_key]
                updates = {
                    "nodes": {},
                    "edges": {},
                }

                for key, ver in versions.items():
                    if ver["ts"] > start_ts and ver.get("session") != session_id:
                        if key.startswith("node:"):
                            node_id = key.split(":", 1)[1]
                            if node_id in self.graphs[graph_key]["nodes"]:
                                updates["nodes"][node_id] = self.graphs[graph_key]["nodes"][node_id]
                        elif key.startswith("edge:"):
                            # Parse edge key
                            edge_part = key.split(":", 1)[1]
                            # Find matching edge
                            for edge in self.graphs[graph_key]["edges"].values():
                                edge_id = f"{edge['from']}->{edge['to']}:{edge['rel']}"
                                if edge_part == edge_id:
                                    updates["edges"][edge_id] = edge
                                    break

                return updates

            result = {
                "user": get_updates("user"),
                "project": {"nodes": {}, "edges": {}}
            }

            # Add project updates if session has one
            try:
                project_root = self.session_manager.get_project_path(session_id)
                if project_root:
                    project_key = project_namespace(project_root)
                    if project_key in self.graphs:
                        result["project"] = get_updates(project_key)
            except Exception as e:
                logger.warning(f"Could not get project updates for session {session_id}: {e}")

            return result

    def search(self, query: str, session_id: str | None = None, seen: set | None = None,
               top_k: int = 5, more_k: int = 10) -> dict:
        """Full-text search across node IDs, gists, notes and touches.

        Reciprocal Rank Fusion across per-term ranked lists. The query is
        split on whitespace, then each token also contributes its ./_-
        subtokens ("claude.md-cleanup" → claude, md, cleanup + itself), terms
        match via a light stem (schedule ≈ scheduling), and adjacent subtoken
        pairs form bigram terms whose IDF reflects co-occurrence rarity — the
        pair "claude md" is strong evidence even where each half is common.
        Occurrences are field-weighted (id ×3, gist ×2, notes/touches ×1) so a
        node ABOUT a concept outranks one that mentions it in passing; ranks
        merge via score += idf/(60 + rank). Always searches the user graph;
        the project graph comes from the session's registered path, or —
        without a session — best-effort across all loaded project graphs.

        Returns a structured result built for compact rendering:
          {
            "top":        up to top_k full records (notes included only when
                          the session hasn't already seen the node's gist —
                          gists + edges are the working currency, notes are
                          re-dumped never, on-demand depth via kg_read),
            "more":       up to more_k one-line records (id + gist),
            "connectors": nodes on shortest paths BETWEEN the top hits that
                          aren't hits themselves (id + gist),
            "path_edges": the edges forming those paths,
            "total":      total match count,
          }

        Holds the store lock for the whole scan (the background maintenance
        thread mutates node dicts concurrently).
        """
        seen = seen or set()
        unigrams, bigrams = search_terms(query)

        def search_graph_rrf(graph_key: str) -> tuple[dict[str, float], dict[str, dict]]:
            nodes = self.graphs.get(graph_key, {}).get("nodes", {})
            return rank_nodes(nodes, unigrams, bigrams)

        def build_record(graph_key: str, node_id: str, label: str, score: float,
                         match_meta: dict | None = None) -> dict:
            node = self.graphs.get(graph_key, {}).get("nodes", {}).get(node_id, {})
            node_seen = node_id in seen
            record = {
                "level": label,
                "id": node_id,
                "gist": node.get("gist", ""),
                "archived": node.get("_archived", False),
                "orphaned": "_orphaned_ts" in node,
                "seen": node_seen,
                "score": round(score, 4),
            }
            if match_meta:
                record["matched_terms"] = match_meta["matched_terms"]
                record["max_term_idf"] = round(match_meta["max_term_idf"], 3)
                record["title_match"] = match_meta["title_match"]
            if not node_seen:
                record["notes"] = list(node.get("notes", []))
            return record

        with self.lock:
            # Which graphs participate (user always; project via session or all loaded)
            project_keys: list[str] = []
            if session_id:
                project_path = self.session_manager.get_project_path(session_id)
                if project_path:
                    # Graphs load lazily; after a server restart the session's
                    # project graph may not be in memory yet — without this,
                    # search (and prompt recall riding on it) silently scans
                    # the user graph only.
                    self._ensure_project_loaded(project_path)
                    project_keys = [project_namespace(project_path)]
            else:
                project_keys = [k for k in self.graphs if is_project_namespace(k)]
            graph_keys = ["user"] + [k for k in project_keys if k in self.graphs]

            user_scores, user_meta = search_graph_rrf("user")
            records = [
                build_record("user", node_id, "user", score, user_meta.get(node_id))
                for node_id, score in user_scores.items()
            ]
            proj_scores: dict[str, float] = {}
            proj_meta: dict[str, dict] = {}
            proj_key_map: dict[str, str] = {}
            for graph_key in project_keys:
                g_scores, g_meta = search_graph_rrf(graph_key)
                for node_id, score in g_scores.items():
                    if node_id not in proj_scores or score > proj_scores[node_id]:
                        proj_scores[node_id] = score
                        proj_meta[node_id] = g_meta.get(node_id, {})
                        proj_key_map[node_id] = graph_key
            records.extend(
                build_record(proj_key_map[node_id], node_id, "project", score,
                             proj_meta.get(node_id))
                for node_id, score in proj_scores.items()
            )

            records.sort(key=lambda r: r["score"], reverse=True)
            top = records[:top_k]
            more = [
                {"level": r["level"], "id": r["id"], "gist": r["gist"], "seen": r["seen"]}
                for r in records[top_k:top_k + more_k]
            ]

            # Connections between the top hits: union of pairwise shortest
            # paths (cheap Steiner approximation — exact Steiner is NP-hard
            # and irrelevant at this scale). Adjacency spans all participating
            # graphs, so cross-level edges connect hits across levels.
            top_ids = [r["id"] for r in top]
            path_edges = self._connection_paths(top_ids, graph_keys)

            def find_gist(nid: str):
                for gk in graph_keys:
                    node = self.graphs[gk]["nodes"].get(nid)
                    if node is not None:
                        level = "user" if gk == "user" else "project"
                        return node.get("gist", ""), level
                return None, None

            hit_ids = set(top_ids) | {m["id"] for m in more}
            connectors = []
            for e in path_edges:
                for nid in (e["from"], e["to"]):
                    if nid in hit_ids or any(c["id"] == nid for c in connectors):
                        continue
                    gist, level = find_gist(nid)
                    if gist is not None:
                        connectors.append({"id": nid, "gist": gist, "level": level, "seen": nid in seen})

            return {
                "top": top,
                "more": more,
                "connectors": connectors,
                "path_edges": path_edges,
                "total": len(records),
            }

    def _connection_paths(self, top_ids: list, graph_keys: list) -> list[dict]:
        """Read-only connecting paths; caller must hold the store lock."""
        node_ids = set()
        edges = []
        for graph_key in graph_keys:
            node_ids.update(self.graphs[graph_key]["nodes"])
            edges.extend(self.graphs[graph_key]["edges"].values())
        return connection_paths(top_ids, node_ids, edges)

    # ========================================================================
    # Progress Tracking
    # ========================================================================

    def mark_maintenance(self, session_id: str) -> None:
        """Flag a session as maintenance before its first read (kg_read's
        maintenance argument): chores read their targets in the call that
        opens the session, before any kg_progress could flag it."""
        with self.lock:
            self._maintenance_sessions.add(session_id)

    def _note_maintenance(self, task_id: str, session_id: str | None) -> None:
        """A pass or a chore opens with kg_progress on its own task; from then
        on the session's reads are maintenance, not use (see _record_read)."""
        from core.debt import MAINTAIN_TASK_ID
        if session_id and task_id in (MAINTAIN_TASK_ID, CHORE_TASK_ID):
            self._maintenance_sessions.add(session_id)

    def get_progress(self, task_id: str, level: str = "user", session_id: str | None = None) -> dict:
        """Read persistent progress for a task from _meta.progress."""
        with self.lock:
            self._note_maintenance(task_id, session_id)
            _lvl, graph_key = self._resolve_graph_key(level, session_id, None)
            return self._progress.get(graph_key, {}).get(task_id, {})

    def set_progress(self, task_id: str, state: dict, level: str = "user", session_id: str | None = None) -> dict:
        """Write persistent progress for a task to _meta.progress. Marks graph dirty.

        The previous stamp is no longer destroyed. Each write appends a
        size-bounded copy of `state` to a ring under PROGRESS_TRAIL_KEY inside
        the stored dict, so a later pass can read what earlier passes did AND
        what they declined. Assignment alone meant the 20-minute maintenance
        dispatcher reconsidered from scratch every tick: a merge weighed and
        refused left no trace and got re-litigated on the next pass, and the
        skill's own "found-but-deferred seeds the next pass's cursor" could not
        survive two stamps. Git records what changed; nothing recorded what was
        considered and rejected.

        Top-level keys are written through unchanged, so readers that reach for
        state["last_ts"] (core.debt) are unaffected. A caller passing its own
        _trail key has it ignored — the ring is server-owned.

        `last_ts` is server-owned too: an MCP-only agent has no clock, so a
        caller-supplied value is a guess, and staleness — the leading term of
        the debt score — must not run on guesses. A supplied value is replaced
        by the server clock in both the stamp and its trail copy.
        """
        with self.lock:
            self._note_maintenance(task_id, session_id)
            # Resolve, don't just name: _resolve_graph_key LOADS a project
            # graph that is not in memory yet. Naming it alone wrote the stamp
            # into a dict the next lazy load overwrote from disk, and
            # _write_through skipped it for having no persistence entry — so a
            # stamp made before anything read the graph vanished twice over.
            _lvl, graph_key = self._resolve_graph_key(level, session_id, None)
            if graph_key not in self._progress:
                self._progress[graph_key] = {}

            state = dict(state)
            state["last_ts"] = time.time()

            prior = self._progress[graph_key].get(task_id) or {}
            trail = list(prior.get(PROGRESS_TRAIL_KEY, []))
            trail.append(_trail_entry(state))

            stored = {k: v for k, v in state.items() if k != PROGRESS_TRAIL_KEY}
            stored[PROGRESS_TRAIL_KEY] = trail[-PROGRESS_TRAIL_MAX:]
            self._progress[graph_key][task_id] = stored
            self.dirty[graph_key] = True

            # Write-through: save immediately
            self._write_through(graph_key)

            return {"task_id": task_id, "stored": True}

    def maintenance_debt(self, session_id: str | None = None) -> dict:
        """Per-level maintenance debt for a session's graphs.

        Rendered as DEBT lines by kg_read/bootstrap. Activity = distinct days
        (last 7) with node-read stamps, plus tool_events traffic for the
        project level. Absent graph → key omitted."""
        from core.debt import MAINTAIN_TASK_ID, activity_days, compute_debt

        result: dict = {}
        with self.lock:
            targets = {"user": "user"}
            project_path = None
            try:
                project_path = self.session_manager.get_project_path(session_id) if session_id else None
            except Exception:
                pass
            if project_path:
                try:
                    targets["project"] = self._get_graph_key("project", session_id)
                except Exception:
                    pass

            for label, graph_key in targets.items():
                graph = self.graphs.get(graph_key)
                if not graph:
                    continue
                nodes = list(graph["nodes"].values())
                edges = list(graph["edges"].values())
                last_maintain = (
                    self._progress.get(graph_key, {})
                    .get(MAINTAIN_TASK_ID, {})
                    .get("last_ts")
                )
                ts_pool = [n.get("_last_read_ts") for n in nodes]
                if label == "project" and project_path:
                    try:
                        ev_path = project_graph_path(project_path).parent / "tool_events.json"
                        events = json.loads(ev_path.read_text()).get("events", {})
                        ts_pool.extend(e.get("last_ts") for e in events.values())
                    except Exception:
                        pass
                slug = None
                if label == "project" and project_path:
                    try:
                        slug = project_graph_path(project_path).parent.name
                    except Exception:
                        pass
                result[label] = compute_debt(
                    nodes, edges, last_maintain, activity_days(ts_pool), slug=slug,
                    project_root=project_path if label == "project" else None,
                    home=Path.home())
        return result

    # ========================================================================
    # Maintenance
    # ========================================================================

    def _maybe_compact(self, graph_key: str):
        """Compact graph if over token limit. Caller must hold lock.

        Passes (at most one of compact/refill/rebalance acts per call):
          Pass 1: archive lowest-scored active nodes until active tokens ≤ max_tokens.
          Pass 1r: refill — if active tokens sit under the fill ceiling, promote the
                   highest-scored archived nodes back up to use the headroom.
          Pass 1b: rebalance — between the fill ceiling and the budget, swap the
                   best archived node for the worst active one when it clearly wins.
          Pass 2: orphan lowest-scored archived nodes until archived tokens ≤ 30% of max.
        """
        nodes = self.graphs[graph_key]["nodes"]
        edges = self.graphs[graph_key]["edges"]
        versions = self._versions[graph_key]

        archived = self.compactor.compact_if_needed(nodes, edges, versions, label=graph_key)
        # Never refill on a tick that just archived: compaction lands at the same
        # ceiling refill fills to, so running both would partially undo the archive
        # in the same call. Skipping keeps "one of compact/refill acts per tick".
        refilled = [] if archived else self.compactor.refill_if_room(nodes, edges, versions)
        swapped = [] if archived or refilled else self.compactor.rebalance(nodes, edges, versions)
        orphaned = self.compactor.orphan_archived_if_needed(nodes, edges, versions)

        if archived or refilled or swapped or orphaned:
            self.dirty[graph_key] = True
            self._write_through(graph_key)

    def _clean_orphaned_edges(self, graph: dict):
        """
        Remove edges pointing to non-existent nodes.
        Called when loading graphs to clean up broken references.
        Modifies graph in-place.

        An endpoint that is not a node in THIS graph is still legitimate when it
        is (a) an artifact/file path (contains "/" or "~" — node ids can't), or
        (b) a cross-level reference: a node id that lives in another loaded
        level. Doctrine: cross-level edges belong in the PROJECT graph and point
        up to user-level nodes — the user graph loads at startup, so it is
        always available by the time a project graph is cleaned. Deleting these
        used to silently garbage-collect real knowledge on every restart.
        """
        nodes = graph["nodes"]
        edges = graph["edges"]

        def endpoint_known(ref: str) -> bool:
            if ref in nodes:
                return True
            if "/" in ref or "~" in ref:
                return True  # artifact/file path — always "present"
            # Cross-level reference resolvable in another loaded graph level
            # Cross-level references point BETWEEN user and project graphs.
            # The maintain graph is excluded in both directions: its lessons
            # must not keep a dead user/project edge alive, and a lesson's own
            # edges are resolved against its own nodes above.
            return any(
                ref in other["nodes"]
                for gk, other in self.graphs.items()
                if other is not graph and gk != MAINTAIN_NAMESPACE
            )

        # Find orphaned edges
        orphaned_keys = []
        for edge_key, edge in edges.items():
            if not endpoint_known(edge["from"]) or not endpoint_known(edge["to"]):
                orphaned_keys.append(edge_key)
                logger.warning(
                    f"Removing orphaned edge: {edge['from']} -> {edge['to']} "
                    f"(rel: {edge.get('rel', 'unknown')})"
                )

        # Remove orphaned edges
        for key in orphaned_keys:
            del edges[key]

        if orphaned_keys:
            logger.info(f"Cleaned {len(orphaned_keys)} orphaned edge(s)")

    def _heal_corrupt_nodes(self, graph: dict) -> bool:
        """Repair nodes whose gist swallowed their notes + tool-call markup.

        Some writes (before heal-on-write existed, or from clients that bypass it)
        landed the whole node — gist, notes, and surrounding tool-call tags — in
        the gist string, leaving notes empty. Those oversized gists are charged on
        every full-graph read, so they dominate the token budget.

        Run at load time on every graph: split the real headline back out and
        recover the embedded notes/touches into their proper fields. Idempotent —
        already-clean nodes are skipped — so re-running it on each load is free.
        Returns True if anything changed, so the caller can persist the repair.

        Modifies graph in-place.
        """
        healed = 0
        for node_id, node in graph["nodes"].items():
            gist = node.get("gist", "") or ""
            if not gist_is_malformed(gist):
                continue
            new_gist, new_notes, new_touches = heal_node_fields(
                gist, node.get("notes"), node.get("touches")
            )
            node["gist"] = new_gist
            if new_notes is not None:
                node["notes"] = new_notes
            if new_touches is not None:
                node["touches"] = new_touches
            healed += 1
            logger.warning(
                f"Healed corrupt node '{node_id}': gist {len(gist)}->{len(new_gist)} chars, "
                f"recovered {len(new_notes) if new_notes else 0} note(s)"
            )

        if healed:
            logger.info(f"Healed {healed} corrupt node(s) on load")
        return healed > 0

    def _prune_orphans(self, graph_key: str):
        """Delete orphaned nodes whose grace period has expired. Caller must hold lock.

        Orphaned nodes have _orphaned_ts set (by compactor's orphan_archived_if_needed).
        After orphan_grace_days without recall, they are permanently removed.
        """
        nodes = self.graphs[graph_key]["nodes"]
        edges = self.graphs[graph_key]["edges"]

        current_time = time.time()
        grace_seconds = self.config.orphan_grace_days * 24 * 60 * 60
        to_delete = []

        for node_id, node in nodes.items():
            if "_orphaned_ts" not in node:
                continue
            orphaned_duration = current_time - node["_orphaned_ts"]
            if orphaned_duration > grace_seconds:
                to_delete.append(node_id)

        for node_id in to_delete:
            edges_to_delete = [
                key for key, edge in edges.items()
                if edge["from"] == node_id or edge["to"] == node_id
            ]
            for key in edges_to_delete:
                del edges[key]
            del nodes[node_id]
            self.dirty[graph_key] = True
            self._bump_gen(graph_key)
            logger.info(f"Permanently deleted orphaned node '{node_id}' from {graph_key}")

    def _save_to_disk(self, graph_key: str) -> bool:
        """Save a graph to disk. Caller must hold lock."""
        success = self._persistence[graph_key].save(
            self.graphs[graph_key],
            self._versions[graph_key],
            self._progress.get(graph_key)
        )

        return success

    def _periodic_save(self):
        """Background thread for periodic maintenance (compaction, pruning).
        Write-through handles immediate persistence; this handles background tasks."""
        while self.running:
            # Event-based wait instead of sleep: shutdown sets the event so the
            # thread exits immediately rather than after up to save_interval.
            if self._stop_event.wait(self.config.save_interval):
                break

            # One bad tick must not end the thread: nothing restarts it, and
            # without it there is no compaction, expiry or session persistence.
            try:
                with self.lock:
                    for graph_key in list(self.graphs.keys()):
                        # Run maintenance
                        self._maybe_compact(graph_key)
                        self._prune_orphans(graph_key)

                        # Save if dirty (from maintenance operations)
                        if self.dirty.get(graph_key, False):
                            if self._save_to_disk(graph_key):
                                self.dirty[graph_key] = False

                    # Cleanup expired sessions and persist active ones
                    self.session_manager.cleanup_expired()
                    self.session_manager.save_sessions()
            except Exception:
                logger.exception("Periodic maintenance tick failed; retrying next tick")

    def shutdown(self):
        """Gracefully shutdown the store. Idempotent — both the lifespan hook and
        the post-serve fallback call this; the second call is a no-op."""
        if not self.running:
            return
        logger.info("Shutting down graph store...")
        self.running = False
        self._stop_event.set()
        self.saver_thread.join(timeout=5)

        # Final save
        with self.lock:
            for graph_key in self.graphs.keys():
                if self.dirty.get(graph_key, False):
                    self._save_to_disk(graph_key)
            self.session_manager.save_sessions()

        logger.info("Graph store shutdown complete")
