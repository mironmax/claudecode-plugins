"""Reproduce the counterexamples of compaction/lean/Compaction.lean against a real
store's maintenance tick.

Real: MultiProjectGraphStore loading user.json from disk, its own scorer and
Compactor (max_chars from GraphConfig), and per tick exactly what
_periodic_save does for a graph under store.lock: _maybe_compact, then
_prune_orphans, then a save if still dirty. Write-throughs go to disk; the
script counts them and reads the archived set back from user.json.
Stubbed: nothing; only the saver's 30-second wait is replaced by calling the
tick directly. Each graph is the model's, node for node, and every tick's size
is checked against the model's closed form for estimate_graph.

Each case prints BUG lines while its finding is unfixed, ok lines otherwise;
the last line is RESULT: FAIL <cases> or all hold. KG_SERVER=<dir> runs it
against another checkout's knowledge-graph/server.
"""
import json, os, sys, tempfile, time
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()

import atexit, shutil
atexit.register(shutil.rmtree, os.environ["KG_STORAGE_ROOT"], True)
sys.path.insert(0, str(Path(os.environ.get("KG_SERVER") or
                        Path(__file__).resolve().parents[3] / "knowledge-graph" / "server")))
import logging; logging.disable(logging.WARNING)
from core.constants import user_graph_path
from core.persistence import GraphPersistence
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore

DAY = 86400
T0 = time.time()
bugs = []
def bug(case, msg): bugs.append(case); print(f"BUG {case}: {msg}")
def ok(case, msg): print(f"ok  {case}: {msg}")


def model_size(nodes, edges):
    """The model's closed form (Compaction.lean `size`) for ids n0..n9, rel "rel"."""
    act = [i for i, n in nodes.items() if not n.get("_archived") and "_orphaned_ts" not in n]
    arc = [i for i, n in nodes.items() if n.get("_archived") and "_orphaned_ts" not in n]
    orph = {i for i, n in nodes.items() if "_orphaned_ts" in n}
    live = [e for e in edges.values() if e["from"] not in orph and e["to"] not in orph
            and (e["from"] in act or e["to"] in act)]
    return ((8 if act else 0) + sum(len(nodes[i]["gist"]) + 7 for i in act)
            + (53 if arc else 0) + 5 * len(arc) + 15 * len(live))


def boot(max_chars, gists, edges, read, created, endorsed=(), archived=()):
    """Write the model's graph to user.json, then start a real store on it."""
    root = Path(os.environ["KG_STORAGE_ROOT"])
    for p in root.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    nodes = {}
    for i, g in enumerate(gists):
        n = {"id": f"n{i}", "gist": "g" * g, "notes": [],
             "_created_ts": T0 - 60 * DAY + created[i] * DAY,
             "_last_read_ts": T0 - 30 * DAY + read[i] * DAY}
        if i in endorsed:
            n["_useful_ts"] = [T0 - 10 * DAY]
        if i in archived:
            n["_archived"] = True
        nodes[f"n{i}"] = n
    es = {(f"n{a}", f"n{b}", "rel"): {"from": f"n{a}", "to": f"n{b}", "rel": "rel"} for a, b in edges}
    GraphPersistence(user_graph_path()).save({"nodes": nodes, "edges": es}, {})
    store = MultiProjectGraphStore(GraphConfig(max_chars=max_chars, save_interval=9999),
                                   HTTPSessionManager(), None)
    # record what each pass returns, and count the saves
    log = {}
    comp = store.compactor
    for name in ("compact_if_needed", "refill_if_room", "rebalance", "orphan_archived_if_needed"):
        orig = getattr(comp, name)
        def rec(*a, _orig=orig, _name=name, **k):
            r = _orig(*a, **k)
            if r:
                log.setdefault(_name, []).extend(r)
            return r
        setattr(comp, name, rec)
    saves = [0]
    pers = store._persistence["user"]
    orig_save = pers.save
    def counted(*a, **k):
        saves[0] += 1
        return orig_save(*a, **k)
    pers.save = counted
    return store, log, saves


def tick(store):
    """One pass of _periodic_save's loop body (store.py:2059-2068) for the user graph."""
    with store.lock:
        store._maybe_compact("user")
        store._prune_orphans("user")
        if store.dirty.get("user", False):
            if store._save_to_disk("user"):
                store.dirty["user"] = False


def on_disk():
    data = json.loads(user_graph_path().read_text())
    return sorted(i for i, n in data["nodes"].items() if n.get("_archived") and "_orphaned_ts" not in n)


def run(case, store, log, saves, ticks):
    """Tick; return [(passes, archived in memory, archived on disk, size)] per tick."""
    g = store.graphs["user"]
    out, sizes_ok = [], True
    for t in range(ticks):
        log.clear()
        before = saves[0]
        tick(store)
        arch = sorted(i for i, n in g["nodes"].items() if n.get("_archived") and "_orphaned_ts" not in n)
        size = store.estimator.estimate_graph(g["nodes"], g["edges"])
        sizes_ok &= size == model_size(g["nodes"], g["edges"])
        acts = {k.split("_")[0]: v for k, v in log.items()}
        out.append((acts, arch, on_disk() if saves[0] > before else None, size))
        print(f"     tick {t}: {acts or 'nothing acts'} -> archived {arch}, {size} chars"
              + (f", written (disk: archived {out[-1][2]})" if saves[0] > before else ""))
    (ok if sizes_ok else bug)(case, "every tick's estimate_graph equals the model's closed form"
                             if sizes_ok else "estimate_graph differs from the model's closed form")
    return out


print("== X1 (F7): a node archived on one tick is promoted on the next")
print("   model: n0 (gist 20), n1 (gist 200), n0<->n1, max 163; n1 read later")
store, log, saves = boot(163, [20, 200], [(0, 1), (1, 0)], read=[1, 2], created=[1, 2])
tr = run("X1", store, log, saves, 3)
churn = [(t, i) for t in range(len(tr) - 1) for i in tr[t][1]
         if tr[t][2] is not None and i in tr[t][2] and i not in tr[t + 1][1]]
if churn:
    t, i = churn[0]
    bug("X1", f"{i} was archived and written on tick {t}, then promoted on tick {t + 1}")
else:
    ok("X1", "no node is written archived on one tick and promoted on the next")
store.shutdown()

print("== X2 (F33): rebalance swaps two linked nodes back and forth, forever")
print("   model: n0, n1 (gist 80 each), n0<->n1, max 190; n1 read later")
store, log, saves = boot(190, [80, 80], [(0, 1), (1, 0)], read=[1, 2], created=[1, 2])
s0 = saves[0]
tr = run("X2", store, log, saves, 6)
states = [tuple(a) for _, a, _, _ in tr]
if tr[-1][0] and states[-1] in states[:-1]:
    flips = sum(1 for t in range(1, len(states)) if states[t] != states[t - 1])
    bug("X2", f"the state after tick 5 repeats an earlier one and passes still act: a cycle; "
              f"{saves[0] - s0} saves in 6 ticks, the archived set changed on {flips} of 5 tick boundaries")
else:
    ok("X2", "the tick reaches a fixpoint")
store.shutdown()

def stuck(case, store, tr):
    """BUG when the graph settles with a node archived though all of it fits."""
    g = store.graphs["user"]
    left = tr[-1][1]
    if not left:
        return ok(case, "refill promotes the last archived node when it fits")
    for i in left:
        g["nodes"][i]["_archived"] = False
    full = store.estimator.estimate_graph(g["nodes"], g["edges"])
    for i in left:
        g["nodes"][i]["_archived"] = True
    ceiling = int(store.config.max_chars * 0.8)
    if full <= ceiling:
        bug(case, f"{left} stays archived; with it active the graph is {full} chars, under the fill ceiling {ceiling}")
    else:
        ok(case, f"{left} stays archived and would not fit ({full} > {ceiling})")


print("== X3 (F34): a node stays archived though the whole graph fits under the ceiling")
print("   model: n0, n1 (gist 20 each), n0<->n1, max 119, n0 archived; n1 read later")
store, log, saves = boot(119, [20, 20], [(0, 1), (1, 0)], read=[1, 2], created=[1, 2], archived={0})
stuck("X3", store, run("X3", store, log, saves, 3))
store.shutdown()
print("   the same outside the model's budget grid: no edges, max 100, n1 archived;")
print("   the graph is over the fill ceiling only through the ARCHIVED header, so refill never starts")
store, log, saves = boot(100, [20, 20], [], read=[1, 2], created=[1, 2], archived={1})
stuck("X3", store, run("X3", store, log, saves, 2))
store.shutdown()

print("RESULT: " + (f"FAIL {', '.join(sorted(set(bugs)))}" if bugs else "all hold"))
