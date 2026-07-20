"""kg_read full-graph output: rendering + the inline-guarantee degradation ladder.

The output is guaranteed to fit READ_CHAR_BUDGET characters so the MCP client
always keeps it inline in context (never spills to a persisted file the model
sees only a preview of). Two layers make the guarantee:

  1. The compactor keeps each level's rendered size ≤ MAX_CHARS_PER_LEVEL, and
     two levels plus wrapper text fit the budget by construction.
  2. For graphs the compactor hasn't maintained (legacy, externally edited),
     this module degrades the output at render time: drop the lowest-scored
     archived anchors first, then the lowest-value edge citations — never
     active gists. Hidden items are summarized with a count and remain
     reachable via kg_search.

The render plan comes from core.render (node-centric adjacency: clusters
together, hubs first, each edge cited once at its first-encountered endpoint)
— the same plan the estimator measures, so render == charge exactly.
"""

from core.constants import BOOTSTRAP_CHAR_BUDGET, READ_CHAR_BUDGET, SEARCH_CHAR_BUDGET
from core.debt import debt_line
from core.render import plan_level, render_edge_line


def _health_line(plan: dict) -> str:
    """Graph health summary. Reflects the graph itself, not the (possibly
    degraded) rendering — hidden anchors/citations still count."""
    connected_ids = set()
    e_count = 0
    for _nid, _line, citations in plan["active"]:
        for edge, _cline in citations:
            e_count += 1
            connected_ids.add(edge["from"])
            connected_ids.add(edge["to"])
    n_count = len(plan["active"])
    o_count = sum(1 for nid, _l, _c in plan["active"] if nid not in connected_ids)
    o_pct = round(100 * o_count / n_count) if n_count else 0
    avg_edges = round(e_count / n_count, 1) if n_count else 0
    return f"HEALTH: {n_count} nodes, {e_count} edges, {o_count} orphans ({o_pct}%), avg {avg_edges} edges/node"


def _level_parts(label: str, nodes: list, edges: list, scores: dict, preloaded: set | None = None) -> dict:
    """Build one level's render state: fixed node lines + droppable pools.

    preloaded: node ids whose gist the session already holds from the
    session-start preload — they render as id-only "(preloaded)" anchors, so
    the read budget goes to content the session does NOT yet have. Their edge
    citations still render: the preload's own ladder drops citations early, so
    the loud read is where a node's strings become fully visible.
    """
    preloaded = preloaded or set()
    node_map = {n["id"]: n for n in nodes}
    edge_map = {i: e for i, e in enumerate(edges)}
    plan = plan_level(node_map, edge_map)

    def endpoint_score(ref: str) -> float:
        # Unknown endpoints (artifact paths, cross-level refs) count as neutral
        # 0.5 — pullable, but not evidence of local importance either way.
        return scores.get(ref, 0.5)

    active_entries = []
    preloaded_count = 0
    for nid, node_line, citations in plan["active"]:
        if nid in preloaded:
            node_line = f"  {nid} (preloaded)"
            preloaded_count += 1
        active_entries.append({
            "nid": nid,
            "score": scores.get(nid, 0.5),
            "line": node_line,
            # (value, citation_line) — droppable, lowest value first
            "citations": [
                (endpoint_score(e["from"]) + endpoint_score(e["to"]), cline)
                for e, cline in citations
            ],
        })
    return {
        "label": label,
        "header": f"=== {label.upper()} — {len(plan['active'])} active, {len(plan['archived'])} archived ===",
        "active": active_entries,
        # Droppable anchor pool. Missing scores: an active-but-in-grace node
        # can't appear here (archiving happens past grace), so 0.0 is only a
        # defensive fallback.
        "archived_pool": [(scores.get(nid, 0.0), anchor) for nid, anchor in plan["archived"]],
        "health": _health_line(plan),
        "debt_line": None,
        "preloaded_count": preloaded_count,
        "hidden_archived": 0,
        "hidden_edges": 0,
        "hidden_active": 0,
    }


def _assemble(levels: list[dict], session_line: str, degradation_note: bool = True) -> str:
    """Render the level parts (post-ladder) into the final output text.

    degradation_note=False suppresses the trailing maintenance advice — the
    bootstrap is compact BY DESIGN, so "restore headroom" guidance would
    mislead there; its per-section count lines already say what is hidden."""
    out_lines = []
    for part in levels:
        out_lines.append(part["header"])
        if part["active"] or part["hidden_active"]:
            out_lines.append("ACTIVE:")
            for entry in part["active"]:
                out_lines.append(entry["line"])
                out_lines.extend(cline for _v, cline in entry["citations"])
            if part["hidden_active"]:
                out_lines.append(
                    f"  …{part['hidden_active']} more active gist(s) not shown (lowest-scored) — kg_read(session_id) renders the full graph"
                )
            if part["hidden_edges"]:
                out_lines.append(f"  …{part['hidden_edges']} edge(s) hidden (lowest-value)")
        if part["archived_pool"] or part["hidden_archived"]:
            out_lines.append("ARCHIVED (use kg_read with id to view full content):")
            out_lines.extend(anchor for _s, anchor in part["archived_pool"])
            if part["hidden_archived"]:
                out_lines.append(
                    f"  …{part['hidden_archived']} more archived hidden (lowest-scored) — kg_search reaches them"
                )
        out_lines.append(part["health"])
        if part.get("debt_line"):
            out_lines.append(part["debt_line"])

    text = "\n".join(out_lines)
    total_hidden_archived = sum(p["hidden_archived"] for p in levels)
    total_hidden_edges = sum(p["hidden_edges"] for p in levels)
    if degradation_note and (total_hidden_archived or total_hidden_edges):
        text += (
            f"\n\nNote: output degraded to fit the inline budget — "
            f"{total_hidden_archived} archived anchor(s) and {total_hidden_edges} edge(s) hidden. "
            f"kg_search reaches everything; a /kg-maintain pass would restore headroom."
        )
    return text + session_line


def _build_levels(graphs: dict, scores: dict, preloaded: set | None = None, debt: dict | None = None) -> list[dict]:
    """Level parts for both graphs, droppable pools sorted for the ladder
    (ascending by value so .pop(0) removes the least valuable item first).

    debt: optional {"user": {...}, "project": {...}} from store.maintenance_debt —
    rendered as a DEBT line after each level's HEALTH (inside the measured
    text, so the budget accounting stays exact)."""
    levels = [
        _level_parts("User Graph", graphs["user"]["nodes"], graphs["user"]["edges"], scores.get("user", {}), preloaded),
        _level_parts("Project Graph", graphs["project"]["nodes"], graphs["project"]["edges"], scores.get("project", {}), preloaded),
    ]
    if debt:
        for part, key in ((levels[0], "user"), (levels[1], "project")):
            if debt.get(key):
                part["debt_line"] = debt_line(debt[key])
    for part in levels:
        part["archived_pool"].sort(key=lambda t: t[0])
        for entry in part["active"]:
            entry["citations"].sort(key=lambda t: t[0])
    return levels


def _fit_to_budget(levels: list[dict], session_line: str, budget: int, prefix: str = "", drop_active: bool = False, degradation_note: bool = True) -> None:
    """Degrade the level parts in place until the assembled text fits budget.

    One item per iteration — the summary/count lines change length as counts
    grow, so re-measuring the assembled text keeps the accounting exact.
    Ladder: archived anchors (lowest-scored first, both levels as one pool) →
    edge citations (lowest endpoint-score sum first) → active gists (lowest-
    scored first; bootstrap only — kg_read never drops active gists).
    """
    def over_budget() -> int:
        return len(prefix) + len(_assemble(levels, session_line, degradation_note)) - budget

    # Ladder step 1: drop archived anchors.
    while over_budget() > 0:
        candidates = [p for p in levels if p["archived_pool"]]
        if not candidates:
            break
        victim = min(candidates, key=lambda p: p["archived_pool"][0][0])
        victim["archived_pool"].pop(0)
        victim["hidden_archived"] += 1

    # Ladder step 2: drop edge citations.
    while over_budget() > 0:
        best = None  # (value, part, entry)
        for part in levels:
            for entry in part["active"]:
                if entry["citations"] and (best is None or entry["citations"][0][0] < best[0]):
                    best = (entry["citations"][0][0], part, entry)
        if best is None:
            break
        _value, part, entry = best
        entry["citations"].pop(0)
        part["hidden_edges"] += 1

    # Ladder step 3 (bootstrap only): drop whole active entries, lowest score
    # first. The compact core keeps the hubs; the loud read shows the rest.
    while drop_active and over_budget() > 0:
        best = None  # (score, part, index)
        for part in levels:
            for i, entry in enumerate(part["active"]):
                if best is None or entry["score"] < best[0]:
                    best = (entry["score"], part, i)
        if best is None:
            break
        _score, part, i = best
        part["active"].pop(i)
        part["hidden_active"] += 1

    # Restore reading order. Anchors sort ALPHABETICALLY: the archived list is
    # id-only, so score order is invisible to the reader anyway — alphabetical
    # clusters related name prefixes (kg-*, night-ops-*) and makes a long list
    # scannable. Score still governs what gets DROPPED (the ladder above).
    # Citations read well enough sorted by descending value.
    for part in levels:
        part["archived_pool"].sort(key=lambda t: t[1])
        for entry in part["active"]:
            entry["citations"].sort(key=lambda t: t[0], reverse=True)


def build_full_read(graphs: dict, scores: dict, session_id: str | None, preloaded: set | None = None, debt: dict | None = None) -> str:
    """Render the two-level kg_read output, degraded if needed to fit the budget.

    graphs: store.read_graphs() result. scores: store.scores_for_read() result.
    preloaded: node ids served by the session-start preload — their gists are
    already in the session's context, so they render as id-only anchors and the
    freed budget keeps more archived anchors and edges visible.
    Active gists are never dropped — if active lines alone exceed the budget
    (possible only on a graph the compactor has never run on), the output may
    exceed it until the next write triggers compaction.
    """
    session_line = f"\n\nSession: {session_id}" if session_id else ""
    levels = _build_levels(graphs, scores, preloaded, debt)

    prefix = ""
    total_preloaded = sum(p["preloaded_count"] for p in levels)
    if total_preloaded:
        prefix = (
            f"{total_preloaded} gist(s) already shown by the session-start preload render as "
            f"id-only '(preloaded)' anchors below — kg_read(session_id, ids=[...]) re-reads any in full.\n\n"
        )

    _fit_to_budget(levels, session_line, READ_CHAR_BUDGET, prefix=prefix)
    return prefix + _assemble(levels, session_line)


def build_bootstrap(graphs: dict, scores: dict, session_id: str, debt: dict | None = None) -> dict:
    """Render the session-start preload: a compact core under BOOTSTRAP_CHAR_BUDGET.

    Hook additionalContext rides a much smaller inline window than tool results
    (~10K chars vs ~50K), so this render may drop what kg_read never would:
    whole active gists, lowest-scored first — the hubs stay, and the loud
    kg_read renders everything the preload dropped without repeating what it
    kept. Returns the final injectable text (instruction header included, so
    render == charge covers every character the hook emits), the graph body
    alone (for older hooks that compose their own header), the ids actually
    shown (they seed the session's preload/seen sets), and counts for the
    hook's user-visible one-liner.
    """
    header = (
        "KG MEMORY PRELOADED — compact core ONLY: a PARTIAL view holding the top-scored "
        "nodes of both graphs ('…N more not shown' lines below count what was dropped). "
        f"session_id: {session_id} (pass it to every kg_* call). "
        "REQUIRED before any substantive work: call kg_read(session_id) ONCE — it renders "
        "everything this preload dropped without repeating what is shown here, and only "
        'AFTER that full read announce "I have recalled KG Memories". '
        "kg_read(session_id, ids=[...]) reads nodes in depth; kg_search looks anything up. "
        "Subagents never receive this preload — when dispatching one, put the relevant gists "
        "or kg_* instructions in its prompt.\n\n"
    )
    session_line = f"\n\nSession: {session_id}"
    levels = _build_levels(graphs, scores, debt=debt)
    totals = {
        "user_active": len(levels[0]["active"]),
        "project_active": len(levels[1]["active"]),
    }

    _fit_to_budget(levels, session_line, BOOTSTRAP_CHAR_BUDGET, prefix=header, drop_active=True, degradation_note=False)

    body = _assemble(levels, session_line, degradation_note=False)
    shown = [entry["nid"] for part in levels for entry in part["active"]]
    return {
        "context": header + body,
        "text": body,
        "shown_ids": shown,
        "stats": {**totals, "shown_gists": len(shown)},
    }


def format_search(query: str, result: dict, session_note: str = "") -> str:
    """Compact text for kg_search results, capped at SEARCH_CHAR_BUDGET.

    Top hits get full treatment — notes included only for nodes the session
    hasn't seen yet (gists may repeat as reminders; notes never re-dump).
    Connections show how the hits relate: connector nodes as id+gist, then the
    path edges. Remaining matches are one-liners. When over budget, trim from
    the least valuable end: one-liners first, then path edges, then notes of
    the lowest-ranked hits.
    """
    def hit_lines(r):
        flags = r["level"]
        if r.get("archived"):
            flags += ", archived"
        if r.get("orphaned"):
            flags += ", orphaned"
        if r.get("seen"):
            flags += ", seen"
        lines = [f"▸ {r['id']} ({flags})", f"  gist: {r['gist']}"]
        lines.extend(f"    - {n}" for n in r.get("notes", []))
        return lines

    top_blocks = [hit_lines(r) for r in result["top"]]
    connector_lines = [f"  {c['id']}: {c['gist']}" for c in result["connectors"]]
    edge_lines = [
        f"  {e['from']} --{e['rel']}--> {e['to']}" for e in result["path_edges"]
    ]
    more_lines = [f"  {m['id']}: {m['gist']}" for m in result["more"]]

    def assemble() -> str:
        lines = [f"Found {result['total']} match(es) for '{query}' — top {len(top_blocks)}:{session_note}"]
        for block in top_blocks:
            lines.append("")
            lines.extend(block)
        if connector_lines or edge_lines:
            lines.append("")
            lines.append("CONNECTIONS between hits:")
            lines.extend(connector_lines)
            lines.extend(edge_lines)
        if more_lines:
            lines.append("")
            lines.append("MORE MATCHES:")
            lines.extend(more_lines)
        return "\n".join(lines)

    # Trim ladder: one-liners → path edges (with their connectors) → notes of
    # lowest-ranked hits. Gists of the top hits are never dropped.
    while len(assemble()) > SEARCH_CHAR_BUDGET and more_lines:
        more_lines.pop()
    while len(assemble()) > SEARCH_CHAR_BUDGET and (edge_lines or connector_lines):
        if edge_lines:
            edge_lines.pop()
        else:
            connector_lines.pop()
    while len(assemble()) > SEARCH_CHAR_BUDGET:
        trimmed = False
        for block in reversed(top_blocks):
            if len(block) > 2:  # has note lines beyond header+gist
                block.pop()
                trimmed = True
                break
        if not trimmed:
            break
    return assemble()


def format_node_full(node_id: str, result: dict) -> str:
    """Compact text for a single full node read (replaces raw JSON dumps).

    Shows gist, notes, touches, and ALL of the node's own edges — the crumbs
    for the next read. The full-graph view cites each edge only once (at its
    first-rendered endpoint); this is where a node's complete neighbourhood is
    always visible.
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
