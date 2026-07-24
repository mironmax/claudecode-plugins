#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.28 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0928.py

Covers the human-prompt gate on ambient recall (week-1 audit 2026-07-24
found 20% of injections firing on harness records, none on user intent):
  1. Task-notification records inject nothing, even on strong term matches
  2. Image-paste placeholders / bare dragged paths inject nothing
  3. Path tokens reduce to basenames — a basename can still match touches,
     but paths alone never clear the speak-at-all floor
  4. Short directive prompts (real text above the floor) still speak
  5. The recall header no longer carries the unused depth invitation

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

from mcp_http.ambient import _prompt_text, build_prompt_recall
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
    print("=== v0.9.28 human-prompt gate tests ===")

    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)

    try:
        sid = session_manager.register(project_dir)["session_id"]
        session_manager.mark_full_read(sid)

        # A node with a rare term in the gist, another reachable via touches.
        store.put_node(level="project", node_id="xanthic-parser",
                       gist="The xanthic parser normalizes quixotic tokens",
                       session_id=sid)
        store.put_node(level="project", node_id="zephyr-styles",
                       gist="Styling entry point for the zephyr widget",
                       touches=["assets/mm-zephyr.css:1-40"],
                       session_id=sid)

        # --- 1. harness records stay silent ----------------------------------
        print("harness records:")
        text = build_prompt_recall(
            store, session_manager, project_dir,
            "[SYSTEM NOTIFICATION - NOT USER INPUT]\nxanthic parser task done")
        check("system-notification prompt injects nothing", text is None, text)

        text = build_prompt_recall(
            store, session_manager, project_dir,
            "<task-notification>agent finished: xanthic parser sweep</task-notification>")
        check("task-notification prompt injects nothing", text is None, text)

        text = build_prompt_recall(
            store, session_manager, project_dir,
            "[Image: source: /home/user/Pictures/xanthic-parser-screenshot.png]")
        check("image-only prompt injects nothing", text is None, text)

        text = build_prompt_recall(
            store, session_manager, project_dir,
            "'/home/user/Downloads/xanthic parser notes.pdf'")
        check("bare dragged path injects nothing", text is None, text)

        # --- 2. gate primitive ------------------------------------------------
        print("gate primitive:")
        check("paths reduce to basenames in term text",
              _prompt_text("please review assets/mm-zephyr.css for the widget")
              == "please review mm-zephyr.css for the widget",
              _prompt_text("please review assets/mm-zephyr.css for the widget"))
        check("path chars don't count toward the floor",
              _prompt_text("ok /very/long/path/that/is/not/text.png") is None)
        check("short directive clears the floor",
              _prompt_text("Yes commit all") == "Yes commit all")

        # --- 3. real prompts still speak --------------------------------------
        print("real prompts:")
        text = build_prompt_recall(
            store, session_manager, project_dir,
            "how does the xanthic parser handle quixotic tokens")
        check("real prompt with rare terms injects", text is not None, text)
        check("header trimmed to essence",
              text is not None and text.splitlines()[0]
              == "KG recall — memory matching this prompt:", text)
        check("unused depth invitation gone",
              text is not None and "depth:" not in text and "ids=[...]" not in text,
              text)

        text = build_prompt_recall(
            store, session_manager, project_dir,
            "tweak the styles in assets/mm-zephyr.css a bit")
        check("basename from a path can still drive a touches match",
              text is not None and "zephyr-styles" in text, text)

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
