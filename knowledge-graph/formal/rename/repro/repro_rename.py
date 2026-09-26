"""Reproduce the Lean rename counterexamples against the real store.

Real: put_node, put_edge, rename_node (loaded-graph rewrite + disk sweep),
project graph loading with _clean_orphaned_edges. No stubs.
Resolution of an edge endpoint in a project graph = local node, else user node
(store.py _clean_orphaned_edges doctrine). `target()` reports that.
"""
import os, shutil, sys, tempfile
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()

import atexit, shutil
def _home_tmpdir():
    """Project roots must live under $HOME (safe_project_path); removed at exit."""
    d = tempfile.mkdtemp(dir=Path.home(), prefix="kg-formal-")
    atexit.register(shutil.rmtree, d, True)
    return d
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "server"))
import logging; logging.disable(logging.WARNING)
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager
from core.constants import project_namespace

def new_store():
    return MultiProjectGraphStore(GraphConfig(save_interval=9999), HTTPSessionManager(), None)

def project():
    return _home_tmpdir()

def target(store, proj, ref):
    """Which node an endpoint in project `proj` resolves to."""
    with store.lock:
        store._ensure_project_loaded(proj)
        if ref in store.graphs[project_namespace(proj)]["nodes"]:
            return f"project:{ref}"
        return f"user:{ref}" if ref in store.graphs["user"]["nodes"] else "DANGLING"

def edges_from(store, proj, src):
    with store.lock:
        store._ensure_project_loaded(proj)
        return sorted(e["to"] for e in store.graphs[project_namespace(proj)]["edges"].values()
                      if e["from"] == src)

fails = []
def expect(name, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'BUG'} {name}: got {got!r}, want {want!r}")
    if not ok: fails.append(name)

def reset():
    root = os.environ["KG_STORAGE_ROOT"]
    shutil.rmtree(root); os.makedirs(root)

# R1: user rename a->b; LOADED project has its own node b and an edge p->a (to user a)
print("R1 user rename, loaded project owns the new name")
reset(); s = new_store(); P = project()
s.put_node("user", "a", "user node a")
s.put_node("project", "p", "p", project_path=P)
s.put_node("project", "b", "a different, project-local b", project_path=P)
s.put_edge("project", "p", "a", "uses", project_path=P)
r = s.rename_node("a", "b", level="user")
print("  skipped_graphs:", r["skipped_graphs"])
expect("p's edge still reaches the renamed user node", [target(s, P, t) for t in edges_from(s, P, "p")], ["user:b"])
s.shutdown()

# R2: same, but the project graph is NOT loaded (on disk only) -> skip:collision
print("R2 user rename, unloaded project owns the new name")
reset(); s = new_store(); P = project()
s.put_node("user", "a", "user node a")
s.put_node("project", "p", "p", project_path=P)
s.put_node("project", "b", "project-local b", project_path=P)
s.put_edge("project", "p", "a", "uses", project_path=P)
s.shutdown(); s = new_store()                     # fresh process: only user graph loaded
r = s.rename_node("a", "b", level="user")
print("  skipped_graphs:", r["skipped_graphs"])
expect("p keeps an edge to the renamed user node after the project loads",
       [target(s, P, t) for t in edges_from(s, P, "p")], ["user:b"])
s.shutdown()

# R3: PROJECT rename of a node whose id also exists in the user graph;
#     another project's edge meant the USER node and gets rewritten anyway
print("R3 project rename sweeps other projects' refs to a same-named user node")
reset(); s = new_store(); P1, P2 = project(), project()
s.put_node("user", "a", "user a")
s.put_node("project", "a", "project-local a in P1", project_path=P1)
s.put_node("project", "q", "q", project_path=P2)
s.put_edge("project", "q", "a", "uses", project_path=P2)
s.shutdown(); s = new_store()
s.rename_node("a", "b", level="project", project_path=P1)
expect("P2's edge still reaches user a", [target(s, P2, t) for t in edges_from(s, P2, "q")], ["user:a"])
s.shutdown()

# R4: project rename INTO a name the user graph has -> captures refs to the user node
print("R4 project rename to a name the user graph owns")
reset(); s = new_store(); P = project()
s.put_node("user", "b", "user b")
s.put_node("project", "a", "project a", project_path=P)
s.put_node("project", "q", "q", project_path=P)
s.put_edge("project", "q", "b", "uses", project_path=P)
s.rename_node("a", "b", level="project", project_path=P)
expect("q's edge still reaches user b", [target(s, P, t) for t in edges_from(s, P, "q")], ["user:b"])
s.shutdown()

print("RESULT:", "all hold" if not fails else "FAIL " + ", ".join(fails))
