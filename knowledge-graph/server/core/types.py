"""Type definitions for knowledge graph."""

from typing import TypedDict, NotRequired


class Node(TypedDict):
    """Node in the knowledge graph."""
    id: str
    gist: str
    touches: NotRequired[list[str]]
    notes: NotRequired[list[str]]
    _archived: NotRequired[bool]
    _orphaned_ts: NotRequired[float]
    _created_ts: NotRequired[float]
    _last_read_ts: NotRequired[float]


# Functional syntax because the runtime key really is "from" (a Python keyword).
Edge = TypedDict("Edge", {
    "from": str,
    "to": str,
    "rel": str,
    "notes": NotRequired[list[str]],
})


class Graph(TypedDict):
    """Complete graph structure (in-memory shape).

    Edges are keyed by (from, to, rel) tuples in memory; on disk they are
    serialized with string keys ("from->to:rel") — see GraphPersistence.
    """
    nodes: dict[str, Node]
    edges: dict[tuple[str, str, str], Edge]
