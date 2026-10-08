"""Recurrence credit — a lesson needed again earns what an endorsement earns.

An instance-of edge says "this case is that lesson". When the case was
written after the lesson, the lesson was needed again: the strongest evidence
a node is alive, and one the scorer never saw (a principle could gather a
dozen later cases and still sink). Each such edge earns its target one
_useful_ts stamp, dated the day the case was written, so it decays like an
endorsement given then. Stamping the day of crediting would make months-old
recurrences look like this week's.

One rule, two moments: put_edge applies it to the edge being written, and
every graph load reconciles all edges — the backfill of history, and the
catch-up for edges that arrived any other way (merge, editor, hand edit).
It is idempotent: each credit is keyed on the target by the case's creation
time, which a rename does not change, so re-running adds nothing.

A lift (older episodes edged to a new principle) is not a recurrence and
earns nothing. Deleting the edge later keeps the credit: the case happened.
"""

from .constants import LIFT_EDGE_REL

RECURRENCE_FIELD = "_recurrence_ts"  # creation times of the cases already credited


def credit_edge(edge: dict, nodes: dict, target_nodes: dict | None = None) -> dict | None:
    """Credit one edge if it is an uncredited recurrence; return what was done.

    nodes holds the edge's source; target_nodes is where the target lives when
    it is not in nodes (a user node cited from a project graph).
    """
    if edge.get("rel") != LIFT_EDGE_REL:
        return None
    src = nodes.get(edge["from"])
    tgt = nodes.get(edge["to"])
    in_user = False
    if tgt is None and target_nodes is not None:
        tgt, in_user = target_nodes.get(edge["to"]), True
    if src is None or tgt is None:
        return None
    case_ts = src.get("_created_ts")
    if not case_ts or case_ts <= tgt.get("_created_ts", 0):
        return None
    credited = tgt.setdefault(RECURRENCE_FIELD, [])
    if case_ts in credited:
        return None
    credited.append(case_ts)
    tgt.setdefault("_useful_ts", []).append(case_ts)
    return {"id": edge["to"], "instance": edge["from"], "case_ts": case_ts, "cross_level": in_user}


def reconcile(nodes: dict, edges: dict, target_nodes: dict | None = None) -> list[dict]:
    """Credit every uncredited recurrence in a graph. Returns the credits made."""
    credits = []
    for edge in list(edges.values()):
        done = credit_edge(edge, nodes, target_nodes)
        if done:
            credits.append(done)
    return credits
