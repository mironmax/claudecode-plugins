"""Single source of truth for kg_read's rendered graph lines.

Every surface that shows or budgets the graph goes through these three
functions: the kg_read formatter renders them, and the estimator measures
exactly these strings (len + newline). That is the render == charge doctrine
taken to its logical end — the compaction budget and the visible output are
the same characters, so they can never drift apart. There are no token
heuristics and no tunable knobs anywhere in the budget path.
"""


def render_active_line(node_id: str, gist: str) -> str:
    """An active node: id + gist on one line."""
    return f"  {node_id}: {gist}"


def render_archived_line(node_id: str) -> str:
    """An archived node: a collapsed id-only anchor line."""
    return f"  {node_id}"


def render_edge_line(from_ref: str, rel: str, to_ref: str) -> str:
    """A live edge: from --rel--> to."""
    return f"  {from_ref} --{rel}--> {to_ref}"
