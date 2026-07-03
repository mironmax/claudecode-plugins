#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.19 change areas.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0919.py

Covers:
  1. build_bootstrap: hard BOOTSTRAP_CHAR_BUDGET cap (header included),
     shown_ids match the rendered gists, lowest-scored gists drop first,
     hubs survive, hidden-count line present, stats correct
  2. build_full_read preload dedup: preloaded nodes render as id-only
     anchors, unpreloaded render in full, prefix note present, budget
     freed for archived anchors, no-preload render unchanged
  3. Session preloaded-tracking: set/get, overwrite-not-append semantics
  4. Small graph: bootstrap shows everything, drops nothing

Uses only in-memory fixtures — never touches real graphs under
~/.knowledge-graph.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constants import BOOTSTRAP_CHAR_BUDGET, READ_CHAR_BUDGET
from mcp_http.read_format import build_bootstrap, build_full_read

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


# --- fixtures ----------------------------------------------------------------
def _big_graphs(n_active=60, n_archived=40, gist_chars=200):
    """Two levels big enough that a 10K bootstrap MUST drop active gists."""
    def level(prefix):
        nodes = []
        edges = []
        for i in range(n_active):
            nodes.append({"id": f"{prefix}-n{i}", "gist": f"g{i} " + "x" * gist_chars})
        for i in range(n_archived):
            nodes.append({"id": f"{prefix}-arch{i}", "gist": "archived", "_archived": True})
        # hub: n0 connected to the next 8 nodes
        for i in range(1, 9):
            edges.append({"from": f"{prefix}-n0", "to": f"{prefix}-n{i}", "rel": "uses"})
        return {"nodes": nodes, "edges": edges}

    graphs = {"user": level("u"), "project": level("p")}
    # scores: n0 hub highest, then descending by index — n{59} is lowest
    scores = {
        lvl: {f"{p}-n{i}": 1.0 - i / 100 for i in range(n_active)}
        for lvl, p in (("user", "u"), ("project", "p"))
    }
    for lvl, p in (("user", "u"), ("project", "p")):
        scores[lvl].update({f"{p}-arch{i}": 0.1 for i in range(n_archived)})
    return graphs, scores


def _small_graphs():
    graphs = {
        "user": {"nodes": [{"id": "u-a", "gist": "alpha"}, {"id": "u-b", "gist": "beta"}],
                 "edges": [{"from": "u-a", "to": "u-b", "rel": "pairs-with"}]},
        "project": {"nodes": [{"id": "p-a", "gist": "gamma"}], "edges": []},
    }
    scores = {"user": {"u-a": 0.9, "u-b": 0.8}, "project": {"p-a": 0.7}}
    return graphs, scores


# --- 1. bootstrap budget + selection ------------------------------------------
def test_bootstrap_budget():
    print("bootstrap budget + selection:")
    graphs, scores = _big_graphs()
    result = build_bootstrap(graphs, scores, "testsess")

    check("context fits hard budget", len(result["context"]) <= BOOTSTRAP_CHAR_BUDGET,
          f"len={len(result['context'])}")
    check("header included in context", result["context"].startswith("KG MEMORY PRELOADED"),
          result["context"][:60])
    check("session id in header", "testsess" in result["context"][:400])
    check("body excludes header", not result["text"].startswith("KG MEMORY"), result["text"][:40])

    shown = set(result["shown_ids"])
    check("some gists dropped on big graph", len(shown) < 120, f"shown={len(shown)}")
    check("hubs survive the ladder", "u-n0" in shown and "p-n0" in shown)
    check("lowest-scored dropped first", "u-n59" not in shown and "p-n59" not in shown)

    # every shown id renders a gist line; every dropped id does not appear as one
    for nid in list(shown)[:5]:
        check(f"shown id renders ({nid})", f"  {nid}: " in result["context"])
    check("hidden count line present", "more active gist(s) not shown" in result["context"])
    check("stats totals", result["stats"]["user_active"] == 60
          and result["stats"]["project_active"] == 60
          and result["stats"]["shown_gists"] == len(result["shown_ids"]),
          result["stats"])


def test_bootstrap_small_graph():
    print("bootstrap on small graph:")
    graphs, scores = _small_graphs()
    result = build_bootstrap(graphs, scores, "smallsess")
    check("everything shown", set(result["shown_ids"]) == {"u-a", "u-b", "p-a"},
          result["shown_ids"])
    check("no hidden-count line", "not shown" not in result["context"])
    check("well under budget", len(result["context"]) < 2500, len(result["context"]))


# --- 2. full read preload dedup ------------------------------------------------
def test_read_dedup():
    print("full read preload dedup:")
    graphs, scores = _big_graphs()
    boot = build_bootstrap(graphs, scores, "s1")
    preloaded = set(boot["shown_ids"])

    text = build_full_read(graphs, scores, "s1", preloaded=preloaded)
    check("read fits its budget", len(text) <= READ_CHAR_BUDGET, len(text))
    check("prefix note present", "session-start preload" in text[:300], text[:200])

    sample_pre = next(iter(preloaded))
    check("preloaded renders as anchor", f"  {sample_pre} (preloaded)" in text)
    check("preloaded gist NOT repeated", f"  {sample_pre}: g" not in text)

    dropped = [f"u-n{i}" for i in range(60) if f"u-n{i}" not in preloaded]
    check("dropped-by-preload gists render fully",
          bool(dropped) and all(f"  {nid}: g" in text for nid in dropped[:5]),
          dropped[:5])

    plain = build_full_read(graphs, scores, "s1")
    check("no-preload render unchanged (no anchors)", "(preloaded)" not in plain)
    check("no-preload render has no prefix", "session-start preload" not in plain[:300])
    check("dedup frees budget (more content fits)",
          plain.count("…") >= text.count("…"),
          f"plain hides {plain.count('…')}, deduped hides {text.count('…')}")


# --- 3. session preloaded tracking ----------------------------------------------
def test_session_preloaded():
    print("session preloaded tracking:")
    # keep the manager off the real sessions file
    import core.constants as C
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    os.environ["KG_STORAGE_ROOT"] = str(tmp)
    try:
        from mcp_http.session_manager import HTTPSessionManager
        mgr = HTTPSessionManager()
        reg = mgr.register(str(Path.home()))
        sid = reg["session_id"]

        check("empty before preload", mgr.get_preloaded(sid) == set())
        mgr.set_preloaded(sid, ["a", "b"])
        check("set records ids", mgr.get_preloaded(sid) == {"a", "b"})
        mgr.set_preloaded(sid, ["c"])
        check("set overwrites, not appends", mgr.get_preloaded(sid) == {"c"})
        check("unknown session -> empty set", mgr.get_preloaded("nope") == set())
        # JSON round-trip shape
        mgr.save_sessions()
        mgr2 = HTTPSessionManager()
        check("survives save/load", mgr2.get_preloaded(sid) == {"c"})
    finally:
        del os.environ["KG_STORAGE_ROOT"]


# --- 4. v0.9.20 readability tweaks ---------------------------------------------
def test_archived_alphabetical():
    print("archived list alphabetical:")
    graphs, scores = _big_graphs(n_active=3, n_archived=15, gist_chars=20)
    text = build_full_read(graphs, scores, "s2")
    for level_tag in ("u", "p"):
        ids = [f"{level_tag}-arch{i}" for i in range(15)]
        positions = [text.index(f"\n  {nid}\n") if f"\n  {nid}\n" in text else text.index(f"\n  {nid}") for nid in sorted(ids)]
        check(f"{level_tag}: anchors render in alphabetical order",
              positions == sorted(positions), positions)


def test_gist_nudge():
    print("gist length nudge:")
    from core.utils import GIST_SCAN_LIMIT, gist_length_warning
    check("short gist -> no nudge", gist_length_warning("x" * GIST_SCAN_LIMIT) == "")
    long_warn = gist_length_warning("x" * (GIST_SCAN_LIMIT + 50))
    check("long gist -> nudge", "scan best" in long_warn and str(GIST_SCAN_LIMIT + 50) in long_warn, long_warn)


if __name__ == "__main__":
    test_bootstrap_budget()
    test_bootstrap_small_graph()
    test_read_dedup()
    test_session_preloaded()
    test_archived_alphabetical()
    test_gist_nudge()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
