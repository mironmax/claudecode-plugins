#!/usr/bin/env python3
"""Self-contained regression tests for GET /api/projects (roadmap/tasks/08).

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_project_listing.py

Covers:
  1. list_projects: tier counts (active/archived/orphaned), edge count,
     project_path missing from _meta, folder removed, counters-only
     directory skipped, unreadable graph.json skipped, custom storage root
  2. Nothing is written to disk by listing
  3. GET /api/projects endpoint shape

Uses a temp KG_STORAGE_ROOT and temp project dirs under ~/.cache — never
touches real graphs under ~/.knowledge-graph.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from fastapi.testclient import TestClient

from core.project_listing import list_projects
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


def _write_graph(pdir: Path, nodes: dict, edges: dict | None = None, meta: dict | None = None):
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "graph.json").write_text(json.dumps({
        "nodes": nodes,
        "edges": edges or {},
        "_meta": meta or {},
    }))


def main():
    print("=== project listing tests (roadmap 08) ===")

    storage = Path(_TMP_STORAGE)
    real_project = Path(tempfile.mkdtemp(prefix="kg-test-real-", dir=str(Path.home() / ".cache")))

    try:
        # --- fixtures ---------------------------------------------------
        # 1. A large, mostly-orphaned graph with a real, existing folder.
        big_dir = storage / "projects" / "big-app"
        nodes = {}
        for i in range(7):
            nodes[f"a{i}"] = {"id": f"a{i}", "gist": "active"}
        for i in range(25):
            nodes[f"r{i}"] = {"id": f"r{i}", "gist": "archived", "_archived": True}
        for i in range(68):
            nodes[f"o{i}"] = {"id": f"o{i}", "gist": "orphaned",
                               "_archived": True, "_orphaned_ts": 1234.0}
        edges = {f"a0->a{i}:rel": {"from": "a0", "to": f"a{i}", "rel": "rel"}
                 for i in range(1, 4)}
        _write_graph(big_dir, nodes, edges, {"project_path": str(real_project)})

        # 2. A graph whose _meta has no project_path (legacy graph).
        nometa_dir = storage / "projects" / "no-meta"
        _write_graph(nometa_dir, {"n1": {"id": "n1", "gist": "g"}}, {}, {})

        # 3. A graph whose folder was since removed.
        gone_dir = storage / "projects" / "gone-project"
        _write_graph(gone_dir, {"n1": {"id": "n1", "gist": "g"}}, {},
                     {"project_path": str(Path.home() / ".cache" / "kg-test-does-not-exist-xyz")})

        # 4. A counters-only directory: no graph.json at all.
        counters_dir = storage / "projects" / "counters-only"
        counters_dir.mkdir(parents=True, exist_ok=True)
        (counters_dir / "tool_events.json").write_text(json.dumps({"events": {}}))

        # 5. An unreadable / corrupt graph.json.
        corrupt_dir = storage / "projects" / "corrupt"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "graph.json").write_text("{not json")

        before = {p: p.stat().st_mtime for p in storage.rglob("*") if p.is_file()}

        # --- 1. list_projects --------------------------------------------
        print("list_projects:")
        rows = list_projects(storage)
        by_slug = {r["slug"]: r for r in rows}

        check("counters-only directory skipped", "counters-only" not in by_slug, by_slug.keys())
        check("corrupt graph.json skipped", "corrupt" not in by_slug, by_slug.keys())
        check("exactly the three real graphs listed",
              set(by_slug.keys()) == {"big-app", "no-meta", "gone-project"}, by_slug.keys())

        big = by_slug["big-app"]
        check("active tier counted", big["active_nodes"] == 7, big)
        check("archived tier counted", big["archived_nodes"] == 25, big)
        check("orphaned tier counted", big["orphaned_nodes"] == 68, big)
        check("edge count", big["edge_count"] == 3, big)
        check("project_path surfaced", big["project_path"] == str(real_project), big)
        check("existing folder flagged true", big["path_exists"] is True, big)
        check("last_used is the graph file's mtime",
              isinstance(big["last_used"], float) and big["last_used"] > 0, big)

        nometa = by_slug["no-meta"]
        check("missing _meta.project_path -> null", nometa["project_path"] is None, nometa)
        check("null path -> path_exists false", nometa["path_exists"] is False, nometa)
        check("no-meta graph still tiered", nometa["active_nodes"] == 1, nometa)

        gone = by_slug["gone-project"]
        check("removed folder still listed", gone["project_path"] is not None, gone)
        check("removed folder -> path_exists false", gone["path_exists"] is False, gone)

        os.utime(nometa_dir / "graph.json", (0, 1000))    # oldest
        os.utime(gone_dir / "graph.json", (0, 2000))
        os.utime(big_dir / "graph.json", (0, 3000))        # newest
        rows = list_projects(storage)
        check("most-recently-modified graph first",
              [r["slug"] for r in rows] == ["big-app", "gone-project", "no-meta"],
              [r["slug"] for r in rows])
        before = {p: p.stat().st_mtime for p in storage.rglob("*") if p.is_file()}

        # --- custom storage root -----------------------------------------
        print("custom storage root:")
        other_root = Path(tempfile.mkdtemp(prefix="kg-test-otherroot-"))
        try:
            _write_graph(other_root / "projects" / "solo", {"z": {"id": "z", "gist": "g"}})
            other_rows = list_projects(other_root)
            check("lists only the custom root's project",
                  [r["slug"] for r in other_rows] == ["solo"], other_rows)
            check("does not leak the primary storage root's projects",
                  "big-app" not in [r["slug"] for r in other_rows], other_rows)
        finally:
            shutil.rmtree(other_root, ignore_errors=True)

        check("empty storage root -> empty list",
              list_projects(Path(tempfile.mkdtemp(prefix="kg-test-empty-"))) == [])

        # --- 2. nothing written --------------------------------------------
        print("no writes:")
        after = {p: p.stat().st_mtime for p in storage.rglob("*") if p.is_file()}
        check("listing wrote no new files and touched none",
              after == before, set(after) ^ set(before))

        # --- 3. endpoint ----------------------------------------------------
        print("endpoint:")
        config = GraphConfig(save_interval=9999)
        session_manager = HTTPSessionManager()
        store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)
        client = TestClient(create_rest_api(store, session_manager, ConnectionManager(), version="test"))

        r = client.get("/api/projects")
        check("endpoint 200", r.status_code == 200, r.text)
        body = r.json()
        check("endpoint shape: 'projects' list", isinstance(body.get("projects"), list), body)
        ep_slugs = {row["slug"] for row in body["projects"]}
        check("endpoint surfaces the same three graphs",
              ep_slugs == {"big-app", "no-meta", "gone-project"}, ep_slugs)

    finally:
        shutil.rmtree(real_project, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
