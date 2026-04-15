"""Graph compaction (archiving low-value nodes)."""

import logging
import time
from .constants import COMPACTION_TARGET_RATIO, ARCHIVED_BUDGET_RATIO
from .estimator import TokenEstimator
from .scorer import NodeScorer

logger = logging.getLogger(__name__)


class Compactor:
    """Handles graph compaction: active→archived and archived→orphaned."""

    def __init__(self, scorer: NodeScorer, estimator: TokenEstimator, max_tokens: int):
        self.scorer = scorer
        self.estimator = estimator
        self.max_tokens = max_tokens

    def compact_if_needed(self, nodes: dict, edges: dict, versions: dict) -> list[str]:
        """
        Archive nodes if graph exceeds token limit.
        Returns list of archived node IDs.
        """
        estimated_tokens = self.estimator.estimate_graph(nodes, edges, include_archived=False)

        if estimated_tokens <= self.max_tokens:
            return []

        logger.info(f"Compacting graph: {estimated_tokens} tokens > {self.max_tokens} limit")

        # Score eligible nodes
        scores = self.scorer.score_all(nodes, edges, versions)

        if not scores:
            logger.debug("No nodes eligible for archiving (all within grace period)")
            return []

        # Sort by score (ascending - lowest scores archived first)
        sorted_nodes = sorted(scores.items(), key=lambda x: x[1])

        # Archive until we're under target
        target = int(self.max_tokens * COMPACTION_TARGET_RATIO)
        archived = []

        for node_id, score in sorted_nodes:
            if estimated_tokens <= target:
                break

            node = nodes.get(node_id)
            if node and not node.get("_archived"):
                # Calculate token cost
                token_cost = self.estimator.estimate_node(node)

                # Archive the node
                node["_archived"] = True

                # Update estimate
                estimated_tokens -= token_cost
                archived.append(node_id)

                logger.debug(f"Archived node '{node_id}' (score: {score:.2f}, tokens: {token_cost})")

        logger.info(f"Compaction complete: archived {len(archived)} nodes, now ~{estimated_tokens} tokens")
        return archived

    def orphan_archived_if_needed(self, nodes: dict, edges: dict) -> list[str]:
        """
        Demote archived nodes to orphaned when archived section exceeds budget.

        Archived nodes show as ID-only lines in kg_read output. If too many accumulate,
        they crowd out active context. This pass keeps archived tokens ≤ 30% of max_tokens.

        A node is "orphaned" by setting _orphaned_ts = now. Orphaned nodes:
          - Are invisible in kg_read/kg_sync output
          - Still searchable via kg_search (flagged as orphaned)
          - Can be rescued by reading a connected archived node (chain promotion)
          - Are permanently deleted after orphan_grace_days without recall

        Returns list of newly orphaned node IDs.
        """
        # Each archived node costs ~1 ID line in kg_read output (~5 tokens)
        ARCHIVED_ID_TOKENS = 5
        archived_nodes = {
            nid: n for nid, n in nodes.items()
            if n.get("_archived") and "_orphaned_ts" not in n
        }

        if not archived_nodes:
            return []

        archived_tokens = len(archived_nodes) * ARCHIVED_ID_TOKENS
        budget = int(self.max_tokens * ARCHIVED_BUDGET_RATIO)

        if archived_tokens <= budget:
            return []

        logger.info(f"Archived section too large: {archived_tokens} tokens > {budget} budget")

        # Score archived nodes — lowest scored get orphaned first.
        # Use edge connectivity as proxy for value: count edges to/from active nodes.
        active_ids = {nid for nid, n in nodes.items() if not n.get("_archived")}
        connectivity = {}
        for edge in edges.values():
            f, t = edge["from"], edge["to"]
            if f in archived_nodes and t in active_ids:
                connectivity[f] = connectivity.get(f, 0) + 1
            if t in archived_nodes and f in active_ids:
                connectivity[t] = connectivity.get(t, 0) + 1

        # Sort by connectivity ascending (least connected orphaned first)
        sorted_archived = sorted(archived_nodes.keys(), key=lambda nid: connectivity.get(nid, 0))

        orphaned = []
        current_time = time.time()

        for node_id in sorted_archived:
            if archived_tokens <= budget:
                break
            node = nodes[node_id]
            node["_orphaned_ts"] = current_time
            archived_tokens -= ARCHIVED_ID_TOKENS
            orphaned.append(node_id)
            logger.debug(f"Orphaned archived node '{node_id}' (connectivity: {connectivity.get(node_id, 0)})")

        logger.info(f"Orphaned {len(orphaned)} archived nodes, archived section now ~{archived_tokens} tokens")
        return orphaned
