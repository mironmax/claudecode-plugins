"""Graph persistence with atomic writes."""

import json
import logging
import os
import shutil
from pathlib import Path
from .utils import edge_storage_key

logger = logging.getLogger(__name__)


class GraphPersistence:
    """Handles graph persistence with atomic writes."""

    def __init__(self, path: Path):
        self.path = path

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
            if hasattr(self, '_project_path') and self._project_path:
                meta["project_path"] = self._project_path

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
