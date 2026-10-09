"""Randomized search for tier churn, driven through the store's REAL tick.

thrash_search.py calls Compactor methods the way the saver called them before
0.13.0 (compact, else refill; NodeScorer(5), so no fresh tier). The server now
runs MultiProjectGraphStore._maybe_compact (mcp_http/store.py), which sequences
  compact -> refill (only if nothing archived) -> rebalance (only if neither)
  -> orphan
with the store's own scorer (fresh tier = FRESH_BUDGET_RATIO of the budget).
This script calls that very method, unbound, on a stub holding only the graph,
the versions and a recording Compactor; _write_through (disk) is the only
thing stubbed. _prune_orphans runs too, as in _periodic_save.

Per random graph it runs TICKS idle ticks (no writes between them, so no score
input changes) and checks:
  C1  churn: a node archived (by compaction or a rebalance swap-out) on tick t,
      and still archived when t ends, is promoted (refill or rebalance) on t+1;
      reported per (demoting pass -> promoting pass);
  F   flip: a node active after tick t-1, archived after t, active after t+1
      (C1 seen as states rather than step labels);
  R   one tick's rebalance swaps a node out and back in;
  C2  no fixpoint: some pass still acts in the last two ticks;
  C2 cycle  a proven perpetual cycle: the full state (archived and orphaned
      sets) after the last tick repeats an earlier one while passes still act.
      An idle tick is a deterministic function of that state (scores rank by
      percentiles, which the clock does not reorder), so it repeats forever,
      with a write-through every tick. "C2 cycle in one tick": the last tick
      acts but ends where it began (rebalance swaps around a loop), so the
      graph is rewritten unchanged; "C2 cycle across ticks": the active set
      itself flips between ticks;
and, right after each pass, against the exact render (see Recording):
  B1  refill ends above int(COMPACTION_TARGET_RATIO * max), the fill ceiling;
  B2  rebalance ends above max;
  B3  refill returns while one more archived node would still fit under the
      ceiling (the claim: refill fills up to the ceiling); "B3 last" when that
      node is the last archived one;
  B4  compaction archives but leaves the graph above max;
  B5  compaction archives right after a refill (refill triggered an archive).

usage: python3 tick_search.py [store|legacy] [v1|v2] [N] [--dump FILE]
  legacy = thrash_search.py's sequence; store = the real tick (default).
  v1 = thrash_search.py's generator; v2 = adds credits, a pre-archived share
       and under-budget starts, so refill and rebalance act first.
  KG_SERVER=<dir> runs against another checkout's knowledge-graph/server.
"""
import json, os, random, sys, logging
from pathlib import Path
from types import SimpleNamespace

SERVER = Path(os.environ.get("KG_SERVER") or
              Path(__file__).resolve().parents[3] / "knowledge-graph" / "server")
sys.path.insert(0, str(SERVER))
logging.disable(logging.WARNING)
from core.compactor import Compactor
from core.scorer import NodeScorer
from core.estimator import CharEstimator
from core.constants import COMPACTION_TARGET_RATIO, FRESH_BUDGET_RATIO
from mcp_http.store import MultiProjectGraphStore as GraphStore

T0 = 1_760_000_000.0  # fixed clock: runs are reproducible
DAY = 86400
TICKS = 10


def graph_v1(rng):
    """thrash_search.py's generator, on a fixed clock."""
    old = T0 - 30 * DAY
    n = rng.randint(4, 14)
    nodes = {}
    for i in range(n):
        nid = f"n{i}"
        nodes[nid] = {"id": nid, "gist": "g" * rng.choice([20, 40, 80, 200, 500]),
                      "notes": ["x" * rng.choice([0, 50, 150])] if rng.random() < .6 else [],
                      "_created_ts": old - rng.randint(0, 20) * DAY,
                      "_last_read_ts": old + rng.randint(0, 25) * DAY}
    edges = {}
    for _ in range(rng.randint(0, 2 * n)):
        a, b = rng.sample(sorted(nodes), 2)
        edges[(a, b, "rel")] = {"from": a, "to": b, "rel": "rel"}
    return nodes, edges


def graph_v2(rng):
    nodes, edges = graph_v1(rng)
    for nid, n in nodes.items():
        if rng.random() < .3:
            n["_useful_ts"] = [T0 - rng.randint(0, 60) * DAY for _ in range(rng.randint(1, 3))]
        if rng.random() < .3:
            n["_archived"] = True
    for _ in range(rng.randint(0, 3)):
        a, b = rng.sample(sorted(nodes), 2)
        rel = rng.choice(["uses", "instance-of", "limitation-of"])
        edges[(a, b, rel)] = {"from": a, "to": b, "rel": rel}
    return nodes, edges


class Recording(Compactor):
    """The real Compactor. Each pass's results are appended to self.log (a tick
    may run a pass more than once), and what each pass promises is checked on
    the spot, against the exact render (estimate_graph):
      B1 refill ends above the fill ceiling;
      B2 rebalance ends above max_chars;
      B3 refill returns while one more archived node would fit under the ceiling;
      B4 compaction archives but leaves the graph above max_chars;
      B5 compaction archives right after a refill, with nothing written between."""
    def __init__(self, *a):
        super().__init__(*a)
        self.log, self.flags, self.last = {}, set(), None

    def _size(self, nodes, edges):
        return self.estimator.estimate_graph(nodes, edges)

    def compact_if_needed(self, nodes, edges, *a, **k):
        r = super().compact_if_needed(nodes, edges, *a, **k)
        if r:
            self.log["archived"] += r
            if self._size(nodes, edges) > self.max_chars:
                self.flags.add("B4")
            if self.last == "refill":
                self.flags.add("B5")
            self.last = "compact"
        return r

    def refill_if_room(self, nodes, edges, *a, **k):
        ceiling = int(self.max_chars * COMPACTION_TARGET_RATIO)
        before = self._size(nodes, edges)
        r = super().refill_if_room(nodes, edges, *a, **k)
        after = self._size(nodes, edges)
        if r:
            self.log["refilled"] += r
            self.last = "refill"
            if after > ceiling:
                self.flags.add("B1")
        if before < ceiling:
            left = [n for n in nodes.values() if n.get("_archived") and "_orphaned_ts" not in n]
            for n in left:
                n["_archived"] = False
                fits = self._size(nodes, edges) <= ceiling
                n["_archived"] = True
                if fits:
                    # the last archived node: promoting it also drops the
                    # ARCHIVED section header, which refill's delta does not credit
                    self.flags.add("B3" if len(left) > 1 else "B3 last")
                    break
        return r

    def rebalance(self, nodes, edges, *a, **k):
        r = super().rebalance(nodes, edges, *a, **k)
        if r:
            self.log["swapped"] += r
            self.last = "rebalance"
            if self._size(nodes, edges) > self.max_chars:
                self.flags.add("B2")
        return r

    def orphan_archived_if_needed(self, *a, **k):
        r = super().orphan_archived_if_needed(*a, **k)
        self.log["orphaned"] += r
        return r


def full_state(nodes):
    return (frozenset(i for i, n in nodes.items() if n.get("_archived")),
            frozenset(i for i, n in nodes.items() if "_orphaned_ts" in n))


def active(nodes):
    return frozenset(i for i, n in nodes.items() if not n.get("_archived") and "_orphaned_ts" not in n)


def run(seed, mode, gen):
    rng = random.Random(seed)
    nodes, edges = (graph_v1 if gen == "v1" else graph_v2)(rng)
    est = CharEstimator()
    total = est.estimate_graph(nodes, edges, include_archived=True)
    lo, hi = (0.6, 1.0) if gen == "v1" else (0.5, 1.3)
    max_chars = max(200, int(total * rng.uniform(lo, hi)))
    ceiling = int(max_chars * COMPACTION_TARGET_RATIO)
    if mode == "legacy":
        comp = Recording(NodeScorer(5), est, max_chars)
    else:
        comp = Recording(NodeScorer(int(max_chars * FRESH_BUDGET_RATIO)), est, max_chars)
    stub = SimpleNamespace(graphs={"g": {"nodes": nodes, "edges": edges}}, _versions={"g": {}},
                           compactor=comp, dirty={}, _write_through=lambda key: None,
                           config=SimpleNamespace(orphan_grace_days=365), _bump_gen=lambda key: None)
    hits, history, states = set(), [], [active(nodes)]
    fulls = [full_state(nodes)]
    for tick in range(TICKS):
        comp.log = {"archived": [], "refilled": [], "swapped": [], "orphaned": []}
        if mode == "legacy":
            a = comp.compact_if_needed(nodes, edges, {}, label="g")
            if not a:
                comp.refill_if_room(nodes, edges, {})
        else:
            GraphStore._maybe_compact(stub, "g")
            GraphStore._prune_orphans(stub, "g")
        lg = dict(comp.log)
        size = est.estimate_graph(nodes, edges)
        history.append((lg, size))
        states.append(active(nodes))
        fulls.append(full_state(nodes))
        if tick:
            prev = history[-2][0]
            for dname, down in (("compact", set(prev["archived"])),
                                ("rebalance", {w for _, w in prev["swapped"]})):
                down -= states[-2]  # net: still archived when the tick ended
                for uname, up in (("refill", set(lg["refilled"])),
                                  ("rebalance", {b for b, _ in lg["swapped"]})):
                    if down & up:
                        hits.add("C1")
                        hits.add(f"C1 {dname}->{uname}")
        if {w for _, w in lg["swapped"]} & {b for b, _ in lg["swapped"]}:
            hits.add("R")
        if len(states) >= 3 and (states[-3] - states[-2]) & states[-1]:
            hits.add("F")
    hits |= comp.flags
    if any(lg["archived"] or lg["refilled"] or lg["swapped"] or lg["orphaned"]
           for lg, _ in history[-2:]):
        hits.add("C2")
        if fulls[-1] in fulls[:-1]:
            hits.add("C2 cycle")
            hits.add("C2 cycle in one tick" if fulls[-1] == fulls[-2] else "C2 cycle across ticks")
    return hits, max_chars, history, states


def show(seed, mode, gen):
    hits, mx, history, states = run(seed, mode, gen)
    print(f"   seed={seed} max_chars={mx} ceiling={int(mx * COMPACTION_TARGET_RATIO)} hits={sorted(hits)}")
    for t, (lg, size) in enumerate(history):
        acts = {k: v for k, v in lg.items() if v}
        if acts:
            print(f"      tick {t}: {acts} -> {size} chars")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    dump = None
    if "--dump" in args:
        i = args.index("--dump"); dump = args[i + 1]; del args[i:i + 2]
    mode = args[0] if args else "store"
    gen = args[1] if len(args) > 1 else "v1"
    N = int(args[2]) if len(args) > 2 else 20000
    counts, first, finals = {}, {}, {}
    for seed in range(N):
        hits, _, _, states = run(seed, mode, gen)
        finals[seed] = sorted(states[-1])
        for h in hits:
            counts[h] = counts.get(h, 0) + 1
            first.setdefault(h, seed)
    print(f"{N} random graphs ({gen}), {TICKS} idle ticks each, through the "
          f"{'store tick GraphStore._maybe_compact' if mode == 'store' else 'legacy compact-else-refill sequence'}")
    for h in ["C1", "C1 compact->refill", "C1 compact->rebalance", "C1 rebalance->refill",
              "C1 rebalance->rebalance", "F", "R", "C2", "C2 cycle",
              "C2 cycle in one tick", "C2 cycle across ticks", "B1", "B2", "B3", "B3 last", "B4", "B5"]:
        print(f"{h}: {counts.get(h, 0)} graphs")
    for h in ["C1 compact->refill", "C1 compact->rebalance", "C1 rebalance->rebalance", "R", "C2",
              "C2 cycle in one tick", "C2 cycle across ticks", "B1", "B2", "B3", "B4", "B5"]:
        if h in first:
            print(f"first {h}:")
            show(first[h], mode, gen)
    if dump:
        Path(dump).write_text(json.dumps(finals))
