#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.12 changes.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0912.py

Covers the four runtime change-areas verified during development:
  1. Gist-corruption healer (heal_node_fields / gist_is_malformed)
  2. Live-string edge accounting (edge_is_live, estimator render==charge)
  3. Iterative refill + archived-edge connectedness weight
  4. Node promotion (recall) flag handling

Exits non-zero if any assertion fails. Uses only in-memory fixtures — never
touches real graph files under ~/.knowledge-graph.
"""

import os
import sys
import time

# Make `core` importable when run from the tests/ dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.healer import heal_node_fields, gist_is_malformed
from core.utils import edge_is_live, is_active, active_node_ids
from core.estimator import TokenEstimator
from core.scorer import NodeScorer
from core.compactor import Compactor
from core.constants import (
    ARCHIVED_ID_TOKENS,
    ARCHIVED_EDGE_WEIGHT,
    REFILL_TRIGGER_RATIO,
    COMPACTION_TARGET_RATIO,
    GRACE_PERIOD_DAYS,
)

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


def past_grace_ts():
    return time.time() - (GRACE_PERIOD_DAYS + 1) * 24 * 3600


# --- 1. healer --------------------------------------------------------------
def test_healer():
    print("healer:")

    # clean gist passes through untouched
    g, n, t = heal_node_fields("A clean headline.", ["note"], ["f.py"])
    check("clean passthrough", g == "A clean headline." and n == ["note"] and t == ["f.py"])
    check("clean not flagged malformed", not gist_is_malformed("A clean headline."))

    # FALSE-POSITIVE GUARD: a gist that merely *mentions* the markup must NOT be
    # flagged — otherwise heal-on-write/load silently truncates legitimate content.
    legit_mentions = [
        'The healer strips <invoke and <parameter tags from gists.',
        'Use the <parameter name=...> syntax in tool calls.',
        'Notes about <notes and how the schema works.',
        'Compare a < b and c > d in this expression.',
        'gist_is_malformed matches </gist> only when structurally leaked.',  # describes but not leaked-shaped... see below
    ]
    for s in legit_mentions[:4]:  # first 4 are pure prose mentions
        check(f"no false positive: {s[:40]!r}", not gist_is_malformed(s), s)
    # idempotent no-op on a legit mention
    g, n, t = heal_node_fields(legit_mentions[1], ["real note"], None)
    check("legit-mention gist not truncated", g == legit_mentions[1] and n == ["real note"])

    # classic corruption: gist swallowed notes + parameter markup
    bad = 'Real gist.</gist>\n<parameter name="notes">["first", "second"]\n<parameter name="touches">["a.py"]'
    g, n, t = heal_node_fields(bad, None, None)
    check("corrupt gist split at marker", g == "Real gist.", repr(g))
    check("corrupt notes recovered", n == ["first", "second"], repr(n))
    check("corrupt touches recovered", t == ["a.py"], repr(t))

    # idempotency: healing healed output is a no-op
    g2, n2, t2 = heal_node_fields(g, n, t)
    check("idempotent", (g2, n2, t2) == (g, n, t))

    # never overwrite caller-supplied notes with embedded ones
    g, n, t = heal_node_fields(bad, ["CALLER"], None)
    check("caller notes not overwritten", n == ["CALLER"], repr(n))

    # trailing tool-call junk after notes is discarded
    bad2 = 'G.</gist>\n<notes>["x"]</notes>\n</invoke>\n<invoke name="kg_put_edge"><parameter name="session_id">abc'
    g, n, t = heal_node_fields(bad2, None, None)
    check("trailing tool-call junk discarded", g == "G." and n == ["x"], (repr(g), repr(n)))

    # nested brackets/quotes inside a note string parse correctly (balanced scan)
    bad3 = 'H.</gist>\n<notes>["arr [1, 2] and a \\"quote\\" inside", "second"]'
    g, n, t = heal_node_fields(bad3, None, None)
    check("nested brackets/quotes in notes", n == ['arr [1, 2] and a "quote" inside', "second"], repr(n))

    # bare </invoke> tail, no notes block -> clean gist, no notes
    g, n, t = heal_node_fields("Long real gist.</invoke>", None, None)
    check("bare invoke tail, no notes", g == "Long real gist." and not n, (repr(g), repr(n)))


# --- 2. live-string edges ---------------------------------------------------
def test_edges():
    print("edges:")
    nodes = {
        "A": {"id": "A", "gist": "a"},                       # active
        "B": {"id": "B", "gist": "b", "_archived": True},    # archived
        "C": {"id": "C", "gist": "c", "_archived": True},    # archived
        "O": {"id": "O", "gist": "o", "_archived": True, "_orphaned_ts": 1.0},  # orphaned
    }
    active = active_node_ids(nodes)
    check("active set excludes archived+orphaned", active == {"A"}, active)

    def e(f, t):
        return {"from": f, "to": t, "rel": "r"}

    check("active-archived edge is live", edge_is_live(e("A", "B"), nodes, active))
    check("archived-archived edge NOT live", not edge_is_live(e("B", "C"), nodes, active))
    check("edge touching orphaned NOT live", not edge_is_live(e("A", "O"), nodes, active))
    # non-node (artifact/file) endpoint is always present -> live
    check("artifact-endpoint edge is live", edge_is_live(e("A", "some/file.py"), nodes, active))
    check("archived-to-artifact edge is live", edge_is_live(e("B", "some/file.py"), nodes, active))

    # estimator: render == charge. active=id+gist, archived=anchor, orphaned=0,
    # only live edges charged.
    est = TokenEstimator()
    edges = {
        "A->B:r": e("A", "B"),   # live (charged)
        "B->C:r": e("B", "C"),   # dead (not charged)
        "A->O:r": e("A", "O"),   # dead (orphaned, not charged)
    }
    from core.constants import BASE_NODE_TOKENS, CHARS_PER_TOKEN, TOKENS_PER_EDGE
    expected = (
        BASE_NODE_TOKENS + len("a") // CHARS_PER_TOKEN  # A active
        + ARCHIVED_ID_TOKENS  # B anchor
        + ARCHIVED_ID_TOKENS  # C anchor
        + 0                   # O orphaned, free
        + TOKENS_PER_EDGE     # only A->B live
    )
    got = est.estimate_graph(nodes, edges, include_archived=False)
    check("estimator charges exactly the rendered set", got == expected, f"got {got} expected {expected}")


# --- 3. refill + archived-edge weight ---------------------------------------
def _cluster_graph():
    """1 active anchor + an archived cluster (hub + 4 satellites) interlinked,
    hub weakly tied to the anchor. The cluster should refill as a unit."""
    old = past_grace_ts()
    nodes = {"anchor": {"id": "anchor", "gist": "x" * 40, "_created_ts": old}}
    for i in range(5):
        role = "hub" if i == 0 else f"sat{i}"
        nodes[role] = {"id": role, "gist": "g" * 60, "_created_ts": old, "_archived": True}
    edges = {}

    def e(a, b):
        edges[f"{a}->{b}:r"] = {"from": a, "to": b, "rel": "r"}

    e("anchor", "hub")
    for i in range(1, 5):
        e("hub", f"sat{i}")
        e(f"sat{i}", "hub")
    e("sat1", "sat2")
    e("sat2", "sat3")
    e("sat3", "sat4")
    return nodes, edges


def test_refill():
    print("refill:")
    check("archived-edge weight between 0 and 1", 0 < ARCHIVED_EDGE_WEIGHT < 1, ARCHIVED_EDGE_WEIGHT)
    check("hysteresis band ordered", REFILL_TRIGGER_RATIO < COMPACTION_TARGET_RATIO < 1.0)

    # connectedness now counts archived neighbours at reduced weight (not zero)
    sc = NodeScorer(GRACE_PERIOD_DAYS)
    nodes, edges = _cluster_graph()
    active_ids = {"anchor"}
    archived_ids = {"hub", "sat1", "sat2", "sat3", "sat4"}
    adj = sc._build_adjacency(edges)
    hub_conn = sc._connectedness("hub", active_ids, archived_ids, adj)
    iso = {"id": "iso", "gist": "z", "_archived": True}
    nodes["iso"] = iso  # archived, zero edges
    iso_conn = sc._connectedness("iso", active_ids, archived_ids | {"iso"}, adj)
    check("dense archived hub scores > isolated archived node", hub_conn > iso_conn, f"hub={hub_conn} iso={iso_conn}")
    del nodes["iso"]

    # iterative refill pulls the cluster back as a unit (hub + all satellites)
    nodes, edges = _cluster_graph()
    est = TokenEstimator()
    comp = Compactor(sc, est, max_tokens=5000)
    promoted = comp.refill_if_room(nodes, edges, {})
    cluster = {"hub", "sat1", "sat2", "sat3", "sat4"}
    check("cluster follows into active set", cluster.issubset(set(promoted)), promoted)

    # respects fill ceiling and does not over-promote / thrash
    ceiling = int(5000 * COMPACTION_TARGET_RATIO)
    after_tok = est.estimate_graph(nodes, edges, include_archived=False)
    check("stays under fill ceiling", after_tok <= ceiling, f"{after_tok} > {ceiling}")
    second = comp.refill_if_room(nodes, edges, {})
    check("second refill is no-op (no thrash)", second == [], second)

    # does not fire when already over the trigger (graph near/over budget)
    big_nodes = {f"n{i}": {"id": f"n{i}", "gist": "q" * 400, "_created_ts": past_grace_ts()} for i in range(40)}
    fired = comp.refill_if_room(big_nodes, {}, {})
    check("no refill when over trigger", fired == [], fired)

    # PERF GUARD: the adjacency index keeps refill cheap even on a large dense graph
    # (this exact shape effectively hung before the index existed). Generous bound —
    # we only care that it's seconds-fast, not the exact figure.
    import random as _r
    pnodes, pedges = {}, {}
    old = past_grace_ts()
    for i in range(400):
        pnodes[f"p{i}"] = {"id": f"p{i}", "gist": "g" * 80, "_archived": True, "_created_ts": old}
    _r.seed(2)
    eid = 0
    for i in range(400):
        for _ in range(50):
            j = _r.randint(0, 399)
            if j != i:
                pedges[f"pe{eid}"] = {"from": f"p{i}", "to": f"p{j}", "rel": "r"}
                eid += 1
    t0 = time.time()
    Compactor(sc, est, max_tokens=5000).refill_if_room(pnodes, pedges, {})
    elapsed = time.time() - t0
    check(f"refill fast on 400-node/{len(pedges)}-edge dense graph ({elapsed:.2f}s)", elapsed < 5.0, f"{elapsed:.2f}s")


# --- 4. promotion (recall) flag handling ------------------------------------
def test_promotion_flags():
    print("promotion:")
    # The recall path pops both flags; popping a missing flag must not raise.
    node = {"id": "x", "_archived": True, "_orphaned_ts": 5.0}
    node.pop("_archived", None)
    node.pop("_orphaned_ts", None)
    check("archived+orphaned -> active after pop", is_active(node))

    # orphaned-without-archived (defensive): pop must be a safe no-op, not KeyError
    node2 = {"id": "y", "_orphaned_ts": 5.0}
    try:
        node2.pop("_archived", None)
        node2.pop("_orphaned_ts", None)
        ok = is_active(node2)
    except KeyError:
        ok = False
    check("orphaned-without-archived pop is safe", ok)


def main():
    print("=== v0.9.12 regression tests ===")
    test_healer()
    test_edges()
    test_refill()
    test_promotion_flags()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
