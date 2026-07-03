"""kg_read full-graph output: rendering + the inline-guarantee degradation ladder.

The output is guaranteed to fit READ_CHAR_BUDGET characters so the MCP client
always keeps it inline in context (never spills to a persisted file the model
sees only a preview of). Two layers make the guarantee:

  1. The compactor keeps each level's rendered size ≤ MAX_CHARS_PER_LEVEL, and
     two levels plus wrapper text fit the budget by construction.
  2. For graphs the compactor hasn't maintained (legacy, externally edited),
     this module degrades the output at render time: drop the lowest-scored
     archived anchors first, then the lowest-value live edges — never active
     gists. Hidden items are summarized with a count and remain reachable via
     kg_search.

Line rendering is shared with the estimator (core.render), so what is measured
here is exactly what was charged during compaction — render == charge.
"""

from core.constants import READ_CHAR_BUDGET
from core.render import render_active_line, render_archived_line, render_edge_line
from core.utils import is_active, edge_is_live


def _level_parts(label: str, nodes: list, edges: list, scores: dict) -> dict:
    """Split one level into fixed lines and droppable, score-annotated lines."""
    node_map = {n["id"]: n for n in nodes}
    active_ids = {nid for nid, n in node_map.items() if is_active(n)}
    active = [n for n in nodes if not n.get("_archived")]
    archived = [n for n in nodes if n.get("_archived") and "_orphaned_ts" not in n]
    live = [e for e in edges if edge_is_live(e, node_map, active_ids)]

    def endpoint_score(ref: str) -> float:
        # Unknown endpoints (artifact paths, cross-level refs) count as neutral
        # 0.5 — pullable, but not evidence of local importance either way.
        return scores.get(ref, 0.5)

    return {
        "label": label,
        "header": f"=== {label.upper()} — {len(active)} active, {len(archived)} archived ===",
        "active_lines": [render_active_line(n["id"], n.get("gist", "")) for n in active],
        # Droppable pools, each entry (score, rendered_line). Missing scores:
        # an active-but-in-grace node can't appear here (archiving happens past
        # grace), so 0.0 is only a defensive fallback.
        "archived_pool": [
            (scores.get(n["id"], 0.0), render_archived_line(n["id"])) for n in archived
        ],
        "edge_pool": [
            (
                endpoint_score(e["from"]) + endpoint_score(e["to"]),
                render_edge_line(e["from"], e["rel"], e["to"]),
            )
            for e in live
        ],
        "health": _health_line(active, live),
        "hidden_archived": 0,
        "hidden_edges": 0,
    }


def _health_line(active: list, live_edges: list) -> str:
    """Graph health summary. Reflects the graph itself, not the (possibly
    degraded) rendering — hidden anchors/edges still count."""
    connected_ids = set()
    for e in live_edges:
        connected_ids.add(e["from"])
        connected_ids.add(e["to"])
    orphans = [n for n in active if n["id"] not in connected_ids]
    n_count = len(active)
    e_count = len(live_edges)
    o_count = len(orphans)
    o_pct = round(100 * o_count / n_count) if n_count else 0
    avg_edges = round(e_count / n_count, 1) if n_count else 0
    return f"HEALTH: {n_count} nodes, {e_count} edges, {o_count} orphans ({o_pct}%), avg {avg_edges} edges/node"


def _assemble(levels: list[dict], session_line: str) -> str:
    """Render the level parts (post-ladder) into the final output text."""
    out_lines = []
    for part in levels:
        out_lines.append(part["header"])
        if part["active_lines"]:
            out_lines.append("ACTIVE:")
            out_lines.extend(part["active_lines"])
        if part["archived_pool"] or part["hidden_archived"]:
            out_lines.append("ARCHIVED (use kg_read with id to view full content):")
            out_lines.extend(line for _, line in part["archived_pool"])
            if part["hidden_archived"]:
                out_lines.append(
                    f"  …{part['hidden_archived']} more archived hidden (lowest-scored) — kg_search reaches them"
                )
        if part["edge_pool"] or part["hidden_edges"]:
            out_lines.append("EDGES:")
            out_lines.extend(line for _, line in part["edge_pool"])
            if part["hidden_edges"]:
                out_lines.append(f"  …{part['hidden_edges']} more edges hidden (lowest-value)")
        out_lines.append(part["health"])

    text = "\n".join(out_lines)
    total_hidden_archived = sum(p["hidden_archived"] for p in levels)
    total_hidden_edges = sum(p["hidden_edges"] for p in levels)
    if total_hidden_archived or total_hidden_edges:
        text += (
            f"\n\nNote: output degraded to fit the inline budget — "
            f"{total_hidden_archived} archived anchor(s) and {total_hidden_edges} edge(s) hidden. "
            f"kg_search reaches everything; a /kg-maintain pass would restore headroom."
        )
    return text + session_line


def build_full_read(graphs: dict, scores: dict, session_id: str | None) -> str:
    """Render the two-level kg_read output, degraded if needed to fit the budget.

    graphs: store.read_graphs() result. scores: store.scores_for_read() result.
    Active gists are never dropped — if active lines alone exceed the budget
    (possible only on a graph the compactor has never run on), the output may
    exceed it until the next write triggers compaction.
    """
    session_line = f"\n\nSession: {session_id}" if session_id else ""

    levels = [
        _level_parts("User Graph", graphs["user"]["nodes"], graphs["user"]["edges"], scores.get("user", {})),
        _level_parts("Project Graph", graphs["project"]["nodes"], graphs["project"]["edges"], scores.get("project", {})),
    ]
    # Sort droppable pools ascending by score so .pop(0) removes the least
    # valuable item; render order for what survives is by descending value.
    for part in levels:
        part["archived_pool"].sort(key=lambda t: t[0])
        part["edge_pool"].sort(key=lambda t: t[0])

    def over_budget() -> int:
        return len(_assemble(levels, session_line)) - READ_CHAR_BUDGET

    # Ladder step 1: drop archived anchors, lowest-scored first, across BOTH
    # levels as one pool. One item per iteration — the summary/count lines
    # change length as counts grow, so re-measuring the assembled text keeps
    # the accounting exact.
    while over_budget() > 0:
        candidates = [p for p in levels if p["archived_pool"]]
        if not candidates:
            break
        victim = min(candidates, key=lambda p: p["archived_pool"][0][0])
        victim["archived_pool"].pop(0)
        victim["hidden_archived"] += 1

    # Ladder step 2: drop live edges, lowest endpoint-score sum first.
    while over_budget() > 0:
        candidates = [p for p in levels if p["edge_pool"]]
        if not candidates:
            break
        victim = min(candidates, key=lambda p: p["edge_pool"][0][0])
        victim["edge_pool"].pop(0)
        victim["hidden_edges"] += 1

    # Restore reading order: archived/edges by descending value (the pools were
    # ascending for cheap popping).
    for part in levels:
        part["archived_pool"].sort(key=lambda t: t[0], reverse=True)
        part["edge_pool"].sort(key=lambda t: t[0], reverse=True)

    return _assemble(levels, session_line)


def format_node_full(node_id: str, result: dict) -> str:
    """Compact text for a single full node read (replaces raw JSON dumps).

    Shows gist, notes, touches, and the node's own edges — the crumbs for the
    next read — without internal fields like _last_read_ts.
    """
    node = result["node"]
    status = "promoted from archive" if result.get("was_archived") else "active"
    lines = [f"▸ {node_id} ({result['level']}, {status})"]
    lines.append(f"  gist: {node.get('gist', '')}")
    notes = node.get("notes") or []
    if notes:
        lines.append("  notes:")
        lines.extend(f"    - {n}" for n in notes)
    touches = node.get("touches") or []
    if touches:
        lines.append("  touches: " + " · ".join(touches))
    edges = result.get("edges") or []
    if edges:
        lines.append("  edges:")
        lines.extend(f"  {render_edge_line(e['from'], e['rel'], e['to'])}" for e in edges)
    return "\n".join(lines)
