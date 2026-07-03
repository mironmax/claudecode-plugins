"""Exact-character size accounting for knowledge graph nodes and graphs.

The estimator charges exactly what kg_read renders — the same strings, measured
with len(), plus one newline each. Render and charge share one source of truth
(core.render), so the compaction budget and the visible output can never
disagree:

  - active node   → id + gist line     → len(render_active_line) + 1
  - archived node → id-only anchor     → len(render_archived_line) + 1
  - orphaned node → invisible          → 0
  - edge          → only if "live"     → len(render_edge_line) + 1, else 0

"Live" is defined once in utils.edge_is_live (≥1 active/artifact endpoint). An
edge between two archived nodes is a dangling string — not shown, not charged.

Exactness is the point: any flat per-item approximation drifts from real
rendered sizes (ids and rels vary widely in length), and drift is what lets a
"within budget" graph overflow the client's inline tool-result limit. Measuring
the rendered string itself makes the inline guarantee arithmetic, not luck.
"""

from .render import render_active_line, render_archived_line, render_edge_line
from .utils import is_active, is_orphaned, active_node_ids, edge_is_live


class CharEstimator:
    """Measures exact rendered character costs for nodes, edges, and graphs."""

    @staticmethod
    def estimate_node(node_id: str, node: dict) -> int:
        """Rendered cost of an active node line (id + gist + newline).

        Only gist counts — notes/touches are detail fetched on-demand via
        kg_read(id), not part of the active context budget.
        """
        return len(render_active_line(node_id, node.get("gist", ""))) + 1

    @staticmethod
    def estimate_archived(node_id: str) -> int:
        """Rendered cost of an archived node's id-only anchor line."""
        return len(render_archived_line(node_id)) + 1

    @staticmethod
    def estimate_edge(edge: dict) -> int:
        """Rendered cost of a live edge line."""
        return len(render_edge_line(edge["from"], edge["rel"], edge["to"])) + 1

    @staticmethod
    def estimate_graph(nodes: dict, edges: dict, include_archived: bool = False) -> int:
        """Exact character cost of a graph level as kg_read renders it.

        Active nodes cost their full id+gist line; archived nodes cost their
        anchor line; orphaned nodes cost nothing. Only *live* edges — those with
        at least one active/artifact endpoint — are charged.

        include_archived=True charges archived nodes their full id+gist cost
        (used by the resurrection pass, which scores active and archived nodes in
        one pool and therefore needs comparable node costs).
        """
        active_ids = active_node_ids(nodes)

        node_chars = 0
        for node_id, node in nodes.items():
            if is_orphaned(node):
                continue
            if is_active(node) or include_archived:
                node_chars += CharEstimator.estimate_node(node_id, node)
            else:
                node_chars += CharEstimator.estimate_archived(node_id)

        edge_chars = sum(
            CharEstimator.estimate_edge(edge)
            for edge in edges.values()
            if edge_is_live(edge, nodes, active_ids)
        )
        return node_chars + edge_chars
