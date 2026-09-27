"""Type definitions for knowledge graph."""

import sys
from typing import TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:  # 3.10: typing_extensions is a dependency of pydantic
    from typing_extensions import NotRequired


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
    _useful_ts: NotRequired[list[float]]
    _gist_ts: NotRequired[list[float]]   # gist rewrites, bounded (churn guard)


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
