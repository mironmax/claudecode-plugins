"""
Project discovery utilities for visual editor.

The memory server is the single source of "what memory projects exist"
(roadmap/tasks/08): discover_projects() asks its read-only GET /api/projects
for every project graph under its storage root, for every harness, honoring
KG_STORAGE_ROOT. Against a server that lacks the endpoint (older server) or
cannot be reached, it falls back to the previous approach of scanning
~/.claude/projects/ Claude Code history — which misses Codex-only projects,
ignores KG_STORAGE_ROOT and can't tell a project's graph apart from an
evaluation graph, but keeps the editor working.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Centralized storage root — fallback path only (the server-backed path asks
# the server, which resolves its own KG_STORAGE_ROOT).
STORAGE_ROOT = Path(os.getenv("KG_STORAGE_ROOT", str(Path.home() / ".knowledge-graph")))

# Logged once per process, not once per request — the editor asks on every
# page load, and repeating the warning on each one would just be noise.
_missing_route_warned = False


@dataclass
class ProjectMetadata:
    """Project metadata shown in the editor's project list."""
    project_path: Optional[str]
    display_name: str
    last_used: float
    has_graph: bool
    node_count: Optional[int] = None
    edge_count: Optional[int] = None
    # None when the graph's _meta has no project_path (legacy graph) — shown
    # as such, never guessed from the slug.
    path_exists: Optional[bool] = None


def decode_claude_project_path_from_cwd(project_dir: Path) -> Path | None:
    """
    Get actual project path from .cwd field in session files.

    This is more reliable than decoding the directory name since encoding
    is ambiguous for paths containing hyphens.

    Args:
        project_dir: Path to ~/.claude/projects/<encoded-name>/

    Returns:
        Decoded project path or None if no sessions found
    """
    # Find any .jsonl file (not agent-)
    session_files = [f for f in project_dir.glob("*.jsonl")
                     if not f.name.startswith("agent-")]

    if not session_files:
        return None

    # Read first few lines of multiple session files to find .cwd
    for session_file in session_files[:3]:
        try:
            with open(session_file, 'r') as f:
                for i, line in enumerate(f):
                    if i >= 10:
                        break
                    try:
                        data = json.loads(line)
                        cwd = data.get('cwd')
                        if cwd:
                            return Path(cwd)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

    return None


def decode_claude_project_path(encoded: str) -> Path:
    """
    Decode Claude Code's project directory naming (FALLBACK ONLY).

    WARNING: This is ambiguous for paths containing hyphens!
    Use decode_claude_project_path_from_cwd() when possible.
    """
    if encoded.startswith("-"):
        decoded = "/" + encoded[1:].replace("-", "/")
        return Path(decoded)
    else:
        return Path(encoded)


def format_project_name(project_path: Path) -> str:
    """
    Extract short, readable name from project path.

    Strategy: parent/name for depth >= 2, else name only. Max 50 chars.
    """
    parts = project_path.parts

    if len(parts) >= 2:
        display = f"{parts[-2]}/{parts[-1]}"
    else:
        display = parts[-1] if parts else str(project_path)

    if len(display) > 50:
        display = display[:47] + "..."

    return display


def project_slug(project_path: Path) -> str:
    """Derive slug from project path (last component)."""
    return project_path.name


def load_graph_stats(project_path: Path) -> tuple[bool, Optional[int], Optional[int]]:
    """
    Load graph statistics from centralized storage (fallback path only).

    Checks STORAGE_ROOT/projects/<slug>/graph.json first, falls back to
    legacy <project>/.claude/knowledge/graph.json.

    Returns:
        Tuple of (has_graph, node_count, edge_count)
    """
    slug = project_slug(project_path)
    centralized_path = STORAGE_ROOT / "projects" / slug / "graph.json"
    legacy_path = project_path / ".claude" / "knowledge" / "graph.json"

    # Prefer centralized
    graph_path = centralized_path if centralized_path.exists() else legacy_path

    if not graph_path.exists():
        return False, None, None

    try:
        data = json.loads(graph_path.read_text())
        nodes = data.get("nodes", {})
        edges = data.get("edges", {})
        node_count = len(nodes) if isinstance(nodes, dict) else 0
        edge_count = len(edges) if isinstance(edges, dict) else 0
        return True, node_count, edge_count
    except Exception as e:
        logger.error(f"Error reading graph {graph_path}: {e}")
        return True, None, None


async def _fetch_server_projects(server_url: str) -> list[dict] | None:
    """GET /api/projects from the memory server, or None when it can't be
    used — the route is missing (older server) or the server is unreachable.
    Either way the caller falls back to scanning Claude Code history."""
    global _missing_route_warned
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{server_url}/api/projects")
    except httpx.HTTPError as e:
        logger.warning(f"Cannot reach MCP server for project listing: {e}")
        return None
    if response.status_code == 404:
        if not _missing_route_warned:
            logger.warning(
                "MCP server has no GET /api/projects (older server) — falling back "
                "to scanning ~/.claude/projects/ history. Restart the MCP server "
                "after updating the plugin to pick up the new endpoint."
            )
            _missing_route_warned = True
        return None
    if response.status_code != 200:
        logger.warning(f"MCP server /api/projects returned {response.status_code}")
        return None
    try:
        projects = response.json()["projects"]
    except (ValueError, KeyError, TypeError):
        logger.warning("MCP server /api/projects returned an unexpected body")
        return None
    return projects


def _projects_from_server_rows(rows: list[dict]) -> list[dict]:
    """Shape the server's survey rows into the editor's ProjectMetadata
    dicts. Deduplicated by slug (last write wins) — the server itself never
    emits a slug twice, but a defensive dedup keeps a future duplicate from
    doubling a project in the list."""
    by_slug: dict[str, dict] = {}
    for row in rows:
        slug = row.get("slug")
        if not slug:
            continue
        project_path = row.get("project_path")
        display_name = format_project_name(Path(project_path)) if project_path else slug
        node_count = (row.get("active_nodes", 0) + row.get("archived_nodes", 0)
                      + row.get("orphaned_nodes", 0))
        metadata = ProjectMetadata(
            project_path=project_path,
            display_name=display_name,
            last_used=row.get("last_used") or 0,
            has_graph=True,
            node_count=node_count,
            edge_count=row.get("edge_count", 0),
            path_exists=row.get("path_exists"),
        )
        d = asdict(metadata)
        d["slug"] = slug
        by_slug[slug] = d

    projects = list(by_slug.values())
    projects.sort(key=lambda p: p["last_used"], reverse=True)
    return projects


def _discover_projects_from_claude_history() -> list[dict]:
    """Fallback discovery for a server that lacks GET /api/projects: scans
    ~/.claude/projects/ and cross-references STORAGE_ROOT for a graph.

    Misses projects used only from Codex, shows folders with no graph,
    ignores a custom KG_STORAGE_ROOT the server was actually started with,
    and can list an evaluation graph as if it were a project — the
    server-backed path fixes all of these; this one only keeps the editor
    usable against an older server.
    """
    projects_dir = Path.home() / ".claude" / "projects"

    if not projects_dir.exists():
        logger.warning(f"Projects directory not found: {projects_dir}")
        return []

    projects = []

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        # Decode project path from session files (reliable)
        project_path = decode_claude_project_path_from_cwd(project_dir)

        # Fallback: decode from directory name (ambiguous)
        if project_path is None:
            project_path = decode_claude_project_path(project_dir.name)
            logger.warning(f"Using fallback path decoding for {project_dir.name} -> {project_path}")

        # Check if project directory still exists
        if not project_path.exists():
            logger.debug(f"Project directory deleted: {project_path}")
            continue

        jsonl_files = list(project_dir.glob("*.jsonl"))
        last_used = max((f.stat().st_mtime for f in jsonl_files), default=0)

        has_graph, node_count, edge_count = load_graph_stats(project_path)

        metadata = ProjectMetadata(
            project_path=str(project_path),
            display_name=format_project_name(project_path),
            last_used=last_used,
            has_graph=has_graph,
            node_count=node_count,
            edge_count=edge_count,
            path_exists=True,
        )

        projects.append(asdict(metadata))

    projects.sort(key=lambda p: p["last_used"], reverse=True)

    logger.info(f"Discovered {len(projects)} projects (fallback: Claude Code history)")

    return projects


async def discover_projects(server_url: str) -> list[dict]:
    """All memory projects the server at server_url stores, most recently
    used first.

    Server-backed by default (works for every harness, honors
    KG_STORAGE_ROOT); falls back to scanning Claude Code history when the
    server lacks GET /api/projects or cannot be reached.
    """
    rows = await _fetch_server_projects(server_url)
    if rows is not None:
        return _projects_from_server_rows(rows)
    return _discover_projects_from_claude_history()


if __name__ == "__main__":
    # Test discovery
    import asyncio

    logging.basicConfig(level=logging.INFO)

    projects = asyncio.run(discover_projects(
        os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8765")))

    print(f"\nFound {len(projects)} projects:\n")

    for p in projects[:5]:
        print(f"  {p['display_name']}")
        print(f"   Path: {p['project_path']}")

        if p['has_graph']:
            print(f"   Graph: {p['node_count']} nodes, {p['edge_count']} edges")
        else:
            print(f"   Graph: Not created")

        print()
