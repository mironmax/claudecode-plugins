"""Single source of truth for kg_read's rendered graph output.

Every surface that shows or budgets the graph goes through this module: the
kg_read formatter renders the plan produced here, and the estimator measures
exactly the same lines (len + newline). That is the render == charge doctrine —
the compaction budget and the visible output are the same characters, so they
can never drift apart. There are no token heuristics and no tunable knobs
anywhere in the budget path.

Format (node-centric adjacency):

  ACTIVE:
    hub-node-id: its gist text
      → uses → other-node
      ← limitation-of ← third-node
    satellite-id: gist...
  ARCHIVED (...):
    anchor-id

Nodes are ordered by graph community — connected clusters render together,
highest-degree (hub) node first, so a cluster reads as one coherent knowledge
paragraph. Each live edge is cited exactly ONCE, under its first-encountered
endpoint (the arrow shows direction); the later endpoint does not repeat it —
one edge, one line, and the budget chars stay available for gists. Local
completeness lives in single-node reads, which list all of a node's edges.
"""

from .utils import is_active, is_orphaned, edge_is_live


def render_active_line(node_id: str, gist: str) -> str:
    """An active node: id + gist on one line."""
    return f"  {node_id}: {gist}"


def render_archived_line(node_id: str) -> str:
    """An archived node: a collapsed id-only anchor line."""
    return f"  {node_id}"


def render_edge_citation(rel: str, other_ref: str, outgoing: bool) -> str:
    """One edge, cited under a node: arrow direction points the relation."""
    arrow = "→" if outgoing else "←"
    return f"    {arrow} {rel} {arrow} {other_ref}"


def render_edge_line(from_ref: str, rel: str, to_ref: str) -> str:
    """A standalone edge triple — used by single-node reads and sync output,
    where an edge appears with both endpoints spelled out."""
    return f"  {from_ref} --{rel}--> {to_ref}"


def plan_level(nodes: dict, edges: dict, include_archived: bool = False) -> dict:
    """Compute the render plan for one graph level.

    Returns:
      {
        "active":   [(node_id, node_line, [(edge, citation_line), ...]), ...]
                    in cluster order (hubs first, communities contiguous),
        "archived": [(node_id, anchor_line), ...],
        "live_edge_count": int,
      }

    include_archived=True plans as if every archived (non-orphaned) node were
    active — used by scoring passes that need comparable full-size costs.

    First-encounter rule: a live edge is attached to whichever of its endpoints
    renders first; an endpoint that never renders as a node here (archived
    anchor, artifact path, cross-level reference) can't cite, so the edge is
    attached to its renderable endpoint.
    """
    renderable = {
        nid for nid, n in nodes.items()
        if is_active(n) or (include_archived and not is_orphaned(n))
    }
    active_ids = {nid for nid, n in nodes.items() if is_active(n)}
    live_check_ids = renderable if include_archived else active_ids

    live_edges = [
        e for e in edges.values()
        if edge_is_live(e, nodes, live_check_ids)
    ]

    # Adjacency among renderable nodes (for communities) + degree over all
    # live edges (hub-ness counts pulls on anchors and artifacts too).
    degree: dict[str, int] = {nid: 0 for nid in renderable}
    neighbours: dict[str, set] = {nid: set() for nid in renderable}
    for e in live_edges:
        f, t = e["from"], e["to"]
        if f in renderable:
            degree[f] += 1
        if t in renderable and t != f:
            degree[t] += 1
        if f in renderable and t in renderable and f != t:
            neighbours[f].add(t)
            neighbours[t].add(f)

    # Communities: connected components over renderable-renderable edges.
    # Components ordered by size desc (then hub degree desc, then id for
    # determinism); within a component, BFS from the hub, higher-degree
    # neighbours first.
    unvisited = set(renderable)
    components: list[list[str]] = []
    while unvisited:
        seed = max(unvisited, key=lambda nid: (degree[nid], nid))
        order, queue, seen = [], [seed], {seed}
        while queue:
            nid = queue.pop(0)
            order.append(nid)
            nexts = sorted(
                (nb for nb in neighbours[nid] if nb in unvisited and nb not in seen),
                key=lambda nb: (-degree[nb], nb),
            )
            queue.extend(nexts)
            seen.update(nexts)
        unvisited -= seen
        components.append(order)
    components.sort(key=lambda c: (-len(c), -degree[c[0]], c[0]))

    ordered = [nid for comp in components for nid in comp]
    position = {nid: i for i, nid in enumerate(ordered)}

    # First-encounter citations.
    cited: dict[str, list] = {nid: [] for nid in ordered}
    for e in live_edges:
        f, t = e["from"], e["to"]
        f_pos = position.get(f)
        t_pos = position.get(t)
        if f_pos is None and t_pos is None:
            continue  # live via artifact endpoints only — nothing renders it
        if f_pos is not None and (t_pos is None or f_pos <= t_pos):
            citer, other, outgoing = f, t, True
        else:
            citer, other, outgoing = t, f, False
        cited[citer].append((e, render_edge_citation(e["rel"], other, outgoing)))

    active_plan = [
        (nid, render_active_line(nid, nodes[nid].get("gist", "")), cited[nid])
        for nid in ordered
    ]
    archived_plan = [
        (nid, render_archived_line(nid))
        for nid, n in nodes.items()
        if n.get("_archived") and not is_orphaned(n) and nid not in renderable
    ]
    return {
        "active": active_plan,
        "archived": archived_plan,
        "live_edge_count": len(live_edges),
    }


def level_body_lines(plan: dict) -> list[str]:
    """Flatten a plan into the exact body lines kg_read renders (no header,
    no health line — those are wrapper text owned by the formatter)."""
    lines: list[str] = []
    if plan["active"]:
        lines.append("ACTIVE:")
        for _nid, node_line, citations in plan["active"]:
            lines.append(node_line)
            lines.extend(citation for _e, citation in citations)
    if plan["archived"]:
        lines.append("ARCHIVED (use kg_read with id to view full content):")
        lines.extend(anchor for _nid, anchor in plan["archived"])
    return lines
