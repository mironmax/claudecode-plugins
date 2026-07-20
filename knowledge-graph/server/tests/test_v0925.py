#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.25 change areas.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0925.py

Covers maintenance debt:
  1. compute_debt math: staleness/activity/deficit factors, level thresholds,
     activity-day bucketing
  2. debt_line rendering (HIGH call-to-action, never-maintained wording)
  3. store.maintenance_debt: stamp resets staleness, per-level keys
  4. DEBT lines render after HEALTH in kg_read and bootstrap, budgets hold
  5. survey_debt disk scan: neediest-first order, project_path from _meta,
     tool_events activity folded in
  6. GET /api/maintenance_debt endpoint shape

Uses a temp KG_STORAGE_ROOT and a temp project under ~/.cache — never touches
real graphs under ~/.knowledge-graph.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from fastapi.testclient import TestClient

from core.constants import BOOTSTRAP_CHAR_BUDGET, READ_CHAR_BUDGET
from core.debt import (
    MAINTAIN_TASK_ID,
    activity_days,
    compute_debt,
    debt_line,
    survey_debt,
)
from mcp_http.read_format import build_bootstrap, build_full_read
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


NOW = time.time()


def _nodes(n_active, oversized=0, archived=0):
    out = []
    for i in range(n_active):
        gist = "x" * (400 if i < oversized else 50)
        out.append({"id": f"n{i}", "gist": gist})
    for i in range(archived):
        out.append({"id": f"a{i}", "gist": "old", "_archived": True})
    return out


def main():
    print("=== v0.9.25 maintenance debt tests ===")

    # --- 1. compute_debt math -------------------------------------------------
    print("compute_debt:")
    nodes = _nodes(10, oversized=5)
    edges = [{"from": f"n{i}", "to": f"n{i+1}", "rel": "r"} for i in range(6)]
    d = compute_debt(nodes, edges, None, active_days_7d=4, now=NOW)
    check("dirty active never-maintained -> HIGH", d["level"] == "HIGH", d)
    check("oversized counted", d["oversized_gists"] == 5, d)
    check("unconnected counted", d["unconnected_active"] == 3, d)
    check("never_maintained flagged", d["never_maintained"])

    d2 = compute_debt(nodes, edges, NOW - 3600, active_days_7d=4, now=NOW)
    check("fresh stamp -> LOW", d2["level"] == "LOW" and d2["score"] < 0.1, d2)

    pristine = _nodes(10)
    all_edges = [{"from": f"n{i}", "to": f"n{(i+1) % 10}", "rel": "r"} for i in range(10)]
    d3 = compute_debt(pristine, all_edges, None, active_days_7d=0, now=NOW)
    check("pristine dormant -> LOW", d3["level"] == "LOW", d3)
    d4 = compute_debt(pristine, all_edges, None, active_days_7d=7, now=NOW)
    check("pristine active never-maintained stays sub-MED", d4["score"] < 0.3, d4)

    check("archived nodes ignored",
          compute_debt(_nodes(2, archived=50), [], NOW - 60, 0, now=NOW)["active_nodes"] == 2)
    check("empty graph safe", compute_debt([], [], None, 0, now=NOW)["score"] >= 0)

    check("activity_days buckets distinct days",
          activity_days([NOW, NOW - 60, NOW - 86400 * 2, NOW - 86400 * 30], now=NOW) == 2)
    check("activity_days ignores None", activity_days([None, 0, NOW], now=NOW) == 1)

    # --- 2. debt_line ---------------------------------------------------------
    print("debt_line:")
    line = debt_line(d)
    check("HIGH line carries call-to-action", "kg-maintain" in line, line)
    check("never-maintained wording", "never maintained" in line, line)
    line2 = debt_line(d2)
    check("LOW line stays quiet", "kg-maintain" not in line2, line2)

    # --- 3+4. store + render integration -------------------------------------
    print("store + render:")
    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)
    client = TestClient(create_rest_api(store, session_manager, ConnectionManager(), version="test"))

    try:
        sid = session_manager.register(project_dir)["session_id"]
        store.put_node(level="project", node_id="big-one",
                       gist="g" * 400, session_id=sid)
        store.put_node(level="user", node_id="u-one", gist="user fact", session_id=sid)

        debt = store.maintenance_debt(sid)
        check("both levels present", "user" in debt and "project" in debt, debt.keys())
        check("project oversized seen", debt["project"]["oversized_gists"] == 1, debt["project"])
        check("project never maintained", debt["project"]["never_maintained"])

        store.set_progress(MAINTAIN_TASK_ID, {"last_ts": NOW}, "project", sid)
        debt = store.maintenance_debt(sid)
        check("stamp resets staleness",
              not debt["project"]["never_maintained"] and debt["project"]["score"] < 0.15,
              debt["project"])

        graphs = store.read_graphs(sid)
        scores = store.scores_for_read(sid)
        text = build_full_read(graphs, scores, sid, debt=debt)
        check("full read renders DEBT after HEALTH",
              text.index("HEALTH:") < text.index("DEBT:"), text[-400:])
        check("two DEBT lines (both levels)", text.count("DEBT:") == 2)
        check("full read fits budget", len(text) <= READ_CHAR_BUDGET + 200, len(text))

        boot = build_bootstrap(graphs, scores, sid, debt=debt)
        check("bootstrap renders DEBT", "DEBT:" in boot["context"])
        check("bootstrap fits budget", len(boot["context"]) <= BOOTSTRAP_CHAR_BUDGET,
              len(boot["context"]))

        no_debt = build_full_read(graphs, scores, sid)
        check("debt omitted -> no DEBT line", "DEBT:" not in no_debt)

        # --- 5. survey_debt (disk) ------------------------------------------
        print("survey:")
        storage = Path(_TMP_STORAGE)
        pdir = storage / "projects" / "busy-proj"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "graph.json").write_text(json.dumps({
            "nodes": {f"n{i}": {"id": f"n{i}", "gist": "y" * 400,
                                "_last_read_ts": NOW - i * 86400} for i in range(6)},
            "edges": {},
            "_meta": {"project_path": "/home/someone/busy-proj", "progress": {}},
        }))
        (pdir / "tool_events.json").write_text(json.dumps({
            "events": {"read:a.py": {"count": 3, "sessions": ["s1"], "last_ts": NOW}},
            "throttle": {},
        }))
        qdir = storage / "projects" / "quiet-proj"
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / "graph.json").write_text(json.dumps({
            "nodes": {"q1": {"id": "q1", "gist": "tidy"}},
            "edges": {},
            "_meta": {"progress": {MAINTAIN_TASK_ID: {"last_ts": NOW - 3600}}},
        }))

        rows = survey_debt(storage, now=NOW)
        names = [r["graph"] for r in rows]
        check("survey covers user + projects", set(names) >= {"user", "busy-proj", "quiet-proj"},
              names)
        check("neediest first",
              names.index("busy-proj") < names.index("quiet-proj"), names)
        busy = next(r for r in rows if r["graph"] == "busy-proj")
        check("project_path surfaced", busy["project_path"] == "/home/someone/busy-proj", busy)
        check("tool_events feed activity", busy["debt"]["active_days_7d"] >= 4, busy["debt"])

        # --- 6. endpoint ------------------------------------------------------
        r = client.get("/api/maintenance_debt")
        body = r.json()
        check("endpoint 200 + rows", r.status_code == 200
              and any(g["graph"] == "busy-proj" for g in body.get("graphs", [])), r.text)

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
