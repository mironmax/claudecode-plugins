"""Node scoring for compaction decisions."""

import time


class NodeScorer:
    """Scores nodes for compaction decisions."""

    def __init__(self, grace_period_days: int):
        self.grace_period_seconds = grace_period_days * 24 * 60 * 60

    def _is_active(self, node: dict) -> bool:
        return not node.get("_archived") and "_orphaned_ts" not in node

    def _connectedness(self, node_id: str, active_ids: set, edges: dict) -> float:
        """Weighted in/out degree counting only edges to/from active nodes."""
        in_degree = 0
        out_degree = 0
        for edge in edges.values():
            if edge["to"] == node_id and edge["from"] in active_ids:
                in_degree += 1
            if edge["from"] == node_id and edge["to"] in active_ids:
                out_degree += 1
        return 0.66 * in_degree + 0.33 * out_degree

    def _recency(self, node_id: str, node: dict, versions: dict, current_time: float) -> float:
        """Most recent of last write or last read. Higher = fresher."""
        version_key = f"node:{node_id}"
        write_ts = versions.get(version_key, {}).get("ts", 0)
        read_ts = node.get("_last_read_ts", 0)
        return max(write_ts, read_ts)

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
                "connectedness_raw": self._connectedness(node_id, active_ids, edges),
            })

        if not eligible:
            return {}

        def assign_percentiles(items: list, raw_key: str, pct_key: str):
            sorted_items = sorted(items, key=lambda x: x[raw_key])
            n = len(sorted_items)
            for i, item in enumerate(sorted_items):
                item[pct_key] = i / (n - 1) if n > 1 else 0.5

        assign_percentiles(eligible, "recency_raw", "recency_pct")
        assign_percentiles(eligible, "connectedness_raw", "connectedness_pct")

        scores = {}
        for item in eligible:
            scores[item["id"]] = (
                0.33 * item["recency_pct"] +
                0.66 * item["connectedness_pct"]
            )

        return scores
