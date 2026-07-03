#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.16 change areas.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0916.py

Covers:
  1. Exact-char accounting: the estimator charges precisely the strings
     kg_read renders (render == charge, no token heuristics)
  2. The inline-guarantee degradation ladder in build_full_read: output always
     fits READ_CHAR_BUDGET; archived anchors drop first (lowest-scored), then
     edges; active gists are never dropped
  3. format_node_full: compact text node reads (no raw JSON, no _internals),
     including the node's own edges as crumbs
  4. Session reuse: lookup() is non-mutating, register() still mints sessions
  5. Cross-level / artifact edge preservation in _clean_orphaned_edges

Uses only in-memory fixtures — never touches real graphs under
~/.knowledge-graph.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.estimator import CharEstimator
from core.render import render_active_line, render_archived_line, render_edge_line
from core.constants import READ_CHAR_BUDGET
from mcp_http.read_format import build_full_read, format_node_full

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


def empty_level():
    return {"nodes": [], "edges": []}


# --- 1. exact-char accounting -------------------------------------------------
def test_exact_chars():
    print("exact chars:")
    est = CharEstimator()

    node = {"id": "some-long-kebab-node-id", "gist": "A gist with some words in it."}
    line = render_active_line("some-long-kebab-node-id", node["gist"])
    check("active node charged len(line)+1",
          est.estimate_node("some-long-kebab-node-id", node) == len(line) + 1)

    anchor = render_archived_line("ios-safari-overflow-clip-clips-out-of-box-absolute-descendant")
    check("archived anchor charged its real rendered length (not a flat 5 tokens)",
          est.estimate_archived("ios-safari-overflow-clip-clips-out-of-box-absolute-descendant") == len(anchor) + 1)

    edge = {"from": "scheduled-agents-share-one-account-quota",
            "rel": "cross-project-contention-refines",
            "to": "overnight-session-loop-doctrine"}
    eline = render_edge_line(edge["from"], edge["rel"], edge["to"])
    check("edge charged its real rendered length", est.estimate_edge(edge) == len(eline) + 1)

    # graph estimate == sum of exactly the rendered lines
    nodes = {
        "a": {"id": "a", "gist": "alpha"},
        "b": {"id": "b", "gist": "beta", "_archived": True},
        "o": {"id": "o", "gist": "orphan", "_archived": True, "_orphaned_ts": 1.0},
    }
    edges = {
        "a->b:r": {"from": "a", "to": "b", "rel": "r"},          # live
        "b->missing/file.py:r": {"from": "b", "to": "missing/file.py", "rel": "r"},  # artifact, live
        "a->o:r": {"from": "a", "to": "o", "rel": "r"},          # orphaned endpoint, dead
    }
    expected = (
        len(render_active_line("a", "alpha")) + 1
        + len(render_archived_line("b")) + 1
        + len(render_edge_line("a", "r", "b")) + 1
        + len(render_edge_line("b", "r", "missing/file.py")) + 1
    )
    check("graph estimate is the exact sum of rendered lines",
          est.estimate_graph(nodes, edges) == expected,
          f"{est.estimate_graph(nodes, edges)} != {expected}")


# --- 2. degradation ladder ----------------------------------------------------
def _big_graphs(n_archived=2500, n_active=30):
    """A user graph large enough to blow READ_CHAR_BUDGET via archived anchors."""
    nodes = []
    for i in range(n_active):
        nodes.append({"id": f"active-node-{i:04d}", "gist": f"gist number {i} with a bit of text"})
    for i in range(n_archived):
        nodes.append({"id": f"archived-node-with-a-long-kebab-id-{i:05d}", "_archived": True, "gist": "x"})
    edges = [
        {"from": f"active-node-{i:04d}", "to": f"active-node-{(i + 1) % n_active:04d}", "rel": "relates-to"}
        for i in range(n_active)
    ]
    graphs = {"user": {"nodes": nodes, "edges": edges}, "project": empty_level()}
    # score archived ascending by index: low index = low score = dropped first
    scores = {"user": {f"archived-node-with-a-long-kebab-id-{i:05d}": i / n_archived for i in range(n_archived)}, "project": {}}
    for i in range(n_active):
        scores["user"][f"active-node-{i:04d}"] = 0.9
    return graphs, scores


def test_ladder():
    print("degradation ladder:")

    # Small graph: nothing degraded, no note
    small = {"user": {"nodes": [{"id": "a", "gist": "alpha"}], "edges": []}, "project": empty_level()}
    out = build_full_read(small, {"user": {}, "project": {}}, "sess1234")
    check("small graph renders whole", "a: alpha" in out and "degraded" not in out)
    check("session id included", "Session: sess1234" in out)

    graphs, scores = _big_graphs()
    raw_anchor_chars = sum(len(render_archived_line(n["id"])) + 1 for n in graphs["user"]["nodes"] if n.get("_archived"))
    check("fixture actually oversized", raw_anchor_chars > READ_CHAR_BUDGET, raw_anchor_chars)

    out = build_full_read(graphs, scores, "sess1234")
    check("output fits READ_CHAR_BUDGET", len(out) <= READ_CHAR_BUDGET, f"{len(out)} > {READ_CHAR_BUDGET}")

    # every active gist survives
    missing_active = [i for i in range(30) if f"active-node-{i:04d}: gist number {i}" not in out]
    check("no active gist dropped", not missing_active, missing_active)

    # degradation is reported
    check("degradation note present", "degraded to fit the inline budget" in out)
    check("hidden count line present", "more archived hidden" in out)

    # lowest-scored anchors dropped first: the top-scored archived id must
    # survive, the bottom-scored must be hidden
    check("highest-scored anchor survives", "archived-node-with-a-long-kebab-id-02499" in out)
    check("lowest-scored anchor hidden", "archived-node-with-a-long-kebab-id-00000" not in out)

    # all edges survive here (dropping anchors was enough)
    check("edges kept when anchors suffice", out.count("--relates-to-->") == 30, out.count("--relates-to-->"))

    # Step 2: still over budget after ALL anchors dropped -> edges drop too,
    # lowest endpoint-score first
    n_active = 400
    nodes = [{"id": f"n{i:03d}", "gist": "g" * 50} for i in range(n_active)]
    edges = []
    for i in range(n_active):
        for j in range(1, 5):
            edges.append({"from": f"n{i:03d}", "to": f"n{(i + j) % n_active:03d}", "rel": "very-long-relationship-name-taking-space"})
    graphs2 = {"user": {"nodes": nodes, "edges": edges}, "project": empty_level()}
    scores2 = {"user": {f"n{i:03d}": i / n_active for i in range(n_active)}, "project": {}}
    out2 = build_full_read(graphs2, scores2, None)
    check("edge-drop stage keeps output within budget", len(out2) <= READ_CHAR_BUDGET, len(out2))
    check("edge hidden count present", "more edges hidden" in out2)
    # highest-scored endpoints' edge should survive; lowest should not
    check("high-value edge survives", "n399 --very-long-relationship-name-taking-space--> n001" in out2
          or "n398 --very-long-relationship-name-taking-space-->" in out2)


# --- 3. compact node read ------------------------------------------------------
def test_format_node_full():
    print("node read format:")
    result = {
        "node": {
            "id": "my-node", "gist": "The headline.",
            "notes": ["why one", "why two"],
            "touches": ["src/a.py:30-40", "docs/b.md"],
            "_last_read_ts": 123.0, "_created_ts": 1.0,
        },
        "level": "project",
        "was_archived": True,
        "edges": [
            {"from": "my-node", "to": "other-node", "rel": "extends"},
            {"from": "third", "to": "my-node", "rel": "refines"},
        ],
    }
    text = format_node_full("my-node", result)
    check("header carries level + promotion", "my-node (project, promoted from archive)" in text)
    check("gist present", "gist: The headline." in text)
    check("notes bulleted", "- why one" in text and "- why two" in text)
    check("touches joined", "src/a.py:30-40" in text and "docs/b.md" in text)
    check("edges as crumbs", "my-node --extends--> other-node" in text and "third --refines--> my-node" in text)
    check("no internal fields leaked", "_last_read_ts" not in text and "_created_ts" not in text)
    check("not JSON", not text.strip().startswith("{"))

    # minimal node: no notes/touches/edges sections
    text2 = format_node_full("bare", {"node": {"id": "bare", "gist": "g"}, "level": "user", "was_archived": False, "edges": []})
    check("bare node omits empty sections", "notes:" not in text2 and "touches:" not in text2 and "edges:" not in text2)


# --- 4. session reuse -----------------------------------------------------------
def test_session_reuse():
    print("session reuse:")
    import tempfile
    from core import constants as C

    with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as tmp:
        os.environ["KG_STORAGE_ROOT"] = tmp
        try:
            from mcp_http.session_manager import HTTPSessionManager
            sm = HTTPSessionManager()
            check("lookup unknown id returns None (no auto-create)", sm.lookup("nope1234") is None)
            check("lookup did not create the session", sm.count() == 0)

            reg = sm.register(os.path.expanduser("~"))
            sid = reg["session_id"]
            info = sm.lookup(sid)
            check("lookup finds registered session with path", info is not None and info["project_path"])

            # reads that pass session_id do NOT register: count stays 1
            sm.increment_ops(sid)
            sm.increment_ops(sid)
            check("ops increment without new sessions", sm.count() == 1 and sm.lookup(sid)["op_count"] == 2)
        finally:
            del os.environ["KG_STORAGE_ROOT"]


# --- 5. cross-level / artifact edge preservation --------------------------------
def test_edge_cleanup():
    print("edge cleanup:")
    from types import SimpleNamespace
    from mcp_http.store import MultiProjectGraphStore

    user_graph = {"nodes": {"user-doctrine": {"id": "user-doctrine", "gist": "u"}}, "edges": {}}
    project_graph = {
        "nodes": {"proj-node": {"id": "proj-node", "gist": "p"}},
        "edges": {
            "keep-artifact": {"from": "proj-node", "to": "www/app/file.css", "rel": "final-state"},
            "keep-crosslevel": {"from": "proj-node", "to": "user-doctrine", "rel": "applies"},
            "keep-local": {"from": "proj-node", "to": "proj-node", "rel": "self"},
            "drop-dangling": {"from": "proj-node", "to": "deleted-node", "rel": "was"},
        },
    }
    fake_store = SimpleNamespace(graphs={"user": user_graph, "project:/x": project_graph})
    MultiProjectGraphStore._clean_orphaned_edges(fake_store, project_graph)

    kept = set(project_graph["edges"].keys())
    check("artifact edge kept", "keep-artifact" in kept, kept)
    check("cross-level edge kept (endpoint in user graph)", "keep-crosslevel" in kept, kept)
    check("local edge kept", "keep-local" in kept, kept)
    check("true dangling edge removed", "drop-dangling" not in kept, kept)


def main():
    print("=== v0.9.16 regression tests ===")
    test_exact_chars()
    test_ladder()
    test_format_node_full()
    test_session_reuse()
    test_edge_cleanup()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
