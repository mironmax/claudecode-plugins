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
              compactor-stall root cause) and unconnected active nodes.
              Floor 0.25: a pristine-looking graph still deserves an
              occasional pass (notes rot invisibly), but never urgently.

All three factors stay legible on purpose — the DEBT line names the raw
numbers so a model (or Maxim) can sanity-check the verdict at a glance.
"""

import json
import time
from pathlib import Path

# Gist length the kg-capture standard targets; beyond it a gist reads as a
# wall, and oversized gists are the documented compactor-stall root cause.
GIST_OVERSIZE_CHARS = 300
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
                 active_days_7d: int, now: float | None = None) -> dict:
    """Debt score + factors for one graph. nodes/edges: snapshot lists."""
    now = now or time.time()

    active = [n for n in nodes if not n.get("_archived")]
    oversized = sum(1 for n in active if len(n.get("gist", "")) > GIST_OVERSIZE_CHARS)

    connected: set[str] = set()
    for e in edges:
        connected.add(e.get("from", ""))
        connected.add(e.get("to", ""))
    unconnected = sum(1 for n in active if n["id"] not in connected)

    n_active = len(active)
    deficit_raw = ((oversized / n_active) + 0.5 * (unconnected / n_active)) if n_active else 0.0

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
        "unconnected_active": unconnected,
        "active_nodes": n_active,
        "untended_days": round(untended_days, 1),
        "never_maintained": not last_maintain_ts,
        "active_days_7d": active_days_7d,
    }


def debt_line(debt: dict) -> str:
    """One-line render appended after HEALTH in kg_read / bootstrap output."""
    untended = ("never maintained" if debt["never_maintained"]
                else f"untended {debt['untended_days']:g}d")
    detail = (
        f"{debt['oversized_gists']} oversized gist(s), "
        f"{debt['unconnected_active']} unconnected, {untended}, "
        f"active {debt['active_days_7d']}/7d"
    )
    line = f"DEBT: {debt['level']} ({debt['score']}) — {detail}"
    if debt["level"] == "HIGH":
        line += " — worth a /kg-maintain pass (or a maintenance subagent) now"
    return line


# --------------------------------------------------------------------------
# Disk survey — for the dispatcher endpoint. Reads graph files directly so
# surveying every project does not pull them all into server memory.
# --------------------------------------------------------------------------

def _graph_debt_from_file(graph_path: Path, extra_ts=None, now: float | None = None):
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
    debt = compute_debt(nodes, edges, last_maintain,
                        activity_days(ts_pool, now=now), now=now)
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
            debt, meta = _graph_debt_from_file(gpath, extra_ts=extra_ts, now=now)
            if not debt:
                continue
            rows.append({
                "graph": pdir.name, "level": "project",
                "project_path": meta.get("project_path"), "debt": debt,
            })

    rows.sort(key=lambda r: r["debt"]["score"], reverse=True)
    return rows
