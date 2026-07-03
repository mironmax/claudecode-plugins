"""Graph compaction (archiving low-value nodes)."""

import logging
import time
from .constants import (
    COMPACTION_TARGET_RATIO,
    ARCHIVED_BUDGET_RATIO,
    RESURRECTION_MARGIN,
)
from .estimator import CharEstimator
from .scorer import NodeScorer

logger = logging.getLogger(__name__)


class Compactor:
    """Handles graph compaction: active→archived and archived→orphaned.

    All budgets are exact rendered characters (see core.render / CharEstimator).
    """

    def __init__(self, scorer: NodeScorer, estimator: CharEstimator, max_chars: int):
        self.scorer = scorer
        self.estimator = estimator
        self.max_chars = max_chars

    def compact_if_needed(self, nodes: dict, edges: dict, versions: dict) -> list[str]:
        """
        Archive nodes if graph exceeds its char budget, then run resurrection pass.

        Pass 1: score active nodes, archive lowest-scored until under target.
        Pass 2: score all (active + archived) together; if any archived node
                outscores a just-archived node by RESURRECTION_MARGIN, swap them.

        Returns list of net-newly-archived node IDs (after swaps).
        """
        estimated_chars = self.estimator.estimate_graph(nodes, edges, include_archived=False)

        if estimated_chars <= self.max_chars:
            return []

        logger.info(f"Compacting graph: {estimated_chars} chars > {self.max_chars} limit")

        # Pass 1: archive lowest-scored active nodes
        active_scores = self.scorer.score_all(nodes, edges, versions, include_archived=False)

        if not active_scores:
            logger.debug("No nodes eligible for archiving (all within grace period)")
            return []

        sorted_active = sorted(active_scores.items(), key=lambda x: x[1])
        target = int(self.max_chars * COMPACTION_TARGET_RATIO)
        archived_this_pass = []

        for node_id, score in sorted_active:
            if estimated_chars <= target:
                break
            node = nodes.get(node_id)
            if node and not node.get("_archived"):
                node["_archived"] = True
                # Re-measure rather than decrement: archiving swaps the node's gist
                # cost for an anchor AND can kill live edges, so the true delta isn't
                # the node cost alone. (Refill re-measures the same way.)
                estimated_chars = self.estimator.estimate_graph(nodes, edges, include_archived=False)
                archived_this_pass.append(node_id)
                logger.debug(f"Archived node '{node_id}' (score: {score:.2f}, now {estimated_chars} chars)")

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
                    # Swap: resurrect the better-scored archived node, keep just_archived_id
                    # archived — but only if the graph stays within budget. The swap isn't
                    # size-neutral (different gist sizes, different edges going live), so
                    # re-measure and revert a swap that would push the graph back over max.
                    nodes[best_archived_id]["_archived"] = False
                    new_estimate = self.estimator.estimate_graph(nodes, edges, include_archived=False)
                    if new_estimate > self.max_chars:
                        nodes[best_archived_id]["_archived"] = True
                        continue
                    estimated_chars = new_estimate
                    resurrected.append(best_archived_id)
                    logger.debug(
                        f"Resurrected '{best_archived_id}' (score: {best_archived_score:.2f}) "
                        f"over '{just_archived_id}' (score: {archived_score:.2f})"
                    )

            if resurrected:
                logger.info(f"Resurrection pass: promoted {len(resurrected)} archived node(s) back to active")

        logger.info(f"Compaction complete: {len(archived_this_pass)} net archived, now {estimated_chars} chars")
        return archived_this_pass

    def refill_if_room(self, nodes: dict, edges: dict, versions: dict) -> list[str]:
        """Promote the highest-scored archived nodes back to active to use spare budget.

        The reverse of compaction. Compaction only ever moves nodes DOWN (active →
        archived) when the graph is over budget; nothing moved them back up except a
        manual kg_read(id). So a graph could sit far below budget with valuable
        archived nodes collapsed and headroom going unused — especially after the
        edge-accounting change freed the budget that archived-archived strings used
        to occupy.

        Single threshold: refill acts whenever the graph is below the fill ceiling
        (COMPACTION_TARGET_RATIO × max, 0.8) and fills up to it. There is no separate
        lower trigger — an earlier 0.6 low-water mark created a dead band (0.6–0.8 of
        budget) where graphs settled permanently with headroom unused and most nodes
        stranded archived. No-thrash is guaranteed by the ceiling sitting below the
        archive threshold (1.0×max) and by the store skipping refill on any tick that
        just archived.

        Iterative re-scoring. Promotion is not a fixed-order sweep: promoting a node
        makes ITS edges to other archived nodes become full-weight "live" strings, which
        raises those neighbours' connectedness. So after each promotion the remaining
        candidates are re-scored against the new active set and the next-best is chosen
        afresh. This lets a dense archived hub lead its own cluster back within a single
        pass — pull the hub, its satellites re-rank to the top, pull them next — instead
        of stranding the cluster because every member looked disconnected at the start.

        Skip, don't stop, on a non-fitting candidate. A top-scored node too large for
        the remaining headroom is set aside for this pass and the next-best is tried.
        The earlier break-on-first-non-fit meant one oversized gist permanently blocked
        every smaller candidate behind it (the estimate only grows during a pass, so
        "reconsidered next time" never fit either). Skipping within the pass cannot
        invert long-term priority: refill runs every tick, so a high-scored node that
        fits later is still taken first then.

        Returns the list of newly-promoted (resurfaced) node IDs, in promotion order.
        """
        estimated_chars = self.estimator.estimate_graph(nodes, edges, include_archived=False)

        fill_ceiling = int(self.max_chars * COMPACTION_TARGET_RATIO)
        if estimated_chars >= fill_ceiling:
            return []

        promoted = []
        too_big: set[str] = set()  # didn't fit this pass; estimate only grows, so final

        # Outer loop: one iteration per successful promotion (bounded by node count).
        # Re-scoring happens only here — a promotion changes the active set, which
        # changes candidate ranks. Skipping a non-fitting candidate changes nothing,
        # so the inner walk continues down the SAME ranking without re-scoring
        # (re-scoring per skip made a saturated dense graph take ~40s).
        for _ in range(len(nodes) + 1):
            candidates = [
                nid for nid, n in nodes.items()
                if n.get("_archived") and "_orphaned_ts" not in n and nid not in too_big
            ]
            if not candidates:
                break

            # Scoring includes archived nodes so active and archived candidates
            # share a comparable scale.
            scores = self.scorer.score_all(nodes, edges, versions, include_archived=True)
            ranked = sorted(candidates, key=lambda nid: scores.get(nid, 0.0), reverse=True)

            # Per-round fit check uses the exact promotion delta: promoting X swaps
            # its anchor line for the full node line, and turns exactly its edges to
            # other ARCHIVED nodes live (X–active and X–artifact edges were live
            # already; X–orphaned stay dead). Each newly-live edge is charged its
            # exact rendered length. One O(E) index build per round instead of
            # re-measuring the whole graph per skip.
            archived_ids = {
                nid for nid, n in nodes.items()
                if n.get("_archived") and "_orphaned_ts" not in n
            }
            newly_live_chars: dict[str, int] = {}
            for edge in edges.values():
                f, t = edge["from"], edge["to"]
                if f in archived_ids and t in archived_ids:
                    cost = self.estimator.estimate_edge(edge)
                    newly_live_chars[f] = newly_live_chars.get(f, 0) + cost
                    if t != f:  # a self-loop goes live once, not twice
                        newly_live_chars[t] = newly_live_chars.get(t, 0) + cost

            promoted_this_round = None
            for nid in ranked:
                node = nodes[nid]
                delta = (
                    self.estimator.estimate_node(nid, node)
                    - self.estimator.estimate_archived(nid)
                    + newly_live_chars.get(nid, 0)
                )
                if estimated_chars + delta > fill_ceiling:
                    # Doesn't fit — set aside and try the next-best this round.
                    too_big.add(nid)
                    continue

                # Accept: promote and take an authoritative full re-measure as a
                # guard against any drift between the delta and the real render.
                node["_archived"] = False
                new_estimate = self.estimator.estimate_graph(nodes, edges, include_archived=False)
                if new_estimate > fill_ceiling:
                    node["_archived"] = True
                    too_big.add(nid)
                    continue

                estimated_chars = new_estimate
                promoted.append(nid)
                promoted_this_round = nid
                logger.debug(f"Refilled node '{nid}' (score: {scores.get(nid, 0.0):.2f})")
                break  # active set changed — re-score before picking the next one

            if promoted_this_round is None:
                break  # nothing left fits

        if promoted:
            logger.info(f"Refill: promoted {len(promoted)} archived node(s) to use spare budget, now {estimated_chars} chars")
        return promoted

    def orphan_archived_if_needed(self, nodes: dict, edges: dict) -> list[str]:
        """
        Demote archived nodes to orphaned when archived section exceeds budget.

        Archived nodes show as ID-only lines in kg_read output. If too many accumulate,
        they crowd out active context. This pass keeps archived anchor chars ≤ 30% of max_chars.

        A node is "orphaned" by setting _orphaned_ts = now. Orphaned nodes:
          - Are invisible in kg_read/kg_sync output
          - Still searchable via kg_search (flagged as orphaned)
          - Can be rescued by reading a connected archived node (chain promotion)
          - Are permanently deleted after orphan_grace_days without recall

        Returns list of newly orphaned node IDs.
        """
        # Each archived node costs exactly its rendered anchor line in kg_read
        # (measured by the estimator, so the two never disagree).
        archived_nodes = {
            nid: n for nid, n in nodes.items()
            if n.get("_archived") and "_orphaned_ts" not in n
        }

        if not archived_nodes:
            return []

        anchor_chars = {nid: self.estimator.estimate_archived(nid) for nid in archived_nodes}
        archived_chars = sum(anchor_chars.values())
        budget = int(self.max_chars * ARCHIVED_BUDGET_RATIO)

        if archived_chars <= budget:
            return []

        logger.info(f"Archived section too large: {archived_chars} chars > {budget} budget")

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
            if archived_chars <= budget:
                break
            node = nodes[node_id]
            node["_orphaned_ts"] = current_time
            archived_chars -= anchor_chars[node_id]
            orphaned.append(node_id)
            logger.debug(f"Orphaned archived node '{node_id}' (connectivity: {connectivity.get(node_id, 0)})")

        logger.info(f"Orphaned {len(orphaned)} archived nodes, archived section now {archived_chars} chars")
        return orphaned
