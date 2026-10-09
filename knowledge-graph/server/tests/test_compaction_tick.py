#!/usr/bin/env python3
"""Self-contained tests for the saver's per-graph tick: compaction, refill,
rebalance and the orphan pass in the order _maybe_compact runs them.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_compaction_tick.py

SAME-TICK REFILL (formal F7). Compaction archives whole nodes until the graph
is under the fill ceiling, so the last one can land it well below. Refill used
to be skipped on that tick, so the gap was filled on the next one, often with a
node compaction had just archived and written: it flickered for a tick. Refill
now runs on the compacting tick, which ends where the next tick used to.

WHOLE ARCHIVE (formal F34). The ARCHIVED header and the anchors go when the last
archived node comes back, which no per-node delta credits. A small graph could
sit over the fill ceiling only because of them, or set its last archived node
aside as too big, with one node archived for good although every node fits.

The graphs are the formal model's counterexamples (formal/compaction/). Each
test ticks a real store the way _periodic_save does. KG_STORAGE_ROOT points at
a temporary directory, so no real graph is touched.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-test-tick-")

import logging; logging.disable(logging.WARNING)
from core.compactor import Compactor
from core.constants import COMPACTION_TARGET_RATIO, FRESH_BUDGET_RATIO, user_graph_path
from core.estimator import CharEstimator
from core.persistence import GraphPersistence
from core.scorer import NodeScorer
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore

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


DAY = 86400
NOW = time.time()


def graph(gists, edges, read, archived=()):
    nodes = {}
    for i, g in enumerate(gists):
        n = {"id": f"n{i}", "gist": "g" * g, "notes": [],
             "_created_ts": NOW - 60 * DAY + i * DAY,
             "_last_read_ts": NOW - 30 * DAY + read[i] * DAY}
        if i in archived:
            n["_archived"] = True
        nodes[f"n{i}"] = n
    es = {(f"n{a}", f"n{b}", "rel"): {"from": f"n{a}", "to": f"n{b}", "rel": "rel"} for a, b in edges}
    return nodes, es


def boot(max_chars, nodes, edges):
    """A real store loading this graph as its user graph; counts its saves."""
    root = Path(os.environ["KG_STORAGE_ROOT"])
    for p in root.iterdir():
        if p.is_file():
            p.unlink()
    GraphPersistence(user_graph_path()).save({"nodes": nodes, "edges": edges}, {})
    store = MultiProjectGraphStore(GraphConfig(max_chars=max_chars, save_interval=9999),
                                   HTTPSessionManager(), broadcast_callback=None)
    saves = [0]
    pers = store._persistence["user"]
    orig = pers.save
    def counted(*a, **k):
        saves[0] += 1
        return orig(*a, **k)
    pers.save = counted
    return store, saves


def tick(store):
    """_periodic_save's loop body for the user graph (store.py)."""
    with store.lock:
        store._maybe_compact("user")
        store._prune_orphans("user")
        if store.dirty.get("user", False) and store._save_to_disk("user"):
            store.dirty["user"] = False


def archived(store):
    return sorted(i for i, n in store.graphs["user"]["nodes"].items()
                  if n.get("_archived") and "_orphaned_ts" not in n)


def test_same_tick_refill():
    print("same-tick refill (F7):")
    # n0 is small and read earlier, n1 large; both must go to get under the
    # ceiling, which leaves room for n0 again.
    nodes, edges = graph([20, 200], [(0, 1), (1, 0)], read=[1, 2])
    store, saves = boot(163, nodes, edges)
    tick(store)
    after_first = archived(store)
    check("the compacting tick refills its own overshoot", after_first == ["n1"], after_first)
    n = saves[0]
    tick(store)
    check("the next tick has nothing to do", archived(store) == ["n1"] and saves[0] == n,
          (archived(store), saves[0] - n))
    size = store.estimator.estimate_graph(store.graphs["user"]["nodes"], store.graphs["user"]["edges"])
    check("the graph ends under the fill ceiling", size <= int(163 * COMPACTION_TARGET_RATIO), size)
    store.shutdown()


def test_write_over_budget():
    print("a write that crosses the budget (F7):")
    nodes, edges = graph([20], [], read=[1])
    store, _ = boot(163, nodes, edges)
    store.put_node(level="user", node_id="n1", gist="g" * 200)
    store.put_edge(level="user", from_ref="n0", to_ref="n1", rel="rel")
    store.put_edge(level="user", from_ref="n1", to_ref="n0", rel="rel")
    arch = archived(store)
    size = store.estimator.estimate_graph(store.graphs["user"]["nodes"], store.graphs["user"]["edges"])
    check("the write's own compaction leaves the graph within budget", size <= 163, size)
    before = arch
    tick(store)
    check("the saver tick after it changes nothing", archived(store) == before, (before, archived(store)))
    store.shutdown()


def test_no_thrash_with_refill():
    print("compaction and refill never undo each other:")
    import random
    rng = random.Random(7)
    est = CharEstimator()
    bad = []
    for seed in range(300):
        rng.seed(seed)
        k = rng.randint(3, 9)
        gists = [rng.choice([20, 40, 80, 200, 500]) for _ in range(k)]
        pairs = [(a, b) for a in range(k) for b in range(k) if a != b]
        es = [p for p in pairs if rng.random() < .25]
        nodes, edges = graph(gists, es, read=[rng.randint(0, 20) for _ in range(k)])
        total = est.estimate_graph(nodes, edges)
        mx = max(150, int(total * rng.uniform(.5, 1.0)))
        comp = Compactor(NodeScorer(int(mx * FRESH_BUDGET_RATIO)), est, mx)
        a = comp.compact_if_needed(nodes, edges, {})
        r = comp.refill_if_room(nodes, edges, {})
        size = est.estimate_graph(nodes, edges)
        again = comp.compact_if_needed(nodes, edges, {})
        if size > mx or again or comp.refill_if_room(nodes, edges, {}):
            bad.append(seed)
    check("300 random graphs: compact then refill lands within budget, and a second "
          "pass of either does nothing", not bad, bad[:5])


def test_whole_archive():
    print("the whole archive comes back when it fits (F34):")
    # Over the ceiling only because of the ARCHIVED header: refill never started.
    nodes, edges = graph([20, 20], [], read=[1, 2], archived={1})
    est = CharEstimator()
    full = est.estimate_graph({k: {**v, "_archived": False} for k, v in nodes.items()}, edges)
    store, _ = boot(100, nodes, edges)
    tick(store)
    check(f"one archived of two, all-active size {full} under the ceiling 80: promoted",
          archived(store) == [], archived(store))
    store.shutdown()
    # Under the ceiling, but the last node's delta ignored the header it removes.
    nodes, edges = graph([20, 20], [], read=[1, 2], archived={1})
    comp = Compactor(NodeScorer(0), est, 118)
    before = est.estimate_graph(nodes, edges)
    got = comp.refill_if_room(nodes, edges, {})
    check(f"at {before} chars under the ceiling 94, the last archived node is promoted",
          got == ["n1"], got)
    # Still nothing promoted when everything together does not fit.
    nodes, edges = graph([20, 200], [], read=[1, 2], archived={1})
    comp = Compactor(NodeScorer(0), est, 118)
    check("a whole archive that does not fit stays archived",
          comp.refill_if_room(nodes, edges, {}) == [] and nodes["n1"].get("_archived"))


if __name__ == "__main__":
    test_same_tick_refill()
    test_write_over_budget()
    test_no_thrash_with_refill()
    test_whole_archive()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
