"""Core knowledge graph components."""

from .types import Node, Edge, Graph
from .constants import *
from .exceptions import *
from .estimator import CharEstimator
from .scorer import NodeScorer
from .compactor import Compactor
from .persistence import GraphPersistence
from .healer import heal_node_fields, gist_is_malformed
from .render import render_active_line, render_archived_line, render_edge_line
from .utils import (
    is_archived,
    is_orphaned,
    is_active,
    active_node_ids,
    edge_is_live,
    version_key_node,
    version_key_edge,
    edge_storage_key,
    validate_level,
    validate_node_id,
    validate_rel,
    validate_edge_ref,
)

__all__ = [
    # Types
    "Node",
    "Edge",
    "Graph",
    # Constants
    "MAX_CHARS_PER_LEVEL",
    "READ_CHAR_BUDGET",
    "COMPACTION_TARGET_RATIO",
    "ARCHIVED_BUDGET_RATIO",
    "RESURRECTION_MARGIN",
    "SESSION_ID_LENGTH",
    "SESSION_TTL_SECONDS",
    "GRACE_PERIOD_DAYS",
    "ORPHAN_GRACE_DAYS",
    "LEVELS",
    "DEFAULT_STORAGE_ROOT",
    "LEGACY_USER_PATH",
    "LEGACY_PROJECT_KNOWLEDGE_PATH",
    "LEGACY_SESSIONS_PATH",
    # Path helpers
    "get_storage_root",
    "safe_project_path",
    "project_slug",
    "project_graph_path",
    "user_graph_path",
    "sessions_file_path",
    # Exceptions
    "KGError",
    "NodeNotFoundError",
    "SessionNotFoundError",
    "NodeNotArchivedError",
    # Classes
    "CharEstimator",
    "NodeScorer",
    "Compactor",
    "GraphPersistence",
    # Healer
    "heal_node_fields",
    "gist_is_malformed",
    # Render (single source of truth for kg_read lines; estimator measures these)
    "render_active_line",
    "render_archived_line",
    "render_edge_line",
    # Utils
    "is_archived",
    "is_orphaned",
    "is_active",
    "active_node_ids",
    "edge_is_live",
    "version_key_node",
    "version_key_edge",
    "edge_storage_key",
    "validate_level",
    "validate_node_id",
    "validate_rel",
    "validate_edge_ref",
]
