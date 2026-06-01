"""Utility functions for knowledge graph operations."""

from .constants import LEVELS
from .exceptions import KGError


def is_archived(node: dict) -> bool:
    """Check if a node is archived."""
    return node.get("_archived", False)


def is_orphaned(node: dict) -> bool:
    """Check if a node is orphaned (invisible in kg_read, search-only)."""
    return "_orphaned_ts" in node


def is_active(node: dict) -> bool:
    """A node is active when it is neither archived nor orphaned."""
    return not node.get("_archived") and "_orphaned_ts" not in node


def active_node_ids(nodes: dict) -> set:
    """Set of IDs for all currently-active nodes."""
    return {nid for nid, n in nodes.items() if is_active(n)}


def edge_is_live(edge: dict, nodes: dict, active_ids: set) -> bool:
    """Is this edge a "live string" — one you can actually pull?

    An edge is a resurfacing string: holding an active node, you see its edges
    and know where to pull to surface a connected node. A string is only useful
    if you hold at least one end of it. So an edge is *live* when:

      - at least one endpoint is an active node, OR
      - an endpoint is a non-node reference (a file/artifact path), which is
        always "present" — you can open it directly.

    An edge between two archived nodes (or touching an orphaned node) is a
    dangling thread between things you are not holding: it adds visual mass and
    budget cost with zero resurfacing value. Such edges are *not* live — they are
    neither rendered in kg_read nor charged against the active budget.

    This single predicate is the source of truth for BOTH rendering
    (format_graph_compact) and charging (TokenEstimator), so the two can never
    drift apart. When an archived node is later promoted, its edges become live
    again automatically — nothing is lost.
    """
    f, t = edge["from"], edge["to"]
    # An orphaned endpoint makes the edge a dangling pointer — never live.
    if is_orphaned(nodes.get(f, {})) or is_orphaned(nodes.get(t, {})):
        return False
    # A non-node endpoint (artifact / file path) is always present → live.
    f_present = f in active_ids or f not in nodes
    t_present = t in active_ids or t not in nodes
    return f_present or t_present


def version_key_node(node_id: str) -> str:
    """Generate version key for a node."""
    return f"node:{node_id}"


def version_key_edge(from_ref: str, to_ref: str, rel: str) -> str:
    """Generate version key for an edge."""
    return f"edge:{from_ref}->{to_ref}:{rel}"


def edge_storage_key(from_ref: str, to_ref: str, rel: str) -> str:
    """Generate string key for edge storage."""
    return f"{from_ref}->{to_ref}:{rel}"


def validate_level(level: str):
    """Validate level parameter. Raises KGError if invalid."""
    if level not in LEVELS:
        raise KGError(f"Invalid level '{level}', must be one of {LEVELS}")
