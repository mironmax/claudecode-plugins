"""Multi-project knowledge graph store for HTTP MCP server."""

import logging
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
    GRACE_PERIOD_DAYS,
    ORPHAN_GRACE_DAYS,
    MAX_CHARS_PER_LEVEL,
    heal_node_fields,
    gist_is_malformed,
    is_archived,
    version_key_node,
    version_key_edge,
    NodeNotFoundError,
    validate_level,
    validate_node_id,
    validate_rel,
    validate_edge_ref,
    get_storage_root,
    project_graph_path,
    user_graph_path,
    safe_project_path,
)
from .session_manager import HTTPSessionManager

logger = logging.getLogger(__name__)


@dataclass
class GraphConfig:
    """Configuration for knowledge graph."""
    max_chars: int = MAX_CHARS_PER_LEVEL
    orphan_grace_days: int = ORPHAN_GRACE_DAYS
    grace_period_days: int = GRACE_PERIOD_DAYS
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
        self.scorer = NodeScorer(config.grace_period_days)
        self.compactor = Compactor(self.scorer, self.estimator, config.max_chars)

        # Graph storage: key = "user" or "project:<project_root>"
        self.graphs: dict[str, Graph] = {}
        self._versions: dict[str, dict] = {}
        self._progress: dict[str, dict] = {}
        self._persistence: dict[str, GraphPersistence] = {}

        # Thread safety
        self.lock = threading.RLock()
        self.dirty: dict[str, bool] = {}

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

            self.graphs[user_key] = graph
            self._versions[user_key] = versions
            self._progress[user_key] = progress
            self._persistence[user_key] = persistence
            self.dirty[user_key] = False

            # Persist the heal so the repair sticks and the next load is a no-op
            if healed:
                self._write_through(user_key)

            logger.info(f"Loaded user graph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")

    def _ensure_project_loaded(self, project_root: str, force_reload: bool = False):
        """
        Load a project graph if not already loaded. Caller must hold lock.
        project_root: Absolute path to project root directory.
        force_reload: If True, reload from disk even if already cached.
        """
        project_key = f"project:{project_root}"

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

        self.graphs[project_key] = graph
        self._versions[project_key] = versions
        self._progress[project_key] = progress
        self._persistence[project_key] = persistence
        self.dirty[project_key] = False

        # Persist the heal so the repair sticks and the next load is a no-op
        if healed:
            self._write_through(project_key)

        logger.info(f"Loaded project graph for {project_root}: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges (path: {graph_path})")

    def _get_graph_key(self, level: str, session_id: str | None) -> str:
        """Get the graph storage key for a level and session."""
        validate_level(level)

        if level == "user":
            return "user"
        else:  # level == "project"
            if not session_id:
                raise ValueError("session_id required for project-level operations")

            project_root = self.session_manager.get_project_path(session_id)
            if not project_root:
                raise ValueError(f"Session {session_id} has no project_path registered")

            return f"project:{project_root}"

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
            return "project", f"project:{project_root}"

        graph_key = self._get_graph_key(level, session_id)
        if graph_key.startswith("project:"):
            project_root = graph_key.split(":", 1)[1]
            self._ensure_project_loaded(project_root)
        return level, graph_key

    def _bump_version(self, graph_key: str, key: str, session_id: str | None = None) -> dict:
        """Increment version for a key and return new version. Caller must hold lock."""
        ts = time.time()
        current = self._versions[graph_key].get(key, {"v": 0})
        new_ver = {"v": current["v"] + 1, "ts": ts, "session": session_id}
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

    def _write_through(self, graph_key: str):
        """Immediately save a graph to disk after mutation. Caller must hold lock."""
        if graph_key in self._persistence:
            self._save_to_disk(graph_key)
            self.dirty[graph_key] = False

    # ========================================================================
    # Public API
    # ========================================================================

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

        Returns dict with "user" and "project" keys.
        """
        with self.lock:
            # Reload user graph from disk if requested
            if force_reload:
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
                    self._ensure_project_loaded(project_root, force_reload=force_reload)
                    project_key = f"project:{project_root}"

                    result["project"] = snapshot(self.graphs[project_key])
                except Exception as e:
                    logger.warning(f"Could not load project graph for {project_root}: {e}")

            return result

    def scores_for_read(self, session_id: str | None = None) -> dict:
        """Node scores per level for kg_read's degradation ladder.

        Returns {"user": {node_id: score}, "project": {node_id: score}} scored
        with archived nodes included (one comparable pool). Nodes inside the
        grace period are absent — the ladder treats missing as "keep" for active
        nodes and can't encounter it for archived ones (archiving only happens
        past grace).
        """
        with self.lock:
            result = {"user": {}, "project": {}}
            keys = [("user", "user")]
            if session_id:
                project_root = self.session_manager.get_project_path(session_id)
                if project_root:
                    keys.append(("project", f"project:{project_root}"))
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

    def put_node(
        self,
        level: str,
        node_id: str,
        gist: str,
        notes: list[str] | None = None,
        touches: list[str] | None = None,
        session_id: str | None = None,
        project_path: str | None = None,
    ) -> dict:
        """Create or update a node.

        project_path resolves a project graph directly (visual editor) — see
        _resolve_graph_key.
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

            # Create or update node
            is_new = node_id not in nodes
            node = nodes.get(node_id, {"id": node_id})
            node["gist"] = gist
            if notes is not None:
                node["notes"] = notes
            if touches is not None:
                node["touches"] = touches

            # Stamp creation time once — never reset by subsequent updates
            if is_new:
                node["_created_ts"] = time.time()

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

            # Advance sync timestamp so this write is not returned by kg_sync for this session
            if session_id:
                self.session_manager.mark_synced(session_id)

            # Run compaction if needed
            self._maybe_compact(graph_key)

            # Broadcast change
            self._broadcast(
                {"type": "node_updated", "level": level, "node": node, "source_session": session_id},
                level,
                session_id
            )

            logger.debug(f"Put node '{node_id}' in {level} graph")
            return {"node": node, "level": level}

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
        """
        validate_edge_ref(from_ref)
        validate_edge_ref(to_ref)
        validate_rel(rel)
        with self.lock:
            level, graph_key = self._resolve_graph_key(level, session_id, project_path)

            edges = self.graphs[graph_key]["edges"]
            edge_key = (from_ref, to_ref, rel)

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

            # Advance sync timestamp so this write is not returned by kg_sync for this session
            if session_id:
                self.session_manager.mark_synced(session_id)

            # Broadcast change
            self._broadcast(
                {"type": "edge_updated", "level": level, "edge": edge, "source_session": session_id},
                level,
                session_id
            )

            logger.debug(f"Put edge {from_ref}->{to_ref}:{rel} in {level} graph")
            return {"edge": edge, "level": level}

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

    def find_edge_level(self, from_ref: str, to_ref: str, rel: str, session_id: str | None = None) -> tuple[str, str] | None:
        """Find which graph contains an edge. Returns (level, graph_key) or None. Caller must hold lock."""
        edge_key = (from_ref, to_ref, rel)
        if edge_key in self.graphs["user"]["edges"]:
            return ("user", "user")
        if session_id:
            try:
                project_root = self.session_manager.get_project_path(session_id)
                if project_root:
                    project_key = f"project:{project_root}"
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
                    project_key = f"project:{project_root}"
                    self._ensure_project_loaded(project_root)
                    if project_key in self.graphs and node_id in self.graphs[project_key]["nodes"]:
                        return ("project", project_key)
            except Exception:
                pass

        return None

    def read_node(self, node_id: str, level: str | None = None, session_id: str | None = None,
                  project_path: str | None = None) -> dict:
        """
        Read a single node's full content (gist + notes + touches).
        If the node is archived, promotes it to active as a side effect.

        Args:
            node_id: Node ID to read
            level: Optional level hint. If None, auto-resolves by searching both graphs.
            session_id: Session ID (resolves project graph via the session's registered path)
            project_path: Direct project root path — an alternative to session_id for
                resolving a project-level node. The visual editor uses this because its
                WebSocket session is not registered against any project path, so a
                session-only lookup would fail to find (and thus could not recall) a
                project node. Mirrors read_graphs' project_path handling.

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
                "edges": node_edges,
            }

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
                    project_key = f"project:{project_root}"
                    if project_key in self.graphs:
                        result["project"] = get_updates(project_key)
            except Exception as e:
                logger.warning(f"Could not get project updates for session {session_id}: {e}")

            return result

    def search(self, query: str, session_id: str | None = None, seen: set | None = None,
               top_k: int = 5, more_k: int = 10) -> dict:
        """Full-text search across node IDs, gists, notes and touches.

        Reciprocal Rank Fusion across per-term ranked lists: the query is split
        on whitespace; for each term, nodes are ranked by occurrence count in
        their searchable text; ranks merge via score += 1/(60 + rank). Always
        searches the user graph; the project graph comes from the session's
        registered path, or — without a session — best-effort across all loaded
        project graphs.

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
        RRF_K = 60
        seen = seen or set()
        terms = list(dict.fromkeys(t for t in query.lower().split() if t))

        def search_graph_rrf(graph_key: str) -> dict[str, float]:
            if graph_key not in self.graphs:
                return {}
            nodes = self.graphs[graph_key]["nodes"]

            searchable = {
                node_id: " ".join([
                    node_id,
                    node.get("gist", ""),
                    " ".join(node.get("notes", [])),
                    " ".join(node.get("touches", [])),
                ]).lower()
                for node_id, node in nodes.items()
            }

            rrf_scores: dict[str, float] = {}
            for term in terms:
                term_scores = [
                    (node_id, text.count(term))
                    for node_id, text in searchable.items()
                    if term in text
                ]
                term_scores.sort(key=lambda x: x[1], reverse=True)
                for rank, (node_id, _) in enumerate(term_scores):
                    rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + 1.0 / (RRF_K + rank)
            return rrf_scores

        def build_record(graph_key: str, node_id: str, label: str, score: float) -> dict:
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
            if not node_seen:
                record["notes"] = list(node.get("notes", []))
            return record

        with self.lock:
            # Which graphs participate (user always; project via session or all loaded)
            project_keys: list[str] = []
            if session_id:
                project_path = self.session_manager.get_project_path(session_id)
                if project_path:
                    project_keys = [f"project:{project_path}"]
            else:
                project_keys = [k for k in self.graphs if k.startswith("project:")]
            graph_keys = ["user"] + [k for k in project_keys if k in self.graphs]

            records = [
                build_record("user", node_id, "user", score)
                for node_id, score in search_graph_rrf("user").items()
            ]
            proj_scores: dict[str, float] = {}
            proj_key_map: dict[str, str] = {}
            for graph_key in project_keys:
                for node_id, score in search_graph_rrf(graph_key).items():
                    if node_id not in proj_scores or score > proj_scores[node_id]:
                        proj_scores[node_id] = score
                        proj_key_map[node_id] = graph_key
            records.extend(
                build_record(proj_key_map[node_id], node_id, "project", score)
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
        """Edges forming pairwise shortest paths (≤4 hops) between top hits.

        Caller must hold the lock. Adjacency is undirected over node-node
        edges in the participating graphs; artifact endpoints don't route.
        """
        MAX_HOPS = 4
        node_ids = set()
        for gk in graph_keys:
            node_ids.update(self.graphs[gk]["nodes"].keys())

        adj: dict[str, list] = {}
        for gk in graph_keys:
            for e in self.graphs[gk]["edges"].values():
                f, t = e["from"], e["to"]
                if f in node_ids and t in node_ids and f != t:
                    adj.setdefault(f, []).append((t, e))
                    adj.setdefault(t, []).append((f, e))

        path_edges: dict[int, dict] = {}
        for i, src in enumerate(top_ids):
            for dst in top_ids[i + 1:]:
                if src not in adj or dst not in adj:
                    continue
                # BFS with parent tracking
                parents = {src: None}
                frontier = [src]
                depth = 0
                found = False
                while frontier and depth < MAX_HOPS and not found:
                    next_frontier = []
                    for nid in frontier:
                        for other, edge in adj.get(nid, []):
                            if other in parents:
                                continue
                            parents[other] = (nid, edge)
                            if other == dst:
                                found = True
                                break
                            next_frontier.append(other)
                        if found:
                            break
                    frontier = next_frontier
                    depth += 1
                if found:
                    cur = dst
                    while parents[cur] is not None:
                        prev, edge = parents[cur]
                        path_edges[id(edge)] = edge
                        cur = prev
        return list(path_edges.values())

    # ========================================================================
    # Progress Tracking
    # ========================================================================

    def get_progress(self, task_id: str, level: str = "user", session_id: str | None = None) -> dict:
        """Read persistent progress for a task from _meta.progress."""
        with self.lock:
            graph_key = self._get_graph_key(level, session_id) if level == "project" else "user"
            return self._progress.get(graph_key, {}).get(task_id, {})

    def set_progress(self, task_id: str, state: dict, level: str = "user", session_id: str | None = None) -> dict:
        """Write persistent progress for a task to _meta.progress. Marks graph dirty."""
        with self.lock:
            graph_key = self._get_graph_key(level, session_id) if level == "project" else "user"
            if graph_key not in self._progress:
                self._progress[graph_key] = {}
            self._progress[graph_key][task_id] = state
            self.dirty[graph_key] = True

            # Write-through: save immediately
            self._write_through(graph_key)

            return {"task_id": task_id, "stored": True}

    # ========================================================================
    # Maintenance
    # ========================================================================

    def _maybe_compact(self, graph_key: str):
        """Compact graph if over token limit. Caller must hold lock.

        Passes (at most one of compact/refill acts per call — refill is skipped
        on any tick that archived):
          Pass 1: archive lowest-scored active nodes until active tokens ≤ max_tokens.
          Pass 1r: refill — if active tokens sit under the fill ceiling, promote the
                   highest-scored archived nodes back up to use the headroom.
          Pass 2: orphan lowest-connectivity archived nodes until archived tokens ≤ 30% of max.
        """
        nodes = self.graphs[graph_key]["nodes"]
        edges = self.graphs[graph_key]["edges"]
        versions = self._versions[graph_key]

        archived = self.compactor.compact_if_needed(nodes, edges, versions)
        # Never refill on a tick that just archived: compaction lands at the same
        # ceiling refill fills to, so running both would partially undo the archive
        # in the same call. Skipping keeps "one of compact/refill acts per tick".
        refilled = [] if archived else self.compactor.refill_if_room(nodes, edges, versions)
        orphaned = self.compactor.orphan_archived_if_needed(nodes, edges)

        if archived or refilled or orphaned:
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
            return any(
                ref in other["nodes"] for other in self.graphs.values() if other is not graph
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
