"""Maintenance debt — how urgently a graph needs a /kg-maintain pass.

The score answers one question for a maintenance dispatcher (human, session
subagent, or scheduled tick): of all graphs, which one repays tending FIRST?

    debt = staleness × activity-weight × deficit-weight

  staleness — days since the last recorded maintenance pass (kg_progress task
              "maintain", stamped by the pass itself), saturating at
              STALENESS_FULL_DAYS. A graph never maintained is fully stale.
  activity  — distinct days in the last 7 with any sign of use: node
              _last_read_ts stamps, or (projects) tool_events.json traffic.
              Active graphs accumulate wear AND repay maintenance sooner; a
              dormant graph can wait. Weighted, not gating — floor 0.4, so a
              dormant graph's debt still grows past ignoring eventually.
  deficit   — concrete, countable wear: oversized gists (the documented
              compactor-stall root cause), unconnected active nodes, long
              ids, touches that no longer resolve (anchor), and instance
              nodes that cluster around an unwritten principle (lift).
              Floor 0.25: a pristine-looking graph still deserves an
              occasional pass (notes rot invisibly), but never urgently.

All three factors stay legible on purpose — the DEBT line names the raw
numbers so a model or a person can sanity-check the verdict at a glance.
"""

import json
import re
import time
from pathlib import Path

from .constants import NODE_ID_TARGET_WORDS
from .utils import node_id_has_date, node_id_words

# Gist length the kg-core capture standard targets; beyond it a gist reads as a
# wall, and oversized gists are the documented compactor-stall root cause.
GIST_OVERSIZE_CHARS = 300

# --- Smear detection -------------------------------------------------------
# A term is "smeared" when many nodes re-describe one entity in prose instead
# of edging to the node that owns it — dozens of node texts naming a product
# feature while the hub node that owns it holds three edges. Smearing is what makes project-central vocabulary useless to search — IDF
# sees a saga, not a signal. The detector names the worst offenders so the
# maintain pass can consolidate one entity at a time.
SMEAR_MIN_TERM_LEN = 5
SMEAR_MIN_DF = 6          # absolute floor of holders before a term counts
SMEAR_DF_RATIO = 0.08     # ...or this fraction of the graph, whichever is more
_SMEAR_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{4,}")
_DATED_ID_RE = re.compile(r"20\d\d-\d\d")
# Chronicle verbs and generic dev vocabulary that would false-flag; entities
# the maintain pass should judge (like "plugin") stay in on purpose.
_SMEAR_STOP = frozenset("""
    about above added after again alway always applied approach around before
    behind between broke built cannot cause caused change changed check
    checked clean commit commits config confirmed correct created datum
    decided decision default deploy deployed direct disable disabled doctrine
    dropped enable enabled every finding fixed fixes fresh fully found gists
    graph graphs happen happened hidden inside instead issue issues
    latest lesson lessons local longer maxim means merge merged might minute
    minutes moved needs never nodes nothing observed order other output
    pattern patterns policy pretty problem procedure project projects prompt
    prompts reason removed renamed report resolved restore result results
    review reviewed right root round runs saved second session sessions
    shipped shipping should since small solved stale stamp standard started
    still stopped store style their there these thing things think third
    those three times today under until update updated using value values
    verified version wanted where which while whole without works would wrong
""".split())


def smeared_terms(nodes: list[dict], edges: list[dict], n_top: int = 3,
                  slug: str | None = None) -> list[dict]:
    """Worst smeared terms across ALL tiers (search reaches every tier).

    A term qualifies when its id+gist document frequency exceeds
    max(SMEAR_MIN_DF, SMEAR_DF_RATIO × node count) AND some undated node id
    carries the term as a token — the hub candidate the satellites should
    edge to (most-connected candidate named). Returns
    [{term, df, hub, hub_edges}] worst-first, at most n_top.
    """
    # The project's own name prefixes half the ids by convention — that is a
    # namespace, not a smeared entity ("my-app×126" is noise, a feature name
    # ×48 is the finding). Slug tokens and their singular/plural kin are skipped.
    slug_tokens = set((slug or "").lower().replace("_", "-").split("-")) - {""}

    def _is_slug_term(term: str) -> bool:
        return any(term.startswith(t) or t.startswith(term) for t in slug_tokens)

    texts = {}
    for n in nodes:
        nid = n.get("id", "")
        texts[nid] = (nid + " " + n.get("gist", "")).lower()
    df: dict[str, set] = {}
    for nid, text in texts.items():
        for tok in set(_SMEAR_TOKEN_RE.findall(text)):
            if tok in _SMEAR_STOP or len(tok) < SMEAR_MIN_TERM_LEN or _is_slug_term(tok):
                continue
            df.setdefault(tok, set()).add(nid)

    degree: dict[str, int] = {}
    for e in edges:
        for end in (e.get("from", ""), e.get("to", "")):
            degree[end] = degree.get(end, 0) + 1

    floor = max(SMEAR_MIN_DF, SMEAR_DF_RATIO * len(texts))
    out = []
    for term, holders in df.items():
        if len(holders) < floor:
            continue
        hubs = [nid for nid in holders
                if not _DATED_ID_RE.search(nid)
                and any(t == term or t.startswith(term) for t in nid.split("-"))]
        if not hubs:
            continue
        hub = max(hubs, key=lambda h: degree.get(h, 0))
        out.append({"term": term, "df": len(holders), "hub": hub,
                    "hub_edges": degree.get(hub, 0)})
    out.sort(key=lambda r: r["df"], reverse=True)
    return out[:n_top]

STALENESS_FULL_DAYS = 14
ACTIVITY_FULL_DAYS = 4      # active-days/7d that count as "fully active"
DEBT_HIGH = 0.5
DEBT_MED = 0.3

MAINTAIN_TASK_ID = "maintain"  # kg_progress task the pass stamps


def activity_days(timestamps, now: float | None = None, window_days: int = 7) -> int:
    """Distinct calendar days (UTC buckets) with any timestamp in the window."""
    now = now or time.time()
    cutoff = now - window_days * 86400
    return len({int(ts // 86400) for ts in timestamps if ts and ts > cutoff})


def compute_debt(nodes: list[dict], edges: list[dict],
                 last_maintain_ts: float | None,
                 active_days_7d: int, now: float | None = None,
                 slug: str | None = None, project_root: str | None = None,
                 home: str | Path | None = None) -> dict:
    """Debt score + factors for one graph. nodes/edges: snapshot lists.

    project_root / home: where touches resolve. Anchors are checked only when
    one is given (a stat per path-shaped entry, never a walk), so a caller
    with no filesystem context gets no dangling count rather than a wrong one.
    """
    # Imported here: lift reuses this module's stop list.
    from .anchors import dangling_touches
    from .lift import lift_clusters, lifted_ids

    now = now or time.time()

    active = [n for n in nodes if not n.get("_archived")]
    oversized = sum(1 for n in active if len(n.get("gist", "")) > GIST_OVERSIZE_CHARS)
    # Episodes sharing a lesson nobody has written down once.
    clusters = lift_clusters(nodes, edges)
    lift_members = sum(len(c["members"]) for c in clusters)
    in_lift = {m for c in clusters for m in c["members"]} | lifted_ids(edges)

    # Ids that carry the claim instead of naming the subject, or carry a date.
    # Counted as wear because ids are load-bearing in search (weighted x3,
    # matched for the recall gate) — a wrong name is paid on every prompt, and
    # unlike a long gist it is invisible until someone looks for it. A dated
    # id waiting in a lift cluster is counted there instead: its fix is the
    # principle, and chores do not rename it (core.chores). One already lifted
    # is evidence waiting to archive, not a name to repair.
    long_ids = [
        n["id"] for n in active
        if n["id"] not in in_lift
        and (node_id_words(n["id"]) > NODE_ID_TARGET_WORDS or node_id_has_date(n["id"]))
    ]

    connected: set[str] = set()
    for e in edges:
        connected.add(e.get("from", ""))
        connected.add(e.get("to", ""))
    unconnected = sum(1 for n in active if n["id"] not in connected)

    # Anchors that no longer resolve: the node points somewhere that is gone.
    dangling = (dangling_touches(nodes, project_root, home)
                if (project_root or home) else {})
    n_active = len(active)
    smeared = smeared_terms(nodes, edges, slug=slug)
    deficit_raw = ((oversized / n_active) + 0.5 * (unconnected / n_active)
                   + 0.5 * (len(long_ids) / n_active)
                   + 0.5 * (len(dangling) / n_active)
                   + 0.25 * (lift_members / n_active)) if n_active else 0.0
    # Each smeared term adds a nudge toward a pass; capped so smear alone
    # never spikes HIGH — consolidation is a slow structural payoff.
    deficit_raw += min(0.25, 0.08 * len(smeared))

    if last_maintain_ts:
        untended_days = max(0.0, (now - last_maintain_ts) / 86400)
    else:
        untended_days = float(STALENESS_FULL_DAYS)  # never maintained = fully stale

    staleness = min(1.0, untended_days / STALENESS_FULL_DAYS)
    activity = min(1.0, active_days_7d / ACTIVITY_FULL_DAYS)

    score = staleness * (0.4 + 0.6 * activity) * (0.25 + 0.75 * min(1.0, deficit_raw))
    level = "HIGH" if score >= DEBT_HIGH else ("MED" if score >= DEBT_MED else "LOW")

    return {
        "score": round(score, 2),
        "level": level,
        "oversized_gists": oversized,
        "long_ids": len(long_ids),
        "long_id_examples": sorted(long_ids, key=lambda i: -node_id_words(i))[:3],
        "unconnected_active": unconnected,
        "dangling_nodes": len(dangling),
        "dangling_touches": sum(len(v) for v in dangling.values()),
        "lift_clusters": len(clusters),
        "lift_members": lift_members,
        "active_nodes": n_active,
        "untended_days": round(untended_days, 1),
        "never_maintained": not last_maintain_ts,
        "active_days_7d": active_days_7d,
        "smeared": smeared,
    }


def debt_line(debt: dict) -> str:
    """One-line render appended after HEALTH in kg_read / bootstrap output."""
    untended = ("never maintained" if debt["never_maintained"]
                else f"untended {debt['untended_days']:g}d")
    detail = (
        f"{debt['oversized_gists']} oversized gist(s), "
        f"{debt['long_ids']} long id(s), "
        f"{debt['unconnected_active']} unconnected, "
        f"{debt.get('dangling_touches', 0)} dangling touch(es), "
        f"{debt.get('lift_clusters', 0)} lift cluster(s), {untended}, "
        f"active {debt['active_days_7d']}/7d"
    )
    if debt.get("smeared"):
        worst = ", ".join(f"{s['term']}×{s['df']}→{s['hub']}" for s in debt["smeared"])
        detail += f" — smeared: {worst}"
    line = f"DEBT: {debt['level']} ({debt['score']}) — {detail}"
    if debt["level"] == "HIGH":
        line += " — worth a /kg-maintain pass (or a maintenance subagent) now"
    return line


# --------------------------------------------------------------------------
# Disk survey — for the dispatcher endpoint. Reads graph files directly so
# surveying every project does not pull them all into server memory.
# --------------------------------------------------------------------------

def _graph_debt_from_file(graph_path: Path, extra_ts=None, now: float | None = None,
                          slug: str | None = None):
    """(debt, meta) from a persisted graph file, or (None, {}) if unreadable."""
    try:
        data = json.loads(graph_path.read_text())
    except Exception:
        return None, {}
    nodes = data.get("nodes", {})
    edges = data.get("edges", {})
    nodes = list(nodes.values()) if isinstance(nodes, dict) else nodes
    edges = list(edges.values()) if isinstance(edges, dict) else edges
    meta = data.get("_meta", {})
    last_maintain = meta.get("progress", {}).get(MAINTAIN_TASK_ID, {}).get("last_ts")
    ts_pool = [n.get("_last_read_ts") for n in nodes]
    ts_pool.extend(extra_ts or [])
    # A project graph resolves touches against its own root; the user graph
    # only has home, for `~` and absolute entries.
    debt = compute_debt(nodes, edges, last_maintain,
                        activity_days(ts_pool, now=now), now=now, slug=slug,
                        project_root=meta.get("project_path") if slug else None,
                        home=Path.home())
    return debt, meta


def survey_debt(storage_root: Path, now: float | None = None) -> list[dict]:
    """Debt for the user graph and every project graph on disk, sorted
    neediest-first. Each row: {graph, level ('user'|'project'), project_path?,
    debt:{...}}."""
    rows: list[dict] = []

    user_path = storage_root / "user.json"
    if user_path.exists():
        debt, _meta = _graph_debt_from_file(user_path, now=now)
        if debt:
            rows.append({"graph": "user", "level": "user", "debt": debt})

    projects_dir = storage_root / "projects"
    if projects_dir.exists():
        for pdir in sorted(projects_dir.iterdir()):
            gpath = pdir / "graph.json"
            if not gpath.exists():
                continue
            extra_ts = []
            ev_path = pdir / "tool_events.json"
            if ev_path.exists():
                try:
                    events = json.loads(ev_path.read_text()).get("events", {})
                    extra_ts = [e.get("last_ts") for e in events.values()]
                except Exception:
                    pass
            debt, meta = _graph_debt_from_file(gpath, extra_ts=extra_ts, now=now,
                                               slug=pdir.name)
            if not debt:
                continue
            rows.append({
                "graph": pdir.name, "level": "project",
                "project_path": meta.get("project_path"), "debt": debt,
            })

    rows.sort(key=lambda r: r["debt"]["score"], reverse=True)
    return rows
