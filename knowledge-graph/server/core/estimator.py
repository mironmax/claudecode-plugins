"""Exact-character size accounting for knowledge graph nodes and graphs.

The estimator charges exactly what kg_read renders — it builds the same render
plan (core.render) and measures the same lines with len(), plus one newline
each. Render and charge share one source of truth, so the compaction budget and
the visible output can never disagree:

  - active node   → id + gist line          → len(line) + 1
  - archived node → id-only anchor          → len(line) + 1
  - orphaned node → invisible               → 0
  - live edge     → one citation line, cited once at its first-encountered
                    endpoint               → len(line) + 1
  - dead edge (both endpoints archived/orphaned) → not shown, not charged

Exactness is the point: any flat per-item approximation drifts from real
rendered sizes (ids and rels vary widely in length), and drift is what lets a
"within budget" graph overflow the client's inline tool-result limit. Measuring
the rendered lines themselves makes the inline guarantee arithmetic, not luck.

estimate_node / estimate_archived / estimate_edge remain as cheap per-item
approximations for incremental deltas (refill's fit checks); every consumer of
those deltas is guarded by an authoritative estimate_graph re-measure.
"""

from .render import (
    plan_level,
    level_body_lines,
    render_active_line,
    render_archived_line,
    render_edge_citation,
)


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
        """Approximate rendered cost of a live edge's citation line.

        Exact when the edge is cited at its from-side; a to-side citation
        differs only by len(from_id) - len(to_id). Used for incremental fit
        deltas only — the authoritative cost always comes from estimate_graph.
        """
        return len(render_edge_citation(edge["rel"], edge["to"], True)) + 1

    @staticmethod
    def estimate_graph(nodes: dict, edges: dict, include_archived: bool = False) -> int:
        """Exact character cost of a graph level as kg_read renders its body.

        Builds the real render plan (cluster order, first-encounter citations,
        anchors) and measures the resulting lines. Section headers inside the
        body (ACTIVE:/ARCHIVED:) are part of the render, so they are charged
        too — render == charge, character for character.

        include_archived=True plans as if archived nodes were active (full
        id+gist lines, their edges live) — used by scoring passes that need
        comparable node costs across active and archived pools.
        """
        plan = plan_level(nodes, edges, include_archived=include_archived)
        return sum(len(line) + 1 for line in level_body_lines(plan))
