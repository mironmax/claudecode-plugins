#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.26 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0926.py

Covers prompt recall v2 — the full search neighbourhood rides the injection:
  1. Hits + connector nodes + path edges render together (tree recall)
  2. Seen nodes render as bare id anchors (attention re-focus, no gist re-dump)
  3. Novelty gate: an all-seen match set injects nothing
  4. Edges repeat across injections (per-blob dedup only — by design),
     while node gists never re-inject
  5. Budget holds; only actually-rendered unseen nodes get marked seen

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

from core.constants import PROMPT_RECALL_CHAR_BUDGET
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
    print("=== v0.9.26 tree-recall tests ===")

    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)

    try:
        sid = session_manager.register(project_dir)["session_id"]
        session_manager.mark_full_read(sid)

        # A small chain: zephyr-uplink -- bridges --> relay-hub -- feeds --> quasar-decoder
        # Prompt will match the two ends; the hub becomes a connector.
        store.put_node(level="project", node_id="zephyr-uplink",
                       gist="Zephyr uplink batches telemetry frames", session_id=sid)
        store.put_node(level="project", node_id="relay-hub",
                       gist="Relay hub multiplexes device streams", session_id=sid)
        store.put_node(level="project", node_id="quasar-decoder",
                       gist="Quasar decoder unpacks frame payloads", session_id=sid)
        store.put_edge(level="project", from_ref="zephyr-uplink", to_ref="relay-hub",
                       rel="bridges", session_id=sid)
        store.put_edge(level="project", from_ref="relay-hub", to_ref="quasar-decoder",
                       rel="feeds", session_id=sid)

        text = build_prompt_recall(store, session_manager, project_dir,
                                   "debug the zephyr uplink and the quasar decoder path")
        check("injects on match", text is not None, text)
        check("both end nodes with gists",
              text and "zephyr-uplink:" in text and "quasar-decoder:" in text, text)
        check("connector rides along", text and "relay-hub" in text, text)
        check("path edges render", text and ("--bridges-->" in text or "--feeds-->" in text),
              text)
        check("connections section present", text and "connections:" in text, text)
        check("fits budget", text and len(text) <= PROMPT_RECALL_CHAR_BUDGET, text and len(text))

        seen = session_manager.get_seen(sid)
        check("rendered nodes marked seen",
              {"zephyr-uplink", "quasar-decoder", "relay-hub"} <= seen, seen)

        # Same prompt again — every node seen — novelty gate closes.
        text2 = build_prompt_recall(store, session_manager, project_dir,
                                    "debug the zephyr uplink and the quasar decoder path")
        check("all-seen match set injects nothing", text2 is None, text2)

        # A NEW node edged to a seen one: the new gist plus the seen anchor
        # (and the edge trace, repeating across injections by design).
        store.put_node(level="project", node_id="fresnel-cache",
                       gist="Fresnel cache memoizes decoded quasar frames", session_id=sid)
        store.put_edge(level="project", from_ref="fresnel-cache", to_ref="quasar-decoder",
                       rel="memoizes-output-of", session_id=sid)
        text3 = build_prompt_recall(store, session_manager, project_dir,
                                    "why does the fresnel cache serve stale quasar frames?")
        check("new node injects with gist", text3 and "fresnel-cache:" in text3, text3)
        check("seen node returns as bare anchor",
              text3 and "quasar-decoder (in context)" in text3, text3)
        check("seen gist never re-dumped",
              text3 and "unpacks frame payloads" not in text3, text3)
        check("edge to seen node renders (per-blob dedup only)",
              text3 and "--memoizes-output-of-->" in text3, text3)

        # Unrelated prompt still silent.
        check("no match stays silent",
              build_prompt_recall(store, session_manager, project_dir,
                                  "completely unrelated gardening question about tulips") is None)

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
