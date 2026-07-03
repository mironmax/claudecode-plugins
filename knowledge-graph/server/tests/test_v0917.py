#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.17 change areas.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0917.py

Covers:
  1. Node-centric render plan: cluster ordering (hubs first, communities
     contiguous), first-encounter edge citation (one edge = one line),
     edges to anchors/artifacts cited at their renderable endpoint
  2. Render == charge identity: estimate_graph measures exactly the plan lines
  3. format_search: seen-dedup (gists repeat, notes never), trim ladder order,
     hard char cap
  4. Session seen-tracking: mark/get, dedup, JSON-serializable shape
  5. Connection paths: pairwise BFS between hits, hop limit

Uses only in-memory fixtures — never touches real graphs under
~/.knowledge-graph.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.estimator import CharEstimator
from core.render import plan_level, level_body_lines
from core.constants import SEARCH_CHAR_BUDGET
from mcp_http.read_format import format_search

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


# --- 1. node-centric plan -----------------------------------------------------
def _cluster_fixture():
    nodes = {
        "hub": {"id": "hub", "gist": "the hub"},
        "sat-a": {"id": "sat-a", "gist": "satellite a"},
        "sat-b": {"id": "sat-b", "gist": "satellite b"},
        "island-1": {"id": "island-1", "gist": "second cluster one"},
        "island-2": {"id": "island-2", "gist": "second cluster two"},
        "loner": {"id": "loner", "gist": "no edges at all"},
        "anchor": {"id": "anchor", "gist": "archived", "_archived": True},
    }
    edges = {
        "e1": {"from": "hub", "to": "sat-a", "rel": "uses"},
        "e2": {"from": "sat-b", "to": "hub", "rel": "refines"},
        "e3": {"from": "hub", "to": "anchor", "rel": "supersedes"},
        "e4": {"from": "island-1", "to": "island-2", "rel": "pairs-with"},
        "e5": {"from": "sat-a", "to": "www/site/file.css", "rel": "final-state"},
        "e6": {"from": "anchor", "to": "other/file.py", "rel": "documents"},  # no renderable citer
    }
    return nodes, edges


def test_plan():
    print("node-centric plan:")
    nodes, edges = _cluster_fixture()
    plan = plan_level(nodes, edges)
    order = [nid for nid, _line, _c in plan["active"]]

    # hub cluster (3 nodes) renders before island cluster (2), loner last
    check("big cluster first", order.index("hub") < order.index("island-1"), order)
    check("loner last", order[-1] == "loner", order)
    hub_cluster_pos = [order.index(n) for n in ("hub", "sat-a", "sat-b")]
    check("cluster contiguous", max(hub_cluster_pos) - min(hub_cluster_pos) == 2, order)
    check("hub leads its cluster", order.index("hub") == min(hub_cluster_pos), order)

    # first-encounter: each live edge cited exactly once
    all_lines = level_body_lines(plan)
    text = "\n".join(all_lines)
    for rel in ("uses", "refines", "supersedes", "pairs-with", "final-state"):
        count = sum(1 for line in all_lines if f" {rel} " in line)
        check(f"edge '{rel}' cited exactly once", count == 1, count)

    # edge to an archived anchor is cited at the ACTIVE endpoint
    check("anchor edge cited at active node", "→ supersedes → anchor" in text, text)
    # artifact edge cited at its node
    check("artifact edge cited", "→ final-state → www/site/file.css" in text)
    # edge whose only endpoints are anchor+artifact has no citer -> invisible
    check("no-citer edge invisible", "documents" not in text)

    # citations attach to the FIRST-rendered endpoint only
    first_of_pair = min(order.index("island-1"), order.index("island-2"))
    citing = plan["active"][first_of_pair]
    check("pair edge cited at earlier endpoint", len(citing[2]) == 1, citing)


# --- 2. render == charge identity ----------------------------------------------
def test_identity():
    print("render == charge:")
    nodes, edges = _cluster_fixture()
    plan = plan_level(nodes, edges)
    manual = sum(len(line) + 1 for line in level_body_lines(plan))
    est = CharEstimator.estimate_graph(nodes, edges)
    check("estimate equals rendered body", est == manual, f"{est} != {manual}")

    est_all = CharEstimator.estimate_graph(nodes, edges, include_archived=True)
    plan_all = plan_level(nodes, edges, include_archived=True)
    manual_all = sum(len(line) + 1 for line in level_body_lines(plan_all))
    check("include_archived identity holds", est_all == manual_all, f"{est_all} != {manual_all}")
    # structurally: the anchor is planned as a full node (no archived section),
    # and its formerly citer-less artifact edge now has a citer
    all_text = "\n".join(level_body_lines(plan_all))
    check("include_archived plans anchor as full node",
          "  anchor: archived" in all_text and not plan_all["archived"], all_text)
    check("anchor's artifact edge becomes visible", "documents" in all_text)


# --- 3. search formatting --------------------------------------------------------
def _search_result(n_top=3, n_more=8, notes_per=3, seen_ids=()):
    def rec(i, seen):
        r = {"level": "project", "id": f"hit-{i}", "gist": f"gist of hit {i}",
             "archived": False, "orphaned": False, "seen": seen, "score": 1.0 - i / 10}
        if not seen:
            r["notes"] = [f"note {j} of hit {i}" for j in range(notes_per)]
        return r
    return {
        "top": [rec(i, f"hit-{i}" in seen_ids) for i in range(n_top)],
        "more": [{"level": "project", "id": f"more-{i}", "gist": f"gist more {i}", "seen": False}
                 for i in range(n_more)],
        "connectors": [{"id": "conn-x", "gist": "connector gist", "level": "user", "seen": False}],
        "path_edges": [{"from": "hit-0", "to": "conn-x", "rel": "links"},
                       {"from": "conn-x", "to": "hit-1", "rel": "links"}],
        "total": 42,
    }


def test_search_format():
    print("search format:")
    out = format_search("query words", _search_result(seen_ids={"hit-1"}))
    check("total shown", "Found 42 match(es)" in out)
    check("unseen hit keeps notes", "note 0 of hit 0" in out)
    check("seen hit marked", "hit-1 (project, seen)" in out)
    check("seen hit gist still shown", "gist of hit 1" in out)
    check("seen hit notes NOT dumped", "note 0 of hit 1" not in out)
    check("connections section", "CONNECTIONS between hits:" in out and "conn-x: connector gist" in out)
    check("path edges rendered", "hit-0 --links--> conn-x" in out)
    check("more matches one-line", "more-3: gist more 3" in out)

    # cap: huge notes force the trim ladder; one-liners drop before notes
    huge = _search_result(n_top=5, n_more=10, notes_per=40)
    for r in huge["top"]:
        if "notes" in r:
            r["notes"] = [f"{n} " + "x" * 200 for n in r["notes"]]
    out2 = format_search("q", huge)
    check("output capped", len(out2) <= SEARCH_CHAR_BUDGET, len(out2))
    check("top gists never dropped", all(f"gist of hit {i}" in out2 for i in range(5)))


# --- 4. seen tracking --------------------------------------------------------------
def test_seen_tracking():
    print("seen tracking:")
    import tempfile
    with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as tmp:
        os.environ["KG_STORAGE_ROOT"] = tmp
        try:
            from mcp_http.session_manager import HTTPSessionManager
            sm = HTTPSessionManager()
            sid = sm.register(os.path.expanduser("~"))["session_id"]
            check("fresh session sees nothing", sm.get_seen(sid) == set())
            sm.mark_seen(sid, ["a", "b"])
            sm.mark_seen(sid, ["b", "c"])
            check("seen accumulates deduped", sm.get_seen(sid) == {"a", "b", "c"})
            check("stored JSON-serializable", isinstance(sm.lookup(sid)["seen_ids"], list))
            sm.mark_seen("unknown-session", ["x"])
            check("unknown session is a no-op", sm.lookup("unknown-session") is None)
        finally:
            del os.environ["KG_STORAGE_ROOT"]


# --- 5. connection paths ---------------------------------------------------------
def test_connection_paths():
    print("connection paths:")
    from types import SimpleNamespace
    from mcp_http.store import MultiProjectGraphStore

    # chain: A - m1 - m2 - B  plus a far node C beyond hop limit from A
    nodes = {n: {"id": n, "gist": n} for n in ("A", "m1", "m2", "B", "f1", "f2", "f3", "f4", "C")}
    edges = {}
    chain = [("A", "m1"), ("m1", "m2"), ("m2", "B"),
             ("B", "f1"), ("f1", "f2"), ("f2", "f3"), ("f3", "f4"), ("f4", "C")]
    for i, (f, t) in enumerate(chain):
        edges[f"e{i}"] = {"from": f, "to": t, "rel": "r"}
    fake = SimpleNamespace(graphs={"user": {"nodes": nodes, "edges": edges}})

    path = MultiProjectGraphStore._connection_paths(fake, ["A", "B"], ["user"])
    used = {(e["from"], e["to"]) for e in path}
    check("A-B path found via intermediates", used == {("A", "m1"), ("m1", "m2"), ("m2", "B")}, used)

    path2 = MultiProjectGraphStore._connection_paths(fake, ["A", "C"], ["user"])
    check("beyond hop limit -> no path", path2 == [], path2)


def main():
    print("=== v0.9.17 regression tests ===")
    test_plan()
    test_identity()
    test_search_format()
    test_seen_tracking()
    test_connection_paths()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
