"""Reproduce the usefulness-accounting counterexamples of credits/lean/Credits.lean
against the real store, and re-check the properties the model found to hold.

Real: MultiProjectGraphStore.mark_useful, put_node (note credit), put_edge and
graph loads (recurrence reconciliation), read_node, rename_node, mark_maintenance,
shutdown, HTTPSessionManager persistence (sessions.json) and the scorer's recency.
Stubbed: nothing. A crash is a new store built without shutdown(); a restart is
shutdown() followed by a new store; both on the same KG_STORAGE_ROOT.

Each case prints BUG lines while its finding is unfixed, ok lines otherwise;
the last line is RESULT: FAIL <cases> or all hold.
"""
import json, os, sys, tempfile, threading, time
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()

import atexit, shutil
atexit.register(shutil.rmtree, os.environ["KG_STORAGE_ROOT"], True)
def _home_tmpdir():
    """Project roots must live under $HOME (safe_project_path); removed at exit."""
    d = tempfile.mkdtemp(dir=Path.home(), prefix="kg-formal-")
    atexit.register(shutil.rmtree, d, True)
    return d
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
import logging; logging.disable(logging.WARNING)
from core.constants import USEFUL_LOG_NAME, get_storage_root, project_graph_path, user_graph_path
from core.persistence import GraphPersistence
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore

bugs = []
def bug(case, msg): bugs.append(case); print(f"BUG {case}: {msg}")
def ok(case, msg): print(f"ok  {case}: {msg}")

def fresh_storage():
    root = Path(os.environ["KG_STORAGE_ROOT"])
    for p in root.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()

def boot():
    sm = HTTPSessionManager()
    return MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None), sm

def crash(store):
    """Stop a store the way a kill does: no final save of graphs or sessions."""
    store.running = False
    store._stop_event.set()
    store.saver_thread.join(timeout=5)

def recency(store, gk, nid):
    return store.scorer._recency(nid, store.graphs[gk]["nodes"][nid], store._versions[gk], time.time())

def log():
    p = get_storage_root() / USEFUL_LOG_NAME
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

P = _home_tmpdir()

print("== X1: a rename lets a session vote twice for one node")
fresh_storage(); store, sm = boot()
A = sm.register(P)["session_id"]; M = sm.register(P)["session_id"]
store.mark_maintenance(M)
store.put_node("user", "lesson-a", "a lesson", session_id=A)
store.mark_useful(["lesson-a"], A)                       # A's one vote
store.mark_useful(["lesson-a"], M, credits=3)            # the pass credits it, 3 by conviction
store.rename_node("lesson-a", "lesson-b", session_id=M)  # the same pass refines the id
ra = store.mark_useful(["lesson-b"], A)
rm = store.mark_useful(["lesson-b"], M, credits=3)
stamps = len(store.graphs["user"]["nodes"]["lesson-b"]["_useful_ts"])
print(f"     after the rename: A's second vote accepted={bool(ra['accepted'])}, "
      f"M's second credit accepted={bool(rm['accepted'])}, node stamps={stamps}")
if ra["accepted"]:
    bug("X1", "session A endorsed the renamed node a second time")
else:
    ok("X1", "a session's vote follows the node through a rename")
if rm["accepted"]:
    bug("X1", "one maintenance pass credited one node 3+3 times (cap: 3 per node)")
else:
    ok("X1", "a pass's credit follows the node through a rename")
store.shutdown()

print("== X2: a maintenance rename resets the node's activity time")
fresh_storage(); store, sm = boot()
A = sm.register(P)["session_id"]; M = sm.register(P)["session_id"]
store.mark_maintenance(M)
store.put_node("user", "old-name", "a lesson written by work", session_id=A)
before = recency(store, "user", "old-name")
time.sleep(0.01)
store.put_node("user", "old-name", "reworded by the pass", session_id=M)
mid = recency(store, "user", "old-name")
store.rename_node("old-name", "new-name", session_id=M)
after = recency(store, "user", "new-name")
show = lambda r: "the write time" if r == before else ("0 (the epoch)" if r == 0 else "another time")
print(f"     recency after the pass's rewrite: {show(mid)}; after the pass's rename: {show(after)}")
if mid == before:
    ok("X2", "a maintenance rewrite keeps the node's previous activity time")
else:
    bug("X2", "a maintenance rewrite moved recency")
if after != before:
    bug("X2", f"a maintenance rename moved recency from the write time to {show(after)} "
              f"(version used_ts={store._versions['user']['node:new-name'].get('used_ts')})")
else:
    ok("X2", "a maintenance rename keeps the node's previous activity time")
store.shutdown()

print("== X3: a server restart forgets that a session is maintenance")
for how in ("restart", "crash"):
    fresh_storage(); store, sm = boot()
    M = sm.register(P)["session_id"]
    store.mark_maintenance(M)                     # kg_read(maintenance=true), before its first read
    store.put_node("user", "judged", "a lesson the pass will judge")
    store.graphs["user"]["nodes"]["judged"]["_archived"] = True
    store.dirty["user"] = True
    store._write_through("user")
    if how == "restart":
        store.shutdown()
    else:
        sm.save_sessions()                        # sessions.json as the 30 s saver left it
        crash(store)
    store, sm = boot()
    print(f"     after a {how}: session restored={sm.lookup(M) is not None}, "
          f"maintenance={store.is_maintenance(M)}")
    r = store.read_node("judged", level="user", session_id=M)
    node = store.graphs["user"]["nodes"]["judged"]
    found = []
    if r["promoted"] or not node.get("_archived"):
        found.append("its read promoted an archived node")
    if node.get("_last_read_ts"):
        found.append("its read stamped recency")
    c = store.mark_useful(["judged"], M, credits=2)
    if c["rejected"]:
        found.append("credits=2 refused as 'credits are for a maintenance pass'")
    c = store.mark_useful(["judged"], M, credits=1)
    via = log()[-1].get("via")
    if c["accepted"] and via != "maintenance":
        found.append(f"credits=1 became an endorsement logged via {via!r} (counted as use)")
    if found:
        bug("X3", f"{how}: " + "; ".join(found))
    else:
        ok("X3", f"{how}: the session stays maintenance")
    store.shutdown()

print("== X4: a maintenance tidy turns the author's next case into a note credit")
fresh_storage(); store, sm = boot()
A = sm.register(P)["session_id"]; M = sm.register(P)["session_id"]
store.mark_maintenance(M)
store.put_node("user", "own-lesson", "A's lesson", notes=["case 1"], session_id=A)
sm.note_viewed(M, ["own-lesson"], at=time.time(), full=True)     # the pass read it
store.put_node("user", "own-lesson", "A's lesson, tidied", notes=["case 1"], session_id=M)
sm.note_viewed(A, ["own-lesson"], at=time.time(), full=True)     # A reads the tidy
r = store.put_node("user", "own-lesson", "A's lesson, tidied", notes=["case 1", "case 2"], session_id=A)
if r["note_credited"]:
    bug("X4", f"author A earned a note credit (via {log()[-1].get('via')!r}) on its own lesson")
else:
    ok("X4", "the author's own case is not a credit after a maintenance tidy")
B = sm.register(P)["session_id"]
sm.note_viewed(B, ["own-lesson"], at=time.time(), full=True)
r = store.put_node("user", "own-lesson", "A's lesson, tidied",
                   notes=["case 1", "case 2", "case 3"], session_id=B)
(ok if r["note_credited"] else bug)("X4-control", f"another session's case credited={r['note_credited']}")
store.shutdown()

print("== X5: a crash keeps the vote and loses the ledger entry")
fresh_storage(); store, sm = boot()
A = sm.register(P)["session_id"]          # register saves sessions.json
store.put_node("user", "voted", "a lesson", session_id=A)
store.mark_useful(["voted"], A)           # the stamp is written through to user.json at once
crash(store)                              # before the 30 s saver writes sessions.json
store, sm = boot()
r = store.mark_useful(["voted"], A)
n = len(store.graphs["user"]["nodes"]["voted"]["_useful_ts"])
print(f"     after the crash: second vote accepted={bool(r['accepted'])}, node stamps={n}")
if r["accepted"]:
    bug("X5", "session A endorsed the same node twice across a crash")
else:
    ok("X5", "a vote and its ledger entry survive a crash together")
store.shutdown()

print("== X6: an instance-of edge in the maintain graph credits a user lesson")
fresh_storage(); store, sm = boot()
store.put_node("user", "user-lesson", "a user lesson")
time.sleep(0.01)
store.put_node("maintain", "craft-case", "a craft lesson of the chore agent")
store.put_edge("maintain", "craft-case", "user-lesson", "instance-of")
stamps = store.graphs["user"]["nodes"]["user-lesson"].get("_useful_ts", [])
store.shutdown(); store, sm = boot()        # a load never reconciles the maintain graph
print(f"     user lesson stamps after put_edge at level maintain: {len(stamps)}")
if stamps:
    bug("X6", "put_edge credited a user lesson from the maintain graph; no load would have")
else:
    ok("X6", "put_edge and the load agree: the maintain graph credits no user lesson")
store.shutdown()

print("== checked, holds")
# H1: one vote per node per session, and the caps, under concurrent calls
fresh_storage(); store, sm = boot()
A = sm.register(P)["session_id"]; W = sm.register(P)["session_id"]; M = sm.register(P)["session_id"]
store.mark_maintenance(M)
for i in range(20):
    store.put_node("user", f"n{i}", f"lesson {i}", notes=["c"], session_id=W)
sm.note_viewed(A, [f"n{i}" for i in range(20)], at=time.time(), full=True)
def worker(k):
    for i in range(20):
        if k % 3 == 0:
            store.mark_useful([f"n{i}", f"n{i}"], A)
        elif k % 3 == 1:
            notes = ["c", f"case {k}"]
            store.put_node("user", f"n{i}", f"lesson {i}", notes=notes, session_id=A, guard=False)
        else:
            store.mark_useful([f"n{(i + k) % 20}"], M, credits=1 + (k % 3))
threads = [threading.Thread(target=worker, args=(k,)) for k in range(12)]
for t in threads: t.start()
for t in threads: t.join()
nodes = store.graphs["user"]["nodes"]
liked_a, liked_m = sm.lookup(A)["liked_ids"], sm.lookup(M)["liked_ids"]
m_credits = sm.lookup(M).get("maintenance_credits", 0)
total = sum(len(n.get("_useful_ts", [])) for n in nodes.values())
if (len(liked_a) <= 10 and len(set(liked_a)) == len(liked_a) and len(set(liked_m)) == len(liked_m)
        and m_credits <= 15 and total == len(liked_a) + m_credits):
    ok("H1", f"12 threads: A holds {len(liked_a)} distinct votes (cap 10), M {m_credits} credits "
             f"on {len(liked_m)} nodes (cap 15), node stamps {total} = votes + credits")
else:
    bug("H1", f"A {liked_a}, M credits {m_credits} on {liked_m}, stamps {total}")
store.shutdown()

# H2: reconciliation on load is idempotent (a project case -> a user lesson, and -> a local one)
fresh_storage()
lesson_ts, case_ts = 1.0e9, 1.1e9
GraphPersistence(user_graph_path()).save({"nodes": {
    "u-lesson": {"id": "u-lesson", "gist": "g", "_created_ts": lesson_ts}}, "edges": {}}, {}, {})
GraphPersistence(project_graph_path(P), project_path=P).save({"nodes": {
    "p-lesson": {"id": "p-lesson", "gist": "g", "_created_ts": lesson_ts},
    "case": {"id": "case", "gist": "g", "_created_ts": case_ts}}, "edges": {
    ("case", "u-lesson", "instance-of"): {"from": "case", "to": "u-lesson", "rel": "instance-of"},
    ("case", "p-lesson", "instance-of"): {"from": "case", "to": "p-lesson", "rel": "instance-of"}}}, {}, {})
store, sm = boot()
counts = []
for i in range(6):
    store.reload_user_graph()
    store.reload_project_graph(str(Path(P).resolve()))
    pk = next(k for k in store.graphs if k.startswith("project:"))
    counts.append((len(store.graphs["user"]["nodes"]["u-lesson"].get("_useful_ts", [])),
                   len(store.graphs[pk]["nodes"]["p-lesson"].get("_useful_ts", [])),
                   sum(r.get("via") == "recurrence" for r in log())))
    if i == 2:
        store.shutdown(); store, sm = boot()
(ok if set(counts) == {(1, 1, 2)} else bug)(
    "H2", f"(user stamps, project stamps, recurrence log lines) over 6 reloads and a restart: {counts}")
store.shutdown()

# H3: a session flagged before its reads neither stamps nor promotes; a late flag
fresh_storage(); store, sm = boot()
M = sm.register(P)["session_id"]; L = sm.register(P)["session_id"]
store.put_node("user", "arch", "archived lesson")
store.put_node("user", "arch2", "archived lesson 2")
for nid in ("arch", "arch2"):
    store.graphs["user"]["nodes"][nid]["_archived"] = True
store.mark_maintenance(M)
r = store.read_node("arch", level="user", session_id=M)
n = store.graphs["user"]["nodes"]["arch"]
(ok if not r["promoted"] and n.get("_archived") and not n.get("_last_read_ts") else bug)(
    "H3", "a session flagged before its read: no stamp, no promotion")
r = store.read_node("arch2", level="user", session_id=L)    # late flag: read first ...
store.mark_maintenance(L)                                    # ... flagged afterwards
n2 = store.graphs["user"]["nodes"]["arch2"]
print(f"     late flag: the read before it promoted={r['promoted']} stamped={bool(n2.get('_last_read_ts'))}"
      f" and stays so; the session is maintenance from the flag on={store.is_maintenance(L)}")
store.shutdown()

# H4: useful.jsonl via labels match the granting path
fresh_storage(); store, sm = boot()
A = sm.register(P)["session_id"]; B = sm.register(P)["session_id"]; M = sm.register(P)["session_id"]
store.mark_maintenance(M)
store.put_node("user", "les", "lesson", notes=["c1"], session_id=A)
store.put_node("user", "ep", "episode", session_id=A)
store.graphs["user"]["nodes"]["ep"]["_created_ts"] = store.graphs["user"]["nodes"]["les"]["_created_ts"] + 1
sm.note_viewed(B, ["les"], at=time.time(), full=True)
store.put_node("user", "les", "lesson", notes=["c1", "c2"], session_id=B)   # note credit
store.put_edge("user", "ep", "les", "instance-of", session_id=M)           # recurrence
store.mark_useful(["les"], M, credits=2)                                    # maintenance credit
sm.mark_seen(A, ["ep"], via="search")
store.mark_useful(["ep"], A)                                                # endorsement
vias = [r["via"] for r in log()]
(ok if vias == ["note", "recurrence", "maintenance", "search"] else bug)("H4", f"vias in order: {vias}")
store.shutdown()

print("RESULT:", ("FAIL " + ", ".join(dict.fromkeys(bugs))) if bugs else "all hold")
