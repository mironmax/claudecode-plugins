"""Utility functions for knowledge graph operations."""

import re

from .constants import LEVELS
from .exceptions import KGError

# Identifier validation. The graph is rendered into other surfaces (kg_read text,
# the visual editor's DOM, REST URL paths), so identifiers are confined to a safe
# charset at the write boundary — quotes, angle brackets, whitespace and the like
# never enter the store. Node IDs additionally exclude "/" so they stay routable
# as a single REST path segment. Edge endpoints may be file/artifact paths, so
# they allow "/" and "~".
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EDGE_REF_RE = re.compile(r"^[A-Za-z0-9~/][A-Za-z0-9._~/-]{0,255}$")


def validate_node_id(node_id: str):
    """Validate a node ID (kebab-ish, single path segment). Raises KGError."""
    if not isinstance(node_id, str) or not _NODE_ID_RE.match(node_id):
        raise KGError(
            f"Invalid node id {node_id!r}: use letters, digits, '.', '_', '-' "
            f"(max 128 chars, must start alphanumeric)"
        )


def validate_rel(rel: str):
    """Validate an edge relationship type. Raises KGError."""
    if not isinstance(rel, str) or not _REL_RE.match(rel):
        raise KGError(
            f"Invalid rel {rel!r}: use letters, digits, '.', '_', '-' "
            f"(max 64 chars, must start alphanumeric)"
        )


def validate_edge_ref(ref: str):
    """Validate an edge endpoint (node ID or file/artifact path). Raises KGError."""
    if not isinstance(ref, str) or not _EDGE_REF_RE.match(ref):
        raise KGError(
            f"Invalid edge ref {ref!r}: use letters, digits, '.', '_', '-', '/', '~' "
            f"(max 256 chars)"
        )


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
    (format_graph_compact) and charging (CharEstimator), so the two can never
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
