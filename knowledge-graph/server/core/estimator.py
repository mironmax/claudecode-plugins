"""Token estimation for knowledge graph nodes and graphs.

The estimator charges exactly what kg_read renders, so the compaction budget and
the visible output never disagree:

  - active node  → id + gist          → BASE_NODE_TOKENS + gist/CHARS_PER_TOKEN
  - archived node → id only (anchor)  → ARCHIVED_ID_TOKENS
  - orphaned node → invisible          → 0
  - edge          → only if "live"     → TOKENS_PER_EDGE, else 0

"Live" is defined once in utils.edge_is_live (≥1 active/artifact endpoint). An
edge between two archived nodes is a dangling string — not shown, not charged.
"""

from .constants import BASE_NODE_TOKENS, CHARS_PER_TOKEN, TOKENS_PER_EDGE, ARCHIVED_ID_TOKENS
from .utils import is_active, is_archived, is_orphaned, active_node_ids, edge_is_live


class TokenEstimator:
    """Estimates token costs for nodes and graphs."""

    @staticmethod
    def estimate_node(node: dict) -> int:
        """Estimate token cost for a single active node.

        Only gist counts — notes/touches are detail fetched on-demand via
        kg_read(id), not part of the active context budget.
        """
        gist_tokens = len(node.get("gist", "")) // CHARS_PER_TOKEN
        return BASE_NODE_TOKENS + gist_tokens

    @staticmethod
    def estimate_graph(nodes: dict, edges: dict, include_archived: bool = False) -> int:
        """Estimate the token cost of a graph level as kg_read would render it.

        Active nodes cost id+gist; archived nodes cost a small anchor (one ID
        line); orphaned nodes cost nothing. Only *live* edges — those with at
        least one active/artifact endpoint — are charged.

        include_archived=True charges archived nodes their full id+gist cost
        (used by the resurrection pass, which scores active and archived nodes in
        one pool and therefore needs comparable node costs).
        """
        active_ids = active_node_ids(nodes)

        node_tokens = 0
        for node in nodes.values():
            if is_orphaned(node):
                continue
            if is_active(node):
                node_tokens += TokenEstimator.estimate_node(node)
            elif include_archived:
                node_tokens += TokenEstimator.estimate_node(node)
            else:
                # Archived: a single collapsed ID line ("anchor") in kg_read.
                node_tokens += ARCHIVED_ID_TOKENS

        edge_tokens = sum(
            TOKENS_PER_EDGE
            for edge in edges.values()
            if edge_is_live(edge, nodes, active_ids)
        )
        return node_tokens + edge_tokens
