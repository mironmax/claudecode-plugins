"""Utility functions for knowledge graph operations."""

import re

from .constants import LEVELS, NODE_ID_MAX_WORDS, NODE_ID_TARGET_WORDS
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


# A calendar date is one word, not three: "2026-08-25" is a single stamp, and
# splitting it would make every dated audit node look bloated.
_ID_DATE_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")


def node_id_words(node_id: str) -> int:
    """Word count of a node id, counting an ISO date as one word."""
    if not isinstance(node_id, str):
        return 0
    return len([p for p in _ID_DATE_RE.sub("d", node_id).split("-") if p])


def validate_node_id(node_id: str):
    """Validate a node ID (kebab-ish, single path segment). Raises KGError."""
    if not isinstance(node_id, str) or not _NODE_ID_RE.match(node_id):
        raise KGError(
            f"Invalid node id {node_id!r}: use letters, digits, '.', '_', '-' "
            f"(max 128 chars, must start alphanumeric)"
        )


def node_id_has_date(node_id: str) -> bool:
    """True if the id embeds a calendar date — a reference, not a meaning."""
    return bool(_ID_DATE_RE.search(node_id))


def validate_new_node_id(node_id: str):
    """Charset validation plus the length rule. Raises KGError.

    Deliberately NOT part of validate_node_id: the length rule applies to ids
    being CHOSEN (a create, a rename target), never to ids being addressed. An
    update, a promotion or a rewire of a node named before the rule existed
    must not fail on its name — that would make the graph's own history
    unwritable.
    """
    validate_node_id(node_id)
    words = node_id_words(node_id)
    if words > NODE_ID_MAX_WORDS:
        raise KGError(
            f"Node id {node_id!r} is {words} words. An id NAMES THE SUBJECT in "
            f"{NODE_ID_TARGET_WORDS - 2}-{NODE_ID_TARGET_WORDS} words; the "
            f"claim about it belongs in the gist. Rename the subject "
            f"(a date counts as one word) and write again."
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


# Gists past this length read as walls in the full-graph render and break the
# scan rhythm (the format's working currency is one-line headlines). The write
# is never rejected — long gists are sometimes right — but the writer gets
# nudged at the moment the fix is cheapest.
GIST_SCAN_LIMIT = 300


def gist_length_warning(gist: str) -> str:
    """A nudge string when a gist exceeds scan-friendly length, else ''."""
    if len(gist) <= GIST_SCAN_LIMIT:
        return ""
    return (
        f" — note: gist is {len(gist)} chars; gists scan best ≤{GIST_SCAN_LIMIT}. "
        "Consider keeping the headline and moving detail to notes."
    )


def node_id_warning(node_id: str) -> str:
    """A nudge when a freshly chosen id is workable but poorly shaped, else ''.

    Two shapes, neither fatal enough to refuse:

    * Width. Above NODE_ID_MAX_WORDS the write is refused outright
      (validate_new_node_id); this covers the last tolerated width, where the
      id still works but has started carrying the claim rather than naming the
      subject.

    * A date in the id. A date is a reference, not a meaning: it says when
      something was written down, never what it is, so it ages into pure
      noise while occupying the one field that has to stay recognisable years
      later. It belongs in the gist. Kept as a nudge because dated ids are a
      long-standing habit here and the series number ("week5") usually
      already carries what the date was standing in for.
    """
    parts = []
    words = node_id_words(node_id)
    if words > NODE_ID_TARGET_WORDS:
        parts.append(
            f"id is {words} words; ids name the SUBJECT in "
            f"{NODE_ID_TARGET_WORDS - 2}-{NODE_ID_TARGET_WORDS}, and the claim "
            "about it belongs in the gist"
        )
    if node_id_has_date(node_id):
        parts.append(
            "id carries a date; a date is a reference, not a meaning. If this "
            "is one more entry in a series, the graph wants ONE enduring node "
            "for the subject, updated in place, touching the current document"
        )
    if not parts:
        return ""
    return (
        " — note: " + "; ".join(parts) +
        ". kg_rename_node carries edges and history if you tighten it."
    )
