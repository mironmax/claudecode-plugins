"""What an agent's own retry does (Shim.lean, harnessRetry): the shim answered
with an error, but the server had applied the call before it died, and the
agent sends the same call again.

Real: MultiProjectGraphStore and HTTPSessionManager, called the way the MCP
handlers call them. Each write is applied twice with the same arguments and
the stored state is compared after the first and after the second.
"""
import copy, os, sys, tempfile, time
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
import logging; logging.disable(logging.WARNING)
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager

sm = HTTPSessionManager()
store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, None)
sid = sm.register(None)["session_id"]
store.put_node("user", "a", "node a", notes=["n1"], session_id=sid)
store.put_node("user", "b", "node b", session_id=sid)
store.put_node("user", "c", "node c", session_id=sid)


def content():
    g = store.graphs["user"]
    nodes = {k: (v.get("gist"), tuple(v.get("notes", [])), tuple(v.get("touches", [])),
                 len(v.get("_useful_ts", []) or [])) for k, v in g["nodes"].items()}
    edges = sorted((e["from"], e["to"], e["rel"], tuple(e.get("notes", []))) for e in (g["edges"].values() if isinstance(g["edges"], dict) else g["edges"]))
    progress = copy.deepcopy(store._progress.get("user", {}))
    for task in progress.values():
        task.pop("last_ts", None)
        for entry in task.get("_trail", []):
            entry.pop("last_ts", None)
    return nodes, edges, progress


def twice(name, call):
    outcomes = []
    for _ in range(2):
        try:
            call()
            outcomes.append("ok")
        except Exception as e:
            outcomes.append(f"refused ({type(e).__name__}: {str(e)[:60]})")
        outcomes.append(content())
        time.sleep(0.01)
    same = outcomes[1] == outcomes[3]
    print(f"{name:12} first: {outcomes[0]:4}  again: {outcomes[2]}")
    print(f"{'':12} {'state unchanged by the repeat' if same else 'the repeat changed the state'}")
    return same


r = {
    "put_node": twice("put_node", lambda: store.put_node("user", "a", "node a", notes=["n1", "n2"], session_id=sid)),
    "put_edge": twice("put_edge", lambda: store.put_edge("user", "a", "b", "uses", session_id=sid)),
    "kg_useful": twice("kg_useful", lambda: store.mark_useful(["b"], sid)),
    "kg_progress": twice("kg_progress", lambda: store.set_progress("scout", {"done": 1}, "user", sid)),
    "rename": twice("rename", lambda: store.rename_node("c", "c2", session_id=sid)),
    "delete_edge": twice("delete_edge", lambda: store.delete_edge("a", "b", "uses", level="user", session_id=sid)),
    "delete_node": twice("delete_node", lambda: store.delete_node("c2", session_id=sid)),
}
print(f"(endorsements on b: {len(store.graphs['user']['nodes']['b'].get('_useful_ts') or [])}; "
      f"kg_progress trail entries: {len(store._progress['user']['scout']['_trail'])})")
store.shutdown()
print("changed by a repeat:", [k for k, v in r.items() if not v] or "none")
