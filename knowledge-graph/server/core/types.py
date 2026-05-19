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


class Edge(TypedDict):
    """Edge in the knowledge graph."""
    from_ref: str  # 'from' is reserved, but we use it in dict form
    to: str
    rel: str
    notes: NotRequired[list[str]]


class Graph(TypedDict):
    """Complete graph structure."""
    nodes: dict[str, Node]
    edges: dict[tuple[str, str, str], Edge]
