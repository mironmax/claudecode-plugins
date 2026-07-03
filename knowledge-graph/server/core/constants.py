"""Constants for knowledge graph operations."""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Size budgets — exact rendered characters, fixed by design (no env overrides).
# The estimator measures the exact strings kg_read renders (core.render), so
# these budgets are invariants, not tuning knobs. The arithmetic that makes the
# inline guarantee hold:
#
#   MAX_CHARS_PER_LEVEL × 2 levels + section headers/health/session lines
#     < READ_CHAR_BUDGET (the render-time degradation ladder's hard ceiling)
#     < the MCP client's tool-result persistence threshold (~50K chars in
#       Claude Code — beyond it the result lands in a file, not in context)
#
# Per-level budget for the compactor: when the rendered level (active gists +
# live-string edges + archived anchors) exceeds this, the lowest-scored active
# nodes are archived. 17,500 chars ≈ the old 5,000-token budget, tightened
# slightly so two full levels plus wrapper text stay under READ_CHAR_BUDGET.
MAX_CHARS_PER_LEVEL = 17500
# Hard ceiling for a single kg_read result. Graphs the compactor maintains never
# reach it; the render-time ladder enforces it for everything else (legacy or
# externally-edited graphs) by dropping lowest-scored archived anchors, then
# lowest-value live edges — never active gists.
READ_CHAR_BUDGET = 40000
# kg_search output ceiling — same inline philosophy as READ_CHAR_BUDGET, sized
# for a focused answer: top hits with notes, connections, a page of one-liners.
SEARCH_CHAR_BUDGET = 10000
COMPACTION_TARGET_RATIO = 0.8
# Refill (reverse compaction): when the active graph sits below the fill ceiling
# (COMPACTION_TARGET_RATIO × max), the highest-scored archived nodes are promoted
# back to active to use the headroom. A single threshold — the ceiling itself —
# governs both trigger and fill level. The old separate low-water trigger (0.6)
# created a dead band: a graph at 0.62-0.79 of budget had real headroom but refill
# never fired, so graphs settled there permanently with most nodes stranded in the
# archive. The no-thrash guarantee never needed the dead band — it comes from the
# ceiling (0.8) sitting below the archive threshold (1.0), plus _maybe_compact
# skipping refill on any tick that just archived.
# Archived nodes budget: max fraction of the per-level char budget that archived
# anchor lines may occupy. When exceeded, lowest-scored archived nodes are
# demoted to orphaned (invisible in kg_read).
ARCHIVED_BUDGET_RATIO = 0.30
# Resurrection: minimum score delta for an archived node to displace a freshly-archived one.
RESURRECTION_MARGIN = 0.05
# Connectedness weight for an edge to an ARCHIVED neighbour, relative to an edge to an
# active neighbour (which is 1.0). A "live string" you can pull (active endpoint) is worth
# full weight; a string between two archived nodes is worth less — but NOT zero. Counting
# archived-neighbour edges at zero created a ratchet: when a well-connected cluster archived
# together, every member's connectedness collapsed to 0 at once, so the refill pass could
# never pull any of them back ("big nodes flying inactive"). At 0.2 a dense archived hub
# scores above an isolated archived node and floats up the refill order; once it is promoted,
# its edges become fully live and the rest of its cluster becomes eligible on the next tick —
# gradual, self-limiting cluster recovery rather than an all-at-once resurrection.
ARCHIVED_EDGE_WEIGHT = 0.2

# Session
SESSION_ID_LENGTH = 8
SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# Grace periods
GRACE_PERIOD_DAYS = 5
ORPHAN_GRACE_DAYS = 365

# Graph levels
LEVELS = ("user", "project")

# Centralized storage
# All graphs stored under ~/.knowledge-graph/ (git-tracked, outside .claude/)
DEFAULT_STORAGE_ROOT = Path.home() / ".knowledge-graph"

# Legacy paths (for migration detection)
LEGACY_USER_PATH = Path.home() / ".claude/knowledge/user.json"
LEGACY_PROJECT_KNOWLEDGE_PATH = ".claude/knowledge/graph.json"
LEGACY_SESSIONS_PATH = Path.home() / ".claude/knowledge/sessions.json"


def get_storage_root() -> Path:
    """Get centralized storage root. Reads KG_STORAGE_ROOT env var, defaults to ~/.knowledge-graph/."""
    return Path(os.getenv("KG_STORAGE_ROOT", str(DEFAULT_STORAGE_ROOT)))


def safe_project_path(project_root: str) -> Path:
    """Resolve and validate a user-supplied project root.

    Constrains the resolved path to the user's home directory to prevent
    path traversal (e.g. '../../etc/passwd') from escaping expected bounds.
    Raises ValueError if the path escapes home.
    """
    home = Path.home().resolve()
    # Resolve via os.path.realpath — avoids symlink games
    resolved_str = os.path.realpath(project_root)
    # Check containment on strings before constructing a Path from user input
    if not (resolved_str + "/").startswith(str(home) + "/"):
        raise ValueError(f"Project path must be within home directory: {resolved_str}")
    return Path(resolved_str)


def _safe_slug(slug: str) -> str:
    """Validate a slug is a plain single directory name with no traversal."""
    if not slug or "/" in slug or "\\" in slug or slug in (".", "..") or slug.startswith("-"):
        raise ValueError(f"Invalid slug: {slug!r}")
    return slug


def project_slug(project_root: str) -> str:
    """Derive a unique slug from project root path.

    Uses last path component.

    Examples:
        /home/maxim/DevProj/comra-wordpress -> comra-wordpress
        /home/maxim/DevProj/heilpraktiker -> heilpraktiker
    """
    # Extract the last component from the string before any Path operations
    # so the slug is derived from validated string manipulation, not a tainted Path
    normalized = os.path.normpath(project_root)
    slug = os.path.basename(normalized)
    return _safe_slug(slug)


def _load_aliases() -> dict:
    """Load slug alias map from ~/.knowledge-graph/aliases.json.

    Maps old_slug -> new_slug for projects that were renamed.
    """
    aliases_path = get_storage_root() / "aliases.json"
    if aliases_path.exists():
        try:
            return json.loads(aliases_path.read_text())
        except Exception:
            pass
    return {}


def _save_aliases(aliases: dict):
    """Save slug alias map atomically."""
    import os
    aliases_path = get_storage_root() / "aliases.json"
    temp_path = aliases_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(aliases, indent=2))
    os.replace(temp_path, aliases_path)


def project_graph_path(project_root: str) -> Path:
    """Get centralized graph path for a project.

    Handles renames: if slug has no graph but an alias or old slug does,
    migrates the old graph to the new slug location.

    Example: ~/.knowledge-graph/projects/comra-wordpress/graph.json
    """
    slug = project_slug(project_root)   # slug is validated — no separators, no traversal
    storage = get_storage_root()
    # Path built entirely from trusted base + validated slug, never from raw user input
    graph_path = storage / "projects" / slug / "graph.json"

    if graph_path.exists():
        return graph_path

    # Check aliases: maybe this project was renamed
    aliases = _load_aliases()

    # Reverse lookup: is there an old slug that points to this one?
    for old_slug, new_slug in aliases.items():
        if new_slug == slug:
            try:
                old_path = storage / "projects" / _safe_slug(old_slug) / "graph.json"
            except ValueError:
                continue
            if old_path.exists():
                _migrate_slug(old_path, graph_path, old_slug, slug)
                return graph_path

    # No alias found — scan existing project dirs for a graph whose
    # _meta.project_path matches (handles first-time rename detection)
    projects_dir = storage / "projects"
    if projects_dir.exists():
        for candidate_dir in projects_dir.iterdir():
            if not candidate_dir.is_dir() or candidate_dir.name == slug:
                continue
            candidate_graph = candidate_dir / "graph.json"
            if candidate_graph.exists():
                try:
                    data = json.loads(candidate_graph.read_text())
                    stored_path = data.get("_meta", {}).get("project_path", "")
                    # Check if the stored path's directory name matches this slug
                    if stored_path and Path(stored_path).name == slug:
                        old_slug = candidate_dir.name
                        logger.info(
                            f"Detected project rename: {old_slug} -> {slug} "
                            f"(stored path: {stored_path})"
                        )
                        _migrate_slug(candidate_graph, graph_path, old_slug, slug)
                        return graph_path
                except Exception:
                    continue

    # Last resort for legacy graphs without _meta.project_path:
    # check if sessions.json has any session whose project_path
    # resolves to a slug that matches an existing project dir
    sessions_path = storage / "sessions.json"
    if sessions_path.exists() and projects_dir.exists():
        try:
            sessions = json.loads(sessions_path.read_text())
            for _sid, sinfo in sessions.items():
                sp = sinfo.get("project_path", "")
                if sp and Path(sp).resolve().name == slug:
                    # This session's project path matches our slug
                    # Check if there's a graph under a different slug
                    old_slug_candidate = Path(sp).name
                    if old_slug_candidate != slug:
                        old_path = storage / "projects" / old_slug_candidate / "graph.json"
                        if old_path.exists():
                            logger.info(
                                f"Detected rename via sessions: {old_slug_candidate} -> {slug}"
                            )
                            _migrate_slug(old_path, graph_path, old_slug_candidate, slug)
                            return graph_path
        except Exception:
            pass

    return graph_path


def _migrate_slug(old_path: Path, new_path: Path, old_slug: str, new_slug: str):
    """Copy graph from old slug to new slug and record alias."""
    import shutil
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(old_path), str(new_path))
    logger.info(f"Migrated graph: {old_slug} -> {new_slug}")

    # Record alias so future lookups are fast
    aliases = _load_aliases()
    aliases[old_slug] = new_slug
    _save_aliases(aliases)
    logger.info(f"Recorded slug alias: {old_slug} -> {new_slug}")


def user_graph_path() -> Path:
    """Get centralized user graph path."""
    return get_storage_root() / "user.json"


def sessions_file_path() -> Path:
    """Get centralized sessions file path."""
    return get_storage_root() / "sessions.json"
