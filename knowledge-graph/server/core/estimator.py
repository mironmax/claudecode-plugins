"""Token estimation for knowledge graph nodes and graphs."""

from .constants import BASE_NODE_TOKENS, CHARS_PER_TOKEN, TOKENS_PER_EDGE


class TokenEstimator:
    """Estimates token costs for nodes and graphs."""

    @staticmethod
    def estimate_node(node: dict) -> int:
        """Estimate token cost for a single node.
        Only gist counts — notes are detail fetched on-demand via kg_read(id),
        not included in the active context budget.
        """
        gist_tokens = len(node.get("gist", "")) // CHARS_PER_TOKEN
        return BASE_NODE_TOKENS + gist_tokens

    @staticmethod
    def estimate_graph(nodes: dict, edges: dict, include_archived: bool = False) -> int:
        """Estimate total token cost for a graph level."""
        if include_archived:
            node_tokens = sum(TokenEstimator.estimate_node(n) for n in nodes.values())
        else:
            node_tokens = sum(
                TokenEstimator.estimate_node(n)
                for n in nodes.values()
                if not n.get("_archived")
            )

        edge_tokens = len(edges) * TOKENS_PER_EDGE
        return node_tokens + edge_tokens
