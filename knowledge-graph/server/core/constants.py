"""Constants for knowledge graph operations."""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Token estimation
BASE_NODE_TOKENS = 20
CHARS_PER_TOKEN = 4
TOKENS_PER_EDGE = 15
# An archived node renders as a single ID line in kg_read — cheaper than an
# active node (which renders id + gist). This is the "anchor" cost of keeping a
# node reachable but collapsed. Single source of truth for both the estimator
# and the orphan-pass; previously the orphan pass hardcoded its own copy.
ARCHIVED_ID_TOKENS = 5

# Compaction
# Active-graph token budget. When the rendered active graph (active node gists +
# live-string edges + archived anchors) exceeds this, the compactor archives the
# lowest-scored active nodes. Single source of truth; the server still honours a
# KG_MAX_TOKENS env override but falls back to this value, not a separate literal.
MAX_TOKENS = 5000
COMPACTION_TARGET_RATIO = 0.8
# Refill (reverse compaction): when the active graph sits well below budget — e.g.
# after edges stopped counting archived-archived strings, or after nodes aged out —
# the highest-scored archived nodes are promoted back to active to use the headroom.
# Two ratios form a hysteresis band so refill and archiving never thrash:
#   - refill only TRIGGERS below REFILL_TRIGGER_RATIO (low-water mark)
#   - refill FILLS UP TO COMPACTION_TARGET_RATIO (the same level archiving compacts to)
# Between trigger (0.6) and the archive threshold (1.0) is a stable zone where neither
# pass acts. Refilling to 0.8 (not 1.0) leaves slack so a refill can't trigger archiving.
REFILL_TRIGGER_RATIO = 0.6
# Archived nodes budget: max fraction of max_tokens that archived IDs+edges may occupy.
# When exceeded, lowest-scored archived nodes are demoted to orphaned (invisible in kg_read).
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
