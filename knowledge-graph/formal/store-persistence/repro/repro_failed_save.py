"""Reproduce the Lean counterexamples for the store's save protocol.

Real code end to end: MultiProjectGraphStore, GraphPersistence.save (its own
except-path), shutdown, reload. The only injection is a transient ENOSPC from
json.dump inside GraphPersistence.save — what a full disk looks like.
"""
import errno, json, os, sys, tempfile
from pathlib import Path
root = tempfile.mkdtemp()
os.environ["KG_STORAGE_ROOT"] = root
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "server"))
import core.persistence as P
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager

real_dump = json.dump
disk_full = {"on": False}
def dump(*a, **k):
    if disk_full["on"]:
        raise OSError(errno.ENOSPC, "No space left on device")
    return real_dump(*a, **k)
P.json.dump = dump

def new_store():
    return MultiProjectGraphStore(GraphConfig(save_interval=9999), HTTPSessionManager(),
                                  broadcast_callback=None)

def on_disk(store, nid):
    return nid in json.load(open(store.config.user_path))["nodes"]

results = {}
# --- A: failed write-through clears dirty -> shutdown skips it -> node gone after restart
s = new_store()
s.put_node("user", "base", "baseline node")
disk_full["on"] = True
r = s.put_node("user", "a1", "written while the disk was full")
disk_full["on"] = False                      # disk recovers before shutdown
print("A put_node returned:", {k: r[k] for k in r if k in ("status", "ok", "error")} or r)
print("A dirty after failed save:", s.dirty["user"])
s.shutdown()
results["A"] = on_disk(new_store(), "a1")
print("A node present after restart:", results["A"])

# --- B: failed write-through, then GET /api/graph/read?reload=true discards it from memory
s = new_store()
disk_full["on"] = True
s.put_node("user", "b1", "written while the disk was full")
disk_full["on"] = False
s.read_graphs(force_reload=True)
results["B"] = "b1" in s.graphs["user"]["nodes"]
print("B node in memory after reload:", results["B"])
s.shutdown()

# --- C: no failure at all: read_node stamp (dirty, awaiting the 30 s saver) lost by reload
s = new_store()
s.put_node("user", "c1", "some node")
s.read_node("c1", level="user")
ts = s.graphs["user"]["nodes"]["c1"].get("_last_read_ts")
s.read_graphs(force_reload=True)
results["C"] = s.graphs["user"]["nodes"]["c1"].get("_last_read_ts") == ts
print("C _last_read_ts survives reload:", results["C"])
s.shutdown()

print("RESULT:", "all hold" if all(results.values()) else
      "FAIL " + ", ".join(k for k, v in results.items() if not v))
