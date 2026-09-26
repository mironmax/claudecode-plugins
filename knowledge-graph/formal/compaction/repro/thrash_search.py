"""Randomized search over the REAL compactor for tick-to-tick churn.

Simulates the saver's per-tick sequence from store._maybe_compact:
  archived = compact_if_needed(); refilled = [] if archived else refill_if_room()
with no writes between ticks. Claim under test (compactor.py refill docstring):
"No-thrash is guaranteed by the ceiling sitting below the archive threshold and
by the store skipping refill on any tick that just archived."
Checks: (C1) a node archived on tick t is re-promoted on tick t+1 (churn);
(C2) the graph reaches a fixpoint within 5 ticks.
"""
import random, sys, time, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "server"))
logging.disable(logging.WARNING)
from core.compactor import Compactor
from core.scorer import NodeScorer
from core.estimator import CharEstimator

def graph(rng):
    old = time.time() - 30 * 86400
    n = rng.randint(4, 14)
    nodes = {}
    for i in range(n):
        nid = f"n{i}"
        nodes[nid] = {"id": nid, "gist": "g" * rng.choice([20, 40, 80, 200, 500]),
                      "notes": ["x" * rng.choice([0, 50, 150])] if rng.random() < .6 else [],
                      "_created_ts": old - rng.randint(0, 20) * 86400,
                      "_last_read_ts": old + rng.randint(0, 25) * 86400}
    edges = {}
    for _ in range(rng.randint(0, 2 * n)):
        a, b = rng.sample(sorted(nodes), 2)
        edges[(a, b, "rel")] = {"from": a, "to": b, "rel": "rel"}
    return nodes, edges

def run(seed):
    rng = random.Random(seed)
    nodes, edges = graph(rng)
    est = CharEstimator()
    total = est.estimate_graph(nodes, edges, include_archived=False)
    max_chars = max(200, int(total * rng.uniform(0.6, 1.0)))
    comp = Compactor(NodeScorer(5), est, max_chars)
    prev_archived, history = [], []
    for tick in range(6):
        archived = comp.compact_if_needed(nodes, edges, {}, label="g")
        refilled = [] if archived else comp.refill_if_room(nodes, edges, {})
        history.append((archived, refilled))
        churn = set(prev_archived) & set(refilled)
        if churn:
            return ("C1", seed, tick, sorted(churn), max_chars, history)
        prev_archived = archived
    if any(a or r for a, r in history[-2:]):
        return ("C2", seed, None, None, max_chars, history)
    return None

hits = {"C1": [], "C2": []}
N = 20000
for seed in range(N):
    r = run(seed)
    if r:
        hits[r[0]].append(r)
print(f"{N} random graphs through the real Compactor")
for k, v in hits.items():
    print(f"{k}: {len(v)} graphs")
    for kind, seed, tick, churn, mx, hist in v[:2]:
        print(f"   seed={seed} max_chars={mx} tick={tick} churned={churn}")
        for t, (a, r) in enumerate(hist):
            print(f"      tick {t}: archived={a} refilled={r}")
