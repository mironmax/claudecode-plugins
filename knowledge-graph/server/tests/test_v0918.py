#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.18 change areas.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0918.py

Covers:
  1. kg_useful (store.mark_useful): like budget, one vote per node per
     session, unknown ids, decaying timestamps land on the node, versions
     untouched (a like is not a content write)
  2. Scorer: tie-aware percentiles (equal raws share average rank; an
     all-zero usefulness column collapses to uniform 0.5), usefulness term
     ranks a liked node above an otherwise-identical unliked one, decay
  3. Namespace helpers: construction, inspection, kind extraction
  4. _meta.namespace stamped on save

Uses a throwaway storage root — never touches ~/.knowledge-graph.
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- tiny test runner -------------------------------------------------------
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


# --- 1. kg_useful ------------------------------------------------------------
def test_mark_useful():
    print("kg_useful:")
    from core.constants import MAX_LIKES_PER_SESSION

    with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as tmp:
        os.environ["KG_STORAGE_ROOT"] = tmp
        try:
            from mcp_http.session_manager import HTTPSessionManager
            from mcp_http.store import MultiProjectGraphStore, GraphConfig
            from pathlib import Path

            sm = HTTPSessionManager()
            config = GraphConfig(storage_root=Path(tmp), user_path=Path(tmp) / "user.json")
            store = MultiProjectGraphStore(config, sm)
            sid = sm.register(os.path.expanduser("~"))["session_id"]

            for i in range(7):
                store.put_node("user", f"n{i}", f"gist {i}", session_id=sid)
            version_before = dict(store._versions["user"])

            r = store.mark_useful(["n0", "n1"], sid)
            check("likes accepted", r["accepted"] == ["n0", "n1"], r)
            check("budget decremented", r["remaining"] == MAX_LIKES_PER_SESSION - 2, r)
            check("timestamp landed on node", len(store.graphs["user"]["nodes"]["n0"].get("_useful_ts", [])) == 1)

            r2 = store.mark_useful(["n0"], sid)
            check("second vote same session rejected", r2["accepted"] == [] and "already" in r2["rejected"]["n0"], r2)
            check("node keeps single timestamp", len(store.graphs["user"]["nodes"]["n0"]["_useful_ts"]) == 1)

            r3 = store.mark_useful(["n2", "n3", "n4", "n5"], sid)
            check("budget cap enforced", r3["accepted"] == ["n2", "n3", "n4"] and "exhausted" in r3["rejected"]["n5"], r3)
            check("remaining zero", r3["remaining"] == 0)

            r4 = store.mark_useful(["nope"], sm.register(os.path.expanduser("~"))["session_id"])
            check("unknown id rejected", r4["rejected"].get("nope") == "not found", r4)

            check("likes do not bump versions (not a content write)",
                  store._versions["user"] == version_before)

            # a NEW session can like the same node again -> second timestamp
            sid2 = sm.register(os.path.expanduser("~"))["session_id"]
            store.mark_useful(["n0"], sid2)
            check("new session adds second timestamp",
                  len(store.graphs["user"]["nodes"]["n0"]["_useful_ts"]) == 2)

            store.shutdown() if hasattr(store, "shutdown") else None
        finally:
            del os.environ["KG_STORAGE_ROOT"]


# --- 2. scorer ---------------------------------------------------------------
def test_scorer():
    print("scorer:")
    from core.scorer import NodeScorer
    from core.constants import GRACE_PERIOD_DAYS

    old = time.time() - (GRACE_PERIOD_DAYS + 1) * 24 * 3600
    sc = NodeScorer(GRACE_PERIOD_DAYS)

    # identical twins, one liked recently — liked one must outrank
    nodes = {
        "liked": {"id": "liked", "gist": "g" * 40, "_created_ts": old,
                  "_useful_ts": [time.time() - 3600]},
        "plain": {"id": "plain", "gist": "g" * 40, "_created_ts": old},
        "plain2": {"id": "plain2", "gist": "g" * 40, "_created_ts": old},
    }
    scores = sc.score_all(nodes, {}, {})
    check("liked node outranks identical unliked", scores["liked"] > scores["plain"],
          scores)
    check("unliked twins tie exactly", scores["plain"] == scores["plain2"], scores)

    # decay: an ancient like is worth less than a fresh one
    now = time.time()
    fresh = sc._usefulness({"_useful_ts": [now - 3600]}, now)
    stale = sc._usefulness({"_useful_ts": [now - 400 * 24 * 3600]}, now)
    check("fresh like beats ancient like", fresh > stale * 5, (fresh, stale))
    check("no likes -> zero raw", sc._usefulness({}, now) == 0.0)

    # tie-aware percentiles: with NO likes anywhere, usefulness must not
    # perturb relative order (all share the same average-rank percentile)
    nodes2 = {
        f"m{i}": {"id": f"m{i}", "gist": "g", "_created_ts": old,
                  "_last_read_ts": old + i}
        for i in range(5)
    }
    scores2 = sc.score_all(nodes2, {}, {})
    order = sorted(scores2, key=scores2.get)
    check("zero-likes column preserves recency order",
          order == [f"m{i}" for i in range(5)], order)


# --- 3. namespace helpers -------------------------------------------------------
def test_namespaces():
    print("namespaces:")
    from core.constants import (
        USER_NAMESPACE, project_namespace, is_project_namespace, namespace_kind,
    )
    key = project_namespace("/home/user/proj")
    check("project key shape", key == "project:/home/user/proj", key)
    check("project key detected", is_project_namespace(key))
    check("user key not project", not is_project_namespace(USER_NAMESPACE))
    check("kind extraction", namespace_kind(key) == "project" and namespace_kind("user") == "user")
    check("future kinds extract cleanly", namespace_kind("role:cmo") == "role")


# --- 4. namespace meta stamped on save --------------------------------------------
def test_namespace_meta():
    print("namespace meta:")
    from core.persistence import GraphPersistence
    from pathlib import Path

    with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as tmp:
        up = GraphPersistence(Path(tmp) / "user.json")
        up.save({"nodes": {}, "edges": {}}, {})
        meta = json.loads((Path(tmp) / "user.json").read_text())["_meta"]
        check("user graph stamped", meta["namespace"] == {"kind": "user", "owner": None}, meta)

        pp = GraphPersistence(Path(tmp) / "graph.json", project_path="/home/x/proj")
        pp.save({"nodes": {}, "edges": {}}, {})
        meta2 = json.loads((Path(tmp) / "graph.json").read_text())["_meta"]
        check("project graph stamped", meta2["namespace"] == {"kind": "project", "owner": None}, meta2)


def main():
    print("=== v0.9.18 regression tests ===")
    test_mark_useful()
    test_scorer()
    test_namespaces()
    test_namespace_meta()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
