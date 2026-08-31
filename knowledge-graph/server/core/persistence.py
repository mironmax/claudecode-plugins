"""Graph persistence with atomic writes."""

import json
import logging
import os
import shutil
from pathlib import Path
from .utils import edge_storage_key, version_key_edge

logger = logging.getLogger(__name__)


class GraphPersistence:
    """Handles graph persistence with atomic writes."""

    def __init__(self, path: Path, project_path: str | None = None):
        self.path = path
        # Stamped into _meta on save; used for rename detection of project graphs.
        self.project_path = project_path

    def load(self) -> tuple[dict, dict, dict]:
        """
        Load graph, versions, and progress from disk.
        Returns (graph_data, versions_dict, progress_dict).
        """
        if not self.path.exists():
            return {"nodes": {}, "edges": {}}, {}, {}

        try:
            with open(self.path) as f:
                data = json.load(f)

            # Extract versions and progress from _meta
            meta = data.get("_meta", {})
            versions = meta.get("versions", {})
            progress = meta.get("progress", {})

            # Load nodes
            nodes = {k: v for k, v in data.get("nodes", {}).items() if k != "_meta"}

            # Load edges (convert string keys to tuple keys internally)
            edges_data = data.get("edges", {})
            edges = {}
            for key, edge in edges_data.items():
                tuple_key = (edge["from"], edge["to"], edge["rel"])
                edges[tuple_key] = edge

            graph = {"nodes": nodes, "edges": edges}

            logger.info(f"Loaded graph from {self.path}: {len(nodes)} nodes, {len(edges)} edges")
            return graph, versions, progress

        except Exception as e:
            logger.error(f"Failed to load graph from {self.path}: {e}")
            raise

    def save(self, graph: dict, versions: dict, progress: dict | None = None) -> bool:
        """
        Save graph to disk with atomic write.
        Returns True on success, False on failure.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)

            # Convert edges from tuple keys to string keys for JSON
            edges_for_disk = {
                edge_storage_key(e["from"], e["to"], e["rel"]): e
                for e in graph["edges"].values()
            }

            meta = {"versions": versions}
            if progress:
                meta["progress"] = progress
            if self.project_path:
                meta["project_path"] = self.project_path
            # Namespace identity of this graph file. "owner" is a placeholder
            # for multi-user/role setups — today every graph belongs to the
            # local user; the field exists so future kinds slot in without a
            # storage migration.
            meta["namespace"] = {
                "kind": "project" if self.project_path else "user",
                "owner": None,
            }

            data = {
                "nodes": graph["nodes"],
                "edges": edges_for_disk,
                "_meta": meta
            }

            # Atomic write: write to temp file, then rename
            temp_path = self.path.with_suffix(".tmp")

            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())  # Ensure written to disk

            # Keep a rolling backup of the previous good state
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_suffix(".prev"))

            # Atomic rename (POSIX guarantees atomicity)
            temp_path.replace(self.path)

            logger.debug(f"Saved graph to {self.path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save graph to {self.path}: {e}")
            # Cleanup failed temp file
            temp_path = self.path.with_suffix(".tmp")
            if temp_path.exists():
                temp_path.unlink()
            return False


def rewrite_edge_refs_on_disk(path: Path, old_id: str, new_id: str) -> tuple[int, str]:
    """Rewrite edge endpoints naming old_id in a graph file that is NOT loaded.

    Cross-level edges live in project graphs and point up to user nodes
    (see MultiProjectGraphStore._clean_orphaned_edges). A rename that only
    fixes loaded graphs leaves those dangling, and the next load of each
    project silently garbage-collects them. This closes that hole by editing
    the file directly, preserving every other _meta field verbatim.

    Returns (edges_rewritten, status):
      "ok"              rewritten (or nothing to rewrite)
      "skip:local-node" the file has its OWN node by that id, so its edges
                        refer to that node, not to the one being renamed
      "skip:collision"  the file already has a node named new_id; rewriting
                        would silently re-point the edge at a different node
    """
    if not path.exists():
        return 0, "ok"
    with open(path) as f:
        data = json.load(f)
    nodes = data.get("nodes", {})
    edges = data.get("edges", {})
    if old_id in nodes:
        return 0, "skip:local-node"

    hits = [k for k, e in edges.items() if e.get("from") == old_id or e.get("to") == old_id]
    if not hits:
        return 0, "ok"
    if new_id in nodes:
        return 0, "skip:collision"

    versions = data.get("_meta", {}).get("versions", {})
    for key in hits:
        edge = edges.pop(key)
        versions.pop(version_key_edge(edge["from"], edge["to"], edge["rel"]), None)
        if edge.get("from") == old_id:
            edge["from"] = new_id
        if edge.get("to") == old_id:
            edge["to"] = new_id
        new_key = edge_storage_key(edge["from"], edge["to"], edge["rel"])
        if new_key in edges:
            # The rename collapsed two edges onto one key — keep the survivor
            # and union the notes rather than dropping a relationship.
            survivor = edges[new_key]
            merged = list(dict.fromkeys(survivor.get("notes", []) + edge.get("notes", [])))
            if merged:
                survivor["notes"] = merged
        else:
            edges[new_key] = edge

    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    shutil.copy2(path, path.with_suffix(".prev"))
    temp_path.replace(path)
    logger.info(f"Rewrote {len(hits)} edge ref(s) {old_id} -> {new_id} in {path}")
    return len(hits), "ok"
