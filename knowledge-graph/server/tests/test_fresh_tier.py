#!/usr/bin/env python3
"""Self-contained tests for the fresh tier and the budget-band rebalance.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_fresh_tier.py

FRESH TIER. The newest nodes by creation time stay active while their node
lines fit in a share of the budget: unscored, never archived, and first back
when archived. A window by budget, not by days, follows the project's pace.

MAINTENANCE READS. A session that opened a pass or a chore (kg_progress on
its task) reads to judge, not to use: no recency stamp, no promotion.

REBALANCE. Between the fill ceiling and the budget neither compaction nor
refill acts, so a well-ranked archived node could sit below a poorly ranked
active one indefinitely. Rebalance swaps them when the archived one wins by
the resurrection margin and the swap fits the budget.

Uses only in-memory fixtures — never touches real graphs under
~/.knowledge-graph.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-test-fresh-")

from core.compactor import Compactor
from core.constants import COMPACTION_TARGET_RATIO, RESURRECTION_MARGIN
from core.estimator import CharEstimator
from core.render import render_active_line
from core.scorer import NodeScorer


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


def line(nid, gist):
    return len(render_active_line(nid, gist)) + 1


OLD = time.time() - 60 * 86400


def node(nid, gist="g" * 60, ts=OLD, archived=False, likes=0):
    n = {"id": nid, "gist": gist, "_created_ts": ts}
    if archived:
        n["_archived"] = True
    if likes:
        n["_useful_ts"] = [time.time() - 3600] * likes
    return n


# --- 1. fresh_ids -------------------------------------------------------------

def test_fresh_ids():
    print("fresh tier selection:")
    nodes = {
        "a": node("a", "a" * 50, ts=3e9),
        "b": node("b", "b" * 50, ts=2e9),
        "c": node("c", "c" * 50, ts=1e9),
        "legacy": node("legacy", "l" * 50, ts=None),
        "gone": node("gone", "o" * 50, ts=4e9),
    }
    del nodes["legacy"]["_created_ts"]
    nodes["gone"]["_orphaned_ts"] = 1.0
    two = line("a", "a" * 50) + line("b", "b" * 50)
    fresh = NodeScorer(two).fresh_ids(nodes, {})
    check("newest two that fit, orphan excluded", fresh == {"a", "b"}, fresh)

    everything = NodeScorer(10_000).fresh_ids(nodes, {})
    check("unstamped node counts as oldest but joins when room allows",
          everything == {"a", "b", "c", "legacy"}, everything)

    nodes["b"]["gist"] = "b" * 400
    fresh = NodeScorer(two).fresh_ids(nodes, {})
    check("stops at the first node that does not fit (no gap-filling)", fresh == {"a"}, fresh)

    check("zero budget means no fresh tier", NodeScorer(0).fresh_ids(nodes, {}) == set())

    # a node is charged its edge citations too, each shared edge once
    from core.render import render_edge_citation
    nodes = {"a": node("a", "a" * 50, ts=3e9), "b": node("b", "b" * 50, ts=2e9)}
    edges = {"a->b:r": {"from": "a", "to": "b", "rel": "r"}}
    cite = len(render_edge_citation("r", "b", True)) + 1
    lines = line("a", "a" * 50) + line("b", "b" * 50)
    check("lines alone no longer fit two linked nodes",
          NodeScorer(lines).fresh_ids(nodes, edges) == {"a"})
    check("lines plus the shared edge once fit both",
          NodeScorer(lines + cite).fresh_ids(nodes, edges) == {"a", "b"})
    nodes["b"]["_orphaned_ts"] = 1.0
    check("an edge to an orphaned node costs nothing",
          NodeScorer(line("a", "a" * 50)).fresh_ids(nodes, edges) == {"a"})


# --- 2. compaction never archives the fresh tier ------------------------------

def test_compaction_spares_fresh():
    print("compaction and the fresh tier:")
    nodes = {f"old{i}": node(f"old{i}", "x" * 200) for i in range(8)}
    nodes["new"] = node("new", "n" * 200, ts=time.time())
    sc = NodeScorer(line("new", "n" * 200))
    est = CharEstimator()
    comp = Compactor(sc, est, max_chars=1000)
    archived = comp.compact_if_needed(nodes, {}, {}, label="t")
    check("over budget: older nodes archived", len(archived) > 0, archived)
    check("the fresh node stays active", not nodes["new"].get("_archived"))
    check("fresh node is not scored", "new" not in sc.score_all(nodes, {}, {}))


# --- 3. rebalance inside the band ---------------------------------------------

def _band_graph():
    nodes = {
        "low1": node("low1"), "low2": node("low2"), "low3": node("low3"),
        "star": node("star", archived=True, likes=3),
        "dull": node("dull", archived=True),
    }
    return nodes


def test_rebalance():
    print("rebalance:")
    est = CharEstimator()
    sc = NodeScorer(0)

    nodes = _band_graph()
    now_chars = est.estimate_graph(nodes, {})
    max_chars = now_chars + 20
    check("fixture sits in the band", now_chars >= int(max_chars * COMPACTION_TARGET_RATIO),
          (now_chars, max_chars))
    comp = Compactor(sc, est, max_chars=max_chars)
    check("refill does not act in the band", comp.refill_if_room(nodes, {}, {}) == [])

    swaps = comp.rebalance(nodes, {}, {})
    check("the liked archived node swaps in", [p for p, _ in swaps] == ["star"], swaps)
    check("star is now active", not nodes["star"].get("_archived"))
    check("budget still holds", est.estimate_graph(nodes, {}) <= max_chars)
    check("a second rebalance is a no-op (no thrash)", comp.rebalance(nodes, {}, {}) == [])

    # a swap that would break the budget is reverted
    nodes = _band_graph()
    nodes["star"]["gist"] = "s" * 400
    comp = Compactor(sc, est, max_chars=est.estimate_graph(nodes, {}) + 20)
    check("oversized winner is not swapped in", comp.rebalance(nodes, {}, {}) == [])
    check("state unchanged after the revert",
          nodes["star"].get("_archived") is True and not nodes["low1"].get("_archived"))

    # equal scores never trade places
    nodes = _band_graph()
    del nodes["star"]["_useful_ts"]
    comp = Compactor(sc, est, max_chars=est.estimate_graph(nodes, {}) + 20)
    check(f"no swap under the {RESURRECTION_MARGIN} margin", comp.rebalance(nodes, {}, {}) == [])


def test_rebalance_and_refill_with_fresh():
    print("rebalance/refill with fresh nodes:")
    est = CharEstimator()

    # an archived fresh node (state from before the tier) beats any score,
    # and an active fresh node is never swapped out
    nodes = _band_graph()
    nodes["newest"] = node("newest", archived=True, ts=time.time())
    nodes["newer"] = node("newer", ts=time.time() - 60)
    sc = NodeScorer(line("newest", "g" * 60) + line("newer", "g" * 60))
    check("both newest nodes are in the tier", sc.fresh_ids(nodes, {}) == {"newest", "newer"})
    comp = Compactor(sc, est, max_chars=est.estimate_graph(nodes, {}) + 20)
    swaps = comp.rebalance(nodes, {}, {})
    check("archived fresh node comes back first", swaps and swaps[0][0] == "newest", swaps)
    check("active fresh node never swapped out", all(out != "newer" for _, out in swaps), swaps)

    # refill: with room for one promotion, the archived fresh node wins over a liked one
    nodes = {
        "anchor": node("anchor"),
        "star": node("star", archived=True, likes=3),
        "newest": node("newest", archived=True, ts=time.time()),
    }
    sc = NodeScorer(line("newest", "g" * 60))
    one = est.estimate_graph(nodes, {}) + line("newest", "g" * 60) + 5
    comp = Compactor(sc, est, max_chars=int(one / COMPACTION_TARGET_RATIO) + 1)
    promoted = comp.refill_if_room(nodes, {}, {})
    check("refill promotes the fresh node first", promoted[:1] == ["newest"], promoted)


# --- 4. explain ------------------------------------------------------------------

def test_explain_fresh():
    print("explain:")
    nodes = {"old1": node("old1"), "old2": node("old2"), "new": node("new", ts=time.time())}
    sc = NodeScorer(line("new", "g" * 60))
    data = sc.explain("new", nodes, {}, {})
    check("fresh node reported protected", data["fresh"]["protected"] is True, data["fresh"])
    check("no automatic score while fresh", data["score"] is None)
    check("preview score offered", isinstance(data["preview_score"], float), data)
    check("explain leaves the graph untouched", "_archived" not in nodes["new"])
    data = sc.explain("old1", nodes, {}, {})
    check("older node is eligible", data["eligible"] and data["score"] is not None, data)


# --- 5. maintenance reads have no effect ------------------------------------------

def test_maintenance_reads():
    print("maintenance reads:")
    from mcp_http.session_manager import HTTPSessionManager
    from mcp_http.store import GraphConfig, MultiProjectGraphStore
    sm = HTTPSessionManager()
    store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, broadcast_callback=None)
    work = sm.register(str(Path.home()), claude_sid="cc-fresh-work")["session_id"]
    maint = sm.register(str(Path.home()), claude_sid="cc-fresh-maint")["session_id"]
    for nid in ("judged", "used"):
        store.put_node(level="user", node_id=nid, gist="g", session_id=work)
    # Archive after both writes: a write runs refill, which would promote first.
    for nid in ("judged", "used"):
        store.graphs["user"]["nodes"][nid]["_archived"] = True
        store.graphs["user"]["nodes"][nid].pop("_last_read_ts", None)

    store.get_progress("maintain", level="user", session_id=maint)
    store.read_node("judged", level="user", session_id=maint)
    judged = store.graphs["user"]["nodes"]["judged"]
    check("maintenance read leaves no recency stamp", "_last_read_ts" not in judged, judged)
    check("maintenance read does not promote", judged.get("_archived") is True)

    store.read_node("used", level="user", session_id=work)
    used = store.graphs["user"]["nodes"]["used"]
    check("a work session's read still stamps", "_last_read_ts" in used)
    check("a work session's read still promotes", not used.get("_archived"))

    other = sm.register(str(Path.home()), claude_sid="cc-fresh-chore")["session_id"]
    store.set_progress("chore", {"done": 1}, level="user", session_id=other)
    check("a chore's progress write flags its session", other in store._maintenance_sessions)
    store.get_progress("scout", level="user", session_id=work)
    check("other tasks do not flag a session", work not in store._maintenance_sessions)


if __name__ == "__main__":
    test_fresh_ids()
    test_compaction_spares_fresh()
    test_rebalance()
    test_rebalance_and_refill_with_fresh()
    test_explain_fresh()
    test_maintenance_reads()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
