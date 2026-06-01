"""Graph compaction (archiving low-value nodes)."""

import logging
import time
from .constants import (
    COMPACTION_TARGET_RATIO,
    REFILL_TRIGGER_RATIO,
    ARCHIVED_BUDGET_RATIO,
    RESURRECTION_MARGIN,
    ARCHIVED_ID_TOKENS,
)
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
        Archive nodes if graph exceeds token limit, then run resurrection pass.

        Pass 1: score active nodes, archive lowest-scored until under target.
        Pass 2: score all (active + archived) together; if any archived node
                outscores a just-archived node by RESURRECTION_MARGIN, swap them.

        Returns list of net-newly-archived node IDs (after swaps).
        """
        estimated_tokens = self.estimator.estimate_graph(nodes, edges, include_archived=False)

        if estimated_tokens <= self.max_tokens:
            return []

        logger.info(f"Compacting graph: {estimated_tokens} tokens > {self.max_tokens} limit")

        # Pass 1: archive lowest-scored active nodes
        active_scores = self.scorer.score_all(nodes, edges, versions, include_archived=False)

        if not active_scores:
            logger.debug("No nodes eligible for archiving (all within grace period)")
            return []

        sorted_active = sorted(active_scores.items(), key=lambda x: x[1])
        target = int(self.max_tokens * COMPACTION_TARGET_RATIO)
        archived_this_pass = []

        for node_id, score in sorted_active:
            if estimated_tokens <= target:
                break
            node = nodes.get(node_id)
            if node and not node.get("_archived"):
                token_cost = self.estimator.estimate_node(node)
                node["_archived"] = True
                estimated_tokens -= token_cost
                archived_this_pass.append(node_id)
                logger.debug(f"Archived node '{node_id}' (score: {score:.2f}, tokens: {token_cost})")

        # Pass 2: resurrection — re-score everything (active + archived) in unified pool.
        # If an archived node beats a freshly-archived node by RESURRECTION_MARGIN, swap.
        if archived_this_pass:
            unified_scores = self.scorer.score_all(nodes, edges, versions, include_archived=True)
            resurrected = []

            for just_archived_id in list(archived_this_pass):
                archived_score = unified_scores.get(just_archived_id, 0.0)
                # Find the best-scoring archived node (excluding just_archived_id itself)
                best_archived_id = None
                best_archived_score = -1.0
                for nid, sc in unified_scores.items():
                    if nid == just_archived_id:
                        continue
                    n = nodes.get(nid)
                    if n and n.get("_archived") and "_orphaned_ts" not in n:
                        if sc > best_archived_score:
                            best_archived_score = sc
                            best_archived_id = nid

                if best_archived_id and (best_archived_score - archived_score) >= RESURRECTION_MARGIN:
                    # Swap: resurrect the better-scored archived node, keep just_archived_id archived
                    nodes[best_archived_id]["_archived"] = False
                    resurrected.append(best_archived_id)
                    logger.debug(
                        f"Resurrected '{best_archived_id}' (score: {best_archived_score:.2f}) "
                        f"over '{just_archived_id}' (score: {archived_score:.2f})"
                    )

            if resurrected:
                logger.info(f"Resurrection pass: promoted {len(resurrected)} archived node(s) back to active")

        logger.info(f"Compaction complete: {len(archived_this_pass)} net archived, now ~{estimated_tokens} tokens")
        return archived_this_pass

    def refill_if_room(self, nodes: dict, edges: dict, versions: dict) -> list[str]:
        """Promote the highest-scored archived nodes back to active to use spare budget.

        The reverse of compaction. Compaction only ever moves nodes DOWN (active →
        archived) when the graph is over budget; nothing moved them back up except a
        manual kg_read(id). So a graph could sit far below budget with valuable
        archived nodes collapsed and headroom going unused — especially after the
        edge-accounting change freed the budget that archived-archived strings used
        to occupy.

        Hysteresis prevents thrashing against compact_if_needed:
          - refill only TRIGGERS when active tokens < REFILL_TRIGGER_RATIO × max (0.6)
          - refill FILLS UP TO COMPACTION_TARGET_RATIO × max (0.8)
        Between 0.8 and the archive threshold (1.0×max) is a dead band, so a refill
        can never push the graph into an immediate archive, and an archive can never
        drop it straight into an immediate refill.

        Iterative re-scoring. Promotion is not a fixed-order sweep: promoting a node
        makes ITS edges to other archived nodes become full-weight "live" strings, which
        raises those neighbours' connectedness. So after each promotion the remaining
        candidates are re-scored against the new active set and the next-best is chosen
        afresh. This lets a dense archived hub lead its own cluster back within a single
        pass — pull the hub, its satellites re-rank to the top, pull them next — instead
        of stranding the cluster because every member looked disconnected at the start.

        Returns the list of newly-promoted (resurfaced) node IDs, in promotion order.
        """
        estimated_tokens = self.estimator.estimate_graph(nodes, edges, include_archived=False)

        trigger = int(self.max_tokens * REFILL_TRIGGER_RATIO)
        if estimated_tokens >= trigger:
            return []

        fill_ceiling = int(self.max_tokens * COMPACTION_TARGET_RATIO)
        promoted = []

        # Iterate: each round re-scores the remaining archived candidates against the
        # current (growing) active set, then promotes the single best that still fits.
        # The candidate pool strictly shrinks each round, so this terminates; the extra
        # safety bound on the loop count is just a guard against pathological input.
        for _ in range(len(nodes) + 1):
            candidates = [
                nid for nid, n in nodes.items()
                if n.get("_archived") and "_orphaned_ts" not in n
            ]
            if not candidates:
                break

            # Re-score every round: a previous promotion may have made some candidate's
            # edges live, changing its rank. Scoring includes archived nodes so active
            # and archived candidates share a comparable scale.
            scores = self.scorer.score_all(nodes, edges, versions, include_archived=True)
            best_id = max(candidates, key=lambda nid: scores.get(nid, 0.0))

            # Promote tentatively and re-measure the whole graph: promotion turns an
            # archived anchor (ARCHIVED_ID_TOKENS) into a full active node (id + gist)
            # AND makes more edges live, so the delta isn't guessable up front.
            node = nodes[best_id]
            node["_archived"] = False
            new_estimate = self.estimator.estimate_graph(nodes, edges, include_archived=False)
            if new_estimate > fill_ceiling:
                # The single most-valuable remaining candidate doesn't fit — revert it
                # and stop. Deliberate choice of break over skip-to-next: a lower-scored
                # candidate may be smaller and would fit, but promoting it ahead of a more
                # valuable node inverts the priority the scoring exists to set. Refill is
                # best-effort and runs every tick, so a node that just missed this pass is
                # reconsidered next time (and can always be pulled by an explicit recall).
                node["_archived"] = True
                break

            estimated_tokens = new_estimate
            promoted.append(best_id)
            logger.debug(f"Refilled node '{best_id}' (score: {scores.get(best_id, 0.0):.2f})")

        if promoted:
            logger.info(f"Refill: promoted {len(promoted)} archived node(s) to use spare budget, now ~{estimated_tokens} tokens")
        return promoted

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
        # Each archived node costs ~1 ID line in kg_read output (ARCHIVED_ID_TOKENS,
        # shared with the estimator so the two never disagree).
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
