"""Node scoring for compaction decisions."""

import time

from .constants import (
    ARCHIVED_EDGE_WEIGHT,
    USEFUL_HALF_LIFE_DAYS,
    SCORE_WEIGHT_RECENCY,
    SCORE_WEIGHT_CONNECTEDNESS,
    SCORE_WEIGHT_USEFULNESS,
)


class NodeScorer:
    """Scores nodes for compaction decisions."""

    def __init__(self, grace_period_days: int):
        self.grace_period_seconds = grace_period_days * 24 * 60 * 60

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

    def _connectedness(self, node_id: str, active_ids: set, archived_ids: set, adj: dict) -> float:
        """Weighted in/out degree, using the prebuilt adjacency index.

        An edge to an active neighbour counts at full weight (1.0); an edge to an
        archived neighbour counts at ARCHIVED_EDGE_WEIGHT (a "string" you can't pull
        yet, but not worthless). Edges to orphaned neighbours count for nothing.

        Without the archived term, a cluster that archived together scored 0
        connectedness for every member — so refill could never resurface any of
        them. The reduced weight lets a dense archived hub float up the refill order
        and lead its cluster back gradually.
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
        return 0.66 * in_degree + 0.33 * out_degree

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

    def _past_grace(self, node: dict, current_time: float) -> bool:
        """Grace period based on _created_ts only — never reset by updates or reads."""
        created_ts = node.get("_created_ts", 0)
        return (current_time - created_ts) >= self.grace_period_seconds

    def score_all(self, nodes: dict, edges: dict, versions: dict, include_archived: bool = False) -> dict[str, float]:
        """
        Score eligible nodes using percentile-based ranking.

        include_archived=True: score archived nodes alongside active ones (for resurrection pass).
        Returns dict of {node_id: score}. Higher score = more valuable = keep longer.
        Grace period based on _created_ts only — updates and reads do not reset it.
        """
        current_time = time.time()
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
            if not self._past_grace(node, current_time):
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

        scores = {}
        for item in eligible:
            scores[item["id"]] = (
                SCORE_WEIGHT_RECENCY * item["recency_pct"]
                + SCORE_WEIGHT_CONNECTEDNESS * item["connectedness_pct"]
                + SCORE_WEIGHT_USEFULNESS * item["usefulness_pct"]
            )

        return scores
