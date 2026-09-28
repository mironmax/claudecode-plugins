"""Read-only project listing — one row per stored project graph.

Built for the visual editor's project list (roadmap/tasks/08): the single
source of "what memory projects exist" should be the server's storage, not
a harness's conversation history, so a project used only from Codex, or
with `KG_STORAGE_ROOT` pointed elsewhere, still shows up.

Reads graph files directly off disk, like debt.py's survey_debt — listing
must never create, load into the store, migrate or rewrite a graph.
"""

import json
from pathlib import Path

from .utils import is_active, is_archived, is_orphaned


def _project_row(pdir: Path) -> dict | None:
    """One row for a project directory, or None when it holds no graph.json
    (a counters-only directory — tool_events.json written before any node
    exists) or the file cannot be parsed."""
    gpath = pdir / "graph.json"
    if not gpath.exists():
        return None
    try:
        data = json.loads(gpath.read_text())
    except (OSError, ValueError):
        return None

    nodes = data.get("nodes", {})
    nodes = list(nodes.values()) if isinstance(nodes, dict) else (nodes or [])
    edges = data.get("edges", {})
    edge_count = len(edges) if isinstance(edges, dict) else len(edges or [])
    meta = data.get("_meta", {}) if isinstance(data.get("_meta"), dict) else {}
    project_path = meta.get("project_path")

    active = archived = orphaned = 0
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if is_orphaned(n):
            orphaned += 1
        elif is_archived(n):
            archived += 1
        elif is_active(n):
            active += 1

    try:
        last_used = gpath.stat().st_mtime
    except OSError:
        last_used = None

    return {
        "slug": pdir.name,
        "project_path": project_path,
        "path_exists": bool(project_path) and Path(project_path).expanduser().exists(),
        "active_nodes": active,
        "archived_nodes": archived,
        "orphaned_nodes": orphaned,
        "edge_count": edge_count,
        "last_used": last_used,
    }


def list_projects(storage_root: Path) -> list[dict]:
    """Every stored project graph under storage_root/projects/*/, most
    recently modified first.

    No marker for "evaluation-run" graphs exists to group or exclude: the
    eval harness (server/eval/) only reads git history off the real storage
    root — it never writes a project graph of its own — so there is nothing
    here to collapse today.
    """
    rows = []
    projects_dir = storage_root / "projects"
    if not projects_dir.exists():
        return rows
    for pdir in sorted(projects_dir.iterdir()):
        if not pdir.is_dir():
            continue
        row = _project_row(pdir)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r["last_used"] or 0, reverse=True)
    return rows
