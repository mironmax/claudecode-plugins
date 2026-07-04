#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.21 change areas.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0921.py

Covers:
  1. build_bootstrap header: directive wording (PARTIAL view, required full
     read, announce moved AFTER the full read), budget still holds
  2. Session full-read tracking: mark/has, persistence across reload,
     find_by_project_path newest-wins and rejection of invalid paths
  3. Compactor grace-period stall: logs the stall ONCE at INFO (later ticks
     debug), names the graph, resets after recovery, and the eligible path
     still archives with the label in its log line

Uses only in-memory fixtures — never touches real graphs under
~/.knowledge-graph.
"""

import logging
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constants import BOOTSTRAP_CHAR_BUDGET
from core.compactor import Compactor
from core.estimator import CharEstimator
from core.scorer import NodeScorer
from mcp_http.read_format import build_bootstrap

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


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def infos(self):
        return [r.getMessage() for r in self.records if r.levelno == logging.INFO]


# --- 1. bootstrap header is directive ----------------------------------------
def test_bootstrap_header_directive():
    print("bootstrap header directive:")
    graphs = {
        "user": {"nodes": [{"id": "u-a", "gist": "alpha"}], "edges": []},
        "project": {"nodes": [{"id": "p-a", "gist": "beta"}], "edges": []},
    }
    scores = {"user": {"u-a": 0.9}, "project": {"p-a": 0.7}}
    result = build_bootstrap(graphs, scores, "dirsess")
    ctx = result["context"]

    check("still starts with preload marker", ctx.startswith("KG MEMORY PRELOADED"))
    check("names itself PARTIAL", "PARTIAL view" in ctx, ctx[:200])
    check("full read is REQUIRED", "REQUIRED before any substantive work" in ctx)
    check("announce moved AFTER full read",
          'AFTER that full read announce "I have recalled KG Memories"' in ctx)
    check("old announce-on-scan wording gone", "after scanning both sections" not in ctx)
    check("subagent warning kept", "Subagents never receive this preload" in ctx)
    check("session id present", "dirsess" in ctx)
    check("fits budget", len(ctx) <= BOOTSTRAP_CHAR_BUDGET, len(ctx))


# --- 2. session full-read tracking --------------------------------------------
def test_session_full_read_tracking():
    print("session full-read tracking:")
    tmp = Path(tempfile.mkdtemp())
    os.environ["KG_STORAGE_ROOT"] = str(tmp)
    try:
        from mcp_http.session_manager import HTTPSessionManager

        mgr = HTTPSessionManager()
        home = str(Path.home())
        sid = mgr.register(home)["session_id"]

        check("fresh session has no full read", not mgr.has_full_read(sid))
        mgr.mark_full_read(sid)
        check("flag flips after mark", mgr.has_full_read(sid))
        check("unknown session is False", not mgr.has_full_read("nope1234"))

        mgr.save_sessions()
        mgr2 = HTTPSessionManager()
        check("flag survives save/load", mgr2.has_full_read(sid))

        # newest session wins the project_path lookup
        time.sleep(0.01)
        sid2 = mgr.register(home)["session_id"]
        hit = mgr.find_by_project_path(home)
        check("lookup finds a session", hit is not None)
        check("newest session wins", hit and hit[0] == sid2, hit and hit[0])
        check("newest has no full read yet", hit and not hit[1].get("full_read_ts"))

        check("unmatched path returns None",
              mgr.find_by_project_path(str(Path.home() / "no-such-project-xyz")) is None)
        check("path outside home returns None (no raise)",
              mgr.find_by_project_path("/etc") is None)
    finally:
        del os.environ["KG_STORAGE_ROOT"]


# --- 3. compactor grace stall logs once ----------------------------------------
def test_compactor_stall_single_log():
    print("compactor grace stall:")
    scorer = NodeScorer(grace_period_days=5)
    est = CharEstimator()
    comp = Compactor(scorer, est, max_chars=800)

    now = time.time()
    fresh = {
        f"n{i}": {"id": f"n{i}", "gist": "x" * 200, "_created_ts": now}
        for i in range(10)
    }

    cap = _Capture()
    log = logging.getLogger("core.compactor")
    log.addHandler(cap)
    old_level = log.level
    log.setLevel(logging.DEBUG)
    try:
        archived = comp.compact_if_needed(fresh, {}, {}, label="project:test")
        check("nothing archived while all in grace", archived == [])
        stall_infos = [m for m in cap.infos() if "grace period" in m]
        check("stall logged once at INFO", len(stall_infos) == 1, cap.infos())
        check("stall log names the graph", "project:test" in stall_infos[0])
        check("stall log carries counts", "10 active nodes" in stall_infos[0], stall_infos[0])

        cap.records.clear()
        comp.compact_if_needed(fresh, {}, {}, label="project:test")
        comp.compact_if_needed(fresh, {}, {}, label="project:test")
        check("later ticks are silent at INFO", cap.infos() == [], cap.infos())

        # a second graph stalls independently — its own single log
        cap.records.clear()
        comp.compact_if_needed(dict(fresh), {}, {}, label="user")
        check("second graph logs its own stall",
              any("user" in m for m in cap.infos()), cap.infos())

        # recovery: once under budget, the suppression resets...
        cap.records.clear()
        tiny = {"n0": {"id": "n0", "gist": "small", "_created_ts": now}}
        comp.compact_if_needed(tiny, {}, {}, label="project:test")
        # ...so going over budget again logs again
        comp.compact_if_needed(fresh, {}, {}, label="project:test")
        check("stall re-logs after recovery",
              any("grace period" in m for m in cap.infos()), cap.infos())

        # eligible path: nodes past grace archive normally, log names the graph
        cap.records.clear()
        old_ts = now - 10 * 86400
        ripe = {
            f"r{i}": {"id": f"r{i}", "gist": "y" * 200, "_created_ts": old_ts}
            for i in range(10)
        }
        archived = comp.compact_if_needed(ripe, {}, {}, label="project:ripe")
        check("past-grace nodes archive", len(archived) > 0, archived)
        check("compaction log names the graph",
              any("Compacting graph project:ripe" in m for m in cap.infos()), cap.infos())
    finally:
        log.removeHandler(cap)
        log.setLevel(old_level)


if __name__ == "__main__":
    test_bootstrap_header_directive()
    test_session_full_read_tracking()
    test_compactor_stall_single_log()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
