"""Node scoring for compaction decisions."""

import math
import time

from .constants import (
    ARCHIVED_EDGE_WEIGHT,
    HUB_FLOOR_WEIGHT,
    USEFUL_HALF_LIFE_DAYS,
    SCORE_WEIGHT_RECENCY,
    SCORE_WEIGHT_CONNECTEDNESS,
    SCORE_WEIGHT_USEFULNESS,
)
from .render import render_active_line, render_edge_citation


class NodeScorer:
    """Scores nodes for compaction decisions."""

    def __init__(self, fresh_chars: int):
        self.fresh_chars = fresh_chars

    def _is_active(self, node: dict) -> bool:
        return not node.get("_archived") and "_orphaned_ts" not in node

    @staticmethod
    def _build_adjacency(edges: dict) -> dict:
        """Index edges by endpoint once: {node_id: ([in_neighbours], [out_neighbours])}.

        Scoring is called per-candidate (and refill re-scores each round), so without
        an index _connectedness would re-scan every edge for every candidate —
        O(candidates × edges). Building this once makes each connectedness lookup
        O(degree of that node) instead.
        """
        adj: dict[str, tuple] = {}
        for edge in edges.values():
            f, t = edge["from"], edge["to"]
            adj.setdefault(t, ([], []))[0].append(f)   # f is an in-neighbour of t
            adj.setdefault(f, ([], []))[1].append(t)   # t is an out-neighbour of f
        return adj

    def _connectedness_details(self, node_id: str, active_ids: set, archived_ids: set,
                               adj: dict, include_counts: bool = True) -> dict:
        """Weighted in/out degree, using the prebuilt adjacency index.

        An edge to an active neighbour counts at full weight (1.0); an edge to an
        archived neighbour counts at ARCHIVED_EDGE_WEIGHT (a "string" you can't pull
        yet, but not worthless). Edges to orphaned neighbours count for nothing.

        Without the archived term, a cluster that archived together scored 0
        connectedness for every member — so refill could never resurface any of
        them. The reduced weight lets a dense archived hub float up the refill order
        and lead its cluster back gradually. The hub floor (HUB_FLOOR_WEIGHT) keeps
        a node many others point to from scoring as isolated while they all sleep.
        """
        in_neighbours, out_neighbours = adj.get(node_id, ([], []))

        def weight(nid: str) -> float:
            if nid in active_ids:
                return 1.0
            if nid in archived_ids:
                return ARCHIVED_EDGE_WEIGHT
            return 0.0  # orphaned or missing — not a pullable string

        in_degree = sum(weight(nid) for nid in in_neighbours)
        out_degree = sum(weight(nid) for nid in out_neighbours)
        hub_floor = HUB_FLOOR_WEIGHT * math.log1p(len(in_neighbours) + len(out_neighbours))
        weighted_degree = 0.66 * in_degree + 0.33 * out_degree
        result = {
            "raw": max(weighted_degree, hub_floor),
            "weighted_in": in_degree, "weighted_out": out_degree,
            "weighted_degree": weighted_degree, "hub_floor": hub_floor,
            "archived_neighbor_weight": ARCHIVED_EDGE_WEIGHT,
            "hub_floor_weight": HUB_FLOOR_WEIGHT,
        }
        if not include_counts:
            return result
        result.update({
            "incoming": {
                "active": sum(nid in active_ids for nid in in_neighbours),
                "archived": sum(nid in archived_ids for nid in in_neighbours),
                "unweighted": sum(nid not in active_ids and nid not in archived_ids for nid in in_neighbours),
            },
            "outgoing": {
                "active": sum(nid in active_ids for nid in out_neighbours),
                "archived": sum(nid in archived_ids for nid in out_neighbours),
                "unweighted": sum(nid not in active_ids and nid not in archived_ids for nid in out_neighbours),
            },
        })
        return result

    def _connectedness(self, node_id: str, active_ids: set, archived_ids: set, adj: dict) -> float:
        return self._connectedness_details(node_id, active_ids, archived_ids, adj, include_counts=False)["raw"]

    def _recency(self, node_id: str, node: dict, versions: dict, current_time: float) -> float:
        """Most recent of last write or last read. Higher = fresher."""
        version_key = f"node:{node_id}"
        write_ts = versions.get(version_key, {}).get("ts", 0)
        read_ts = node.get("_last_read_ts", 0)
        return max(write_ts, read_ts)

    @staticmethod
    def _usefulness(node: dict, current_time: float) -> float:
        """Decayed like-count from explicit kg_useful endorsements.

        Each like contributes 0.5 ** (age / half_life) — a node liked recently
        and repeatedly scores high; past usefulness fades unless renewed. Reads
        deliberately don't count: a well-formed gist never needs the full read,
        so read-counting would reward the weakest gists.
        """
        half_life_seconds = USEFUL_HALF_LIFE_DAYS * 24 * 3600
        return sum(
            0.5 ** (max(0.0, current_time - ts) / half_life_seconds)
            for ts in node.get("_useful_ts", [])
        )

    def fresh_ids(self, nodes: dict, edges: dict) -> set:
        """The fresh tier: the newest non-orphaned nodes, by _created_ts, while
        their render cost fits in fresh_chars.

        A node is charged what it costs on screen: its line plus the citation of
        every edge it brings live (other end not orphaned), each edge once. Node
        lines are well under half of a rendered level, so charging lines alone
        let a 30% share hold about 70% of the visible nodes (replay 10-07).

        Creation time only — reads and updates never make a node fresh again.
        A window by budget, not by days, follows the project's own pace: a graph
        touched once a month keeps its last work visible, a sprint week rotates
        through. The walk stops at the first node that does not fit, so the tier
        is always the most recent work, never a gap-filled mix. Nodes without
        _created_ts predate the stamp and count as oldest.
        """
        if self.fresh_chars <= 0:
            return set()
        candidates = sorted(
            (nid for nid, n in nodes.items() if "_orphaned_ts" not in n),
            key=lambda nid: nodes[nid].get("_created_ts", 0), reverse=True,
        )
        incident: dict[str, list] = {}
        for edge in edges.values():
            incident.setdefault(edge["from"], []).append(edge)
            if edge["to"] != edge["from"]:
                incident.setdefault(edge["to"], []).append(edge)
        fresh, used, charged = set(), 0, set()
        for nid in candidates:
            cost = len(render_active_line(nid, nodes[nid].get("gist", ""))) + 1
            new_edges = []
            for edge in incident.get(nid, ()):
                key = (edge["from"], edge["to"], edge["rel"])
                other = edge["to"] if edge["from"] == nid else edge["from"]
                if key in charged or "_orphaned_ts" in nodes.get(other, {}):
                    continue
                new_edges.append(key)
                cost += len(render_edge_citation(edge["rel"], other, edge["from"] == nid)) + 1
            if used + cost > self.fresh_chars:
                break
            fresh.add(nid)
            used += cost
            charged.update(new_edges)
        return fresh

    def score_all(self, nodes: dict, edges: dict, versions: dict, include_archived: bool = False) -> dict[str, float]:
        """The canonical scores used by compaction, refill and read ranking."""
        return {nid: item["score"] for nid, item in self.score_breakdown(
            nodes, edges, versions, include_archived=include_archived,
        ).items()}

    def score_breakdown(self, nodes: dict, edges: dict, versions: dict,
                        include_archived: bool = False, current_time: float | None = None,
                        fresh: set | None = None) -> dict:
        """
        Score eligible nodes using percentile-based ranking.

        include_archived=True: score archived nodes alongside active ones (for resurrection pass).
        Returns the raw factors, percentile ranks and final score per node.
        Fresh-tier nodes are not scored (fresh=None computes the tier).
        """
        current_time = time.time() if current_time is None else current_time
        fresh = self.fresh_ids(nodes, edges) if fresh is None else fresh
        active_ids = {nid for nid, n in nodes.items() if self._is_active(n)}
        # Archived (but not orphaned) neighbours contribute reduced connectedness so a
        # cluster that archived together isn't scored as fully disconnected (see
        # _connectedness). Orphaned nodes are excluded — they are invisible and unpullable.
        archived_ids = {
            nid for nid, n in nodes.items()
            if n.get("_archived") and "_orphaned_ts" not in n
        }

        # Build the edge index once, not per-candidate (see _build_adjacency).
        adj = self._build_adjacency(edges)

        eligible = []
        for node_id, node in nodes.items():
            if "_orphaned_ts" in node:
                continue
            if not include_archived and node.get("_archived"):
                continue
            if node_id in fresh:
                continue

            eligible.append({
                "id": node_id,
                "archived": bool(node.get("_archived")),
                "recency_raw": self._recency(node_id, node, versions, current_time),
                "connectedness_raw": self._connectedness(node_id, active_ids, archived_ids, adj),
                "usefulness_raw": self._usefulness(node, current_time),
            })

        if not eligible:
            return {}

        def assign_percentiles(items: list, raw_key: str, pct_key: str):
            """Tie-aware percentiles: equal raw values share the average rank.

            This matters most for usefulness, where the bulk of nodes sit at
            exactly 0 likes — index-order percentiles would spread identical
            values across the whole 0..1 range arbitrarily. With average ranks,
            an all-zero column collapses to a uniform 0.5 and distorts nothing.
            """
            sorted_items = sorted(items, key=lambda x: x[raw_key])
            n = len(sorted_items)
            if n == 1:
                sorted_items[0][pct_key] = 0.5
                return
            i = 0
            while i < n:
                j = i
                while j + 1 < n and sorted_items[j + 1][raw_key] == sorted_items[i][raw_key]:
                    j += 1
                avg_rank = (i + j) / 2
                for k in range(i, j + 1):
                    sorted_items[k][pct_key] = avg_rank / (n - 1)
                i = j + 1

        assign_percentiles(eligible, "recency_raw", "recency_pct")
        assign_percentiles(eligible, "connectedness_raw", "connectedness_pct")
        assign_percentiles(eligible, "usefulness_raw", "usefulness_pct")

        for item in eligible:
            item["score"] = (
                SCORE_WEIGHT_RECENCY * item["recency_pct"]
                + SCORE_WEIGHT_CONNECTEDNESS * item["connectedness_pct"]
                + SCORE_WEIGHT_USEFULNESS * item["usefulness_pct"]
            )

        return {item["id"]: item for item in eligible}

    def explain(self, node_id: str, nodes: dict, edges: dict, versions: dict) -> dict:
        """Explain this node's current automatic score without changing it.

        Active nodes use the compaction pool; archived nodes use the unified
        refill pool. Orphaned and fresh-tier nodes have no automatic score:
        show a clearly marked preview as an eligible node instead.
        """
        node = nodes[node_id]
        now = time.time()
        orphaned = "_orphaned_ts" in node
        fresh = self.fresh_ids(nodes, edges)
        protected = node_id in fresh
        include_archived = bool(node.get("_archived")) and not orphaned
        eligible = not orphaned and not protected
        scoring_nodes = nodes
        if orphaned:
            preview = dict(node)
            preview.pop("_orphaned_ts", None)
            preview.pop("_archived", None)
            scoring_nodes = {**nodes, node_id: preview}
        breakdown = self.score_breakdown(scoring_nodes, edges, versions,
                                         include_archived=include_archived, current_time=now,
                                         fresh=fresh - {node_id})
        item = breakdown[node_id]
        active_ids = {nid for nid, n in scoring_nodes.items() if self._is_active(n)}
        archived_ids = {nid for nid, n in scoring_nodes.items()
                        if n.get("_archived") and "_orphaned_ts" not in n}
        connections = self._connectedness_details(node_id, active_ids, archived_ids,
                                                   self._build_adjacency(edges))
        write_ts = versions.get(f"node:{node_id}", {}).get("ts", 0)
        components = []
        for key, label, weight in (
            ("recency", "Recency", SCORE_WEIGHT_RECENCY),
            ("connectedness", "Connectedness", SCORE_WEIGHT_CONNECTEDNESS),
            ("usefulness", "Usefulness", SCORE_WEIGHT_USEFULNESS),
        ):
            components.append({
                "key": key, "label": label, "weight": weight,
                "raw": item[f"{key}_raw"], "percentile": item[f"{key}_pct"],
                "contribution": weight * item[f"{key}_pct"],
            })
        reason = ("Preview if recalled to active; orphaned nodes are excluded from automatic ranking."
                  if orphaned else "Preview once newer work pushes it out of the fresh tier; it is protected until then."
                  if protected else "Used for refill and orphaning among eligible active and archived nodes."
                  if include_archived else "Used for archival among eligible active nodes.")
        return {
            "id": node_id, "score": item["score"] if eligible else None,
            "preview_score": item["score"] if not eligible else None,
            "eligible": eligible, "reason": reason,
            "pool": {"size": len(breakdown), "include_archived": include_archived},
            "components": components, "connectedness": connections,
            "recency": {"write_ts": write_ts, "read_ts": node.get("_last_read_ts", 0)},
            "usefulness": {
                "endorsements": len(node.get("_useful_ts", [])),
                "half_life_days": USEFUL_HALF_LIFE_DAYS,
            },
            "fresh": {
                "protected": protected,
                "tier_size": len(fresh),
                "budget_chars": self.fresh_chars,
            },
            "calculated_at": now,
        }
