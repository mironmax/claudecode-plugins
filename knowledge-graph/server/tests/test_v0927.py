#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.27 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0927.py

Covers IDF-weighted search terms (the fix for tangential prompt-recall
injections on conversational vocabulary, observed live 2026-07-20):
  1. A ubiquitous term contributes ~nothing; a rare term dominates ranking
  2. Prompt recall: generic-vocabulary prompts stay SILENT even when the
     generic terms technically match many nodes
  3. Prompt recall: one rare term still speaks; rare+generic mixes rank the
     rare match first
  4. kg_search ordering benefits: rare-term hit outranks generic-term hit

Uses a temp KG_STORAGE_ROOT and a temp project under ~/.cache.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from mcp_http.ambient import build_prompt_recall
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


def main():
    print("=== v0.9.27 IDF-weighted search tests ===")

    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)

    try:
        sid = session_manager.register(project_dir)["session_id"]
        session_manager.mark_full_read(sid)

        # Ten nodes all sharing generic vocabulary ("project", "works",
        # "restart"); exactly one carries a rare distinctive term.
        for i in range(9):
            store.put_node(level="project", node_id=f"generic-{i}",
                           gist=f"The project works fine after restart, module {i} checks pass",
                           session_id=sid)
        store.put_node(level="project", node_id="xanthic-parser",
                       gist="The xanthic parser in this project works on restart tokens",
                       session_id=sid)

        # --- 1. raw ranking ---------------------------------------------------
        print("ranking:")
        result = store.search("xanthic restart project", session_id=sid)
        top = result["top"]
        check("rare term dominates ranking",
              top and top[0]["id"] == "xanthic-parser",
              [r["id"] for r in top[:3]])
        rare_score = top[0]["score"]
        runner_up = top[1]["score"] if len(top) > 1 else 0
        check("rare hit clearly separated from generic hits",
              rare_score > runner_up * 2, (rare_score, runner_up))

        # --- 2. generic prompts stay silent ----------------------------------
        print("prompt recall gates:")
        text = build_prompt_recall(store, session_manager, project_dir,
                                   "does the project still works fine after restart checks")
        check("all-generic prompt injects nothing", text is None, text)

        # --- 3. rare term speaks ---------------------------------------------
        text = build_prompt_recall(store, session_manager, project_dir,
                                   "what about the xanthic parser handling after restart")
        check("rare-term prompt injects", text is not None, text)
        check("rare match leads the injection",
              text and "xanthic-parser:" in text, text)
        check("generic nodes not dragged along",
              text and "generic-0" not in text and "generic-5" not in text, text)

        # --- 4. term in every node contributes zero ---------------------------
        # "project" appears in all 10 nodes -> idf 0 -> a prompt of only that
        # term (plus stopword-length filler) cannot rank anything.
        text = build_prompt_recall(store, session_manager, project_dir,
                                   "project project project")
        check("all-node term alone injects nothing", text is None, text)

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
