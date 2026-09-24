"""Ranking variants: what recall would inject for one prompt.

A variant is a function

    variant(terms: list[str], graphs: Graphs, seen: frozenset[str]) -> list[str]

returning the node ids it would put in front of the model, best first; an
empty list is silence. The harness counts an injection when at least one
returned id is unseen, and treats only the unseen ones as surfaced — the same
novelty rule production applies. Register a new idea with @variant("name"),
or pass "module:function" on the command line.

The baseline is not a model of production, it IS production: the store's own
search method and ambient.decide_recall, run over private copies of the
graphs. A change to either shows up in the baseline the next time it runs.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module

from core.constants import (
    PROMPT_RECALL_MAX_HITS,
    PROMPT_RECALL_SCORE_MULTI,
    PROMPT_RECALL_SCORE_SINGLE,
    project_namespace,
)
from mcp_http.ambient import RECALL_SEARCH_TOP_K, decide_recall
from mcp_http.store import MultiProjectGraphStore


@dataclass
class Graphs:
    """The graphs one prompt ran against: the user graph and, when the
    session had one, its project graph. Parsed from files by the harness and
    shared between variants: read-only by convention."""
    user: dict
    project: dict | None = None
    project_path: str | None = None
    states: dict = field(default_factory=dict)   # "user"/"project" -> data.KNOWN…

    def store_view(self) -> "_SearchView":
        graphs = {"user": self.user}
        if self.project is not None:
            graphs[project_namespace(self.project_path or "?")] = self.project
        return _SearchView(graphs)


class _SearchView:
    """Just enough of MultiProjectGraphStore to run its own search method.

    Borrowing the methods (not subclassing) keeps __init__ — which loads the
    user graph from the storage root and starts the saver thread — out of
    reach. With no session id, search() scans every project graph present,
    which here is exactly the session's one.
    """
    search = MultiProjectGraphStore.search
    _connection_paths = MultiProjectGraphStore._connection_paths

    def __init__(self, graphs: dict):
        self.graphs = graphs
        self.lock = threading.RLock()
        self.session_manager = None


def production_search(terms: list[str], graphs: Graphs, seen, top_k: int = RECALL_SEARCH_TOP_K) -> dict:
    """The store's search, exactly as prompt recall calls it."""
    return graphs.store_view().search(" ".join(terms), session_id=None,
                                      seen=set(seen), top_k=top_k)


def baseline_decision(terms: list[str], graphs: Graphs, seen) -> dict:
    """Production recall's full decision for one prompt (see decide_recall)."""
    return decide_recall(production_search(terms, graphs, seen), terms)


def production_threshold(terms: list[str]) -> float:
    return PROMPT_RECALL_SCORE_MULTI if len(terms) >= 2 else PROMPT_RECALL_SCORE_SINGLE


Variant = Callable[[list[str], Graphs, frozenset], list[str]]
REGISTRY: dict[str, Variant] = {}


def variant(name: str):
    def register(fn: Variant) -> Variant:
        REGISTRY[name] = fn
        return fn
    return register


def resolve(name: str) -> Variant:
    """A registered name, or "module:function" for a variant kept elsewhere."""
    if name in REGISTRY:
        return REGISTRY[name]
    if ":" in name:
        module, _, attr = name.partition(":")
        return getattr(import_module(module), attr)
    raise KeyError(f"unknown variant {name!r}; known: {', '.join(sorted(REGISTRY))}")


# --------------------------------------------------------------------------
# Variants
# --------------------------------------------------------------------------

@variant("baseline")
def baseline(terms, graphs, seen):
    """Production: what recall shows — hits, then connectors."""
    d = baseline_decision(terms, graphs, seen)
    if d["reason"] != "injected":
        return []
    return [r["id"] for r in d["hits"]] + [c["id"] for c in d["connectors"]]


@variant("half-threshold")
def half_threshold(terms, graphs, seen):
    """Score bar halved; evidence = a second term or a title match. No
    connectors."""
    bar = production_threshold(terms) / 2
    top = production_search(terms, graphs, seen)["top"]
    hits = [r for r in top if r["score"] >= bar and r.get("gist")
            and (r.get("matched_terms", 0) >= 2 or r.get("title_match"))]
    return [r["id"] for r in hits[:PROMPT_RECALL_MAX_HITS]]


@variant("score-only")
def score_only(terms, graphs, seen):
    """Threshold only: no evidence gate, no title-first ordering."""
    bar = production_threshold(terms)
    top = production_search(terms, graphs, seen)["top"]
    return [r["id"] for r in top if r["score"] >= bar][:PROMPT_RECALL_MAX_HITS]
