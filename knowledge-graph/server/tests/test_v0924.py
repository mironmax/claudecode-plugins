#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.24 change areas.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0924.py

Covers the ambient-memory endpoints:
  1. Prompt term extraction: stopwords, length floor, dedupe, cap
  2. Prompt recall: full-read nudge precedence, matched gists injected once
     (seen-marking), generic/unrelated prompts stay silent, budget holds
  3. Tool events: noise paths ignored, first read silent, re-derivation across
     distinct sessions nudges once, coverage suppresses, throttles hold,
     WebFetch count threshold, counters persist
  4. REST wrappers: ready-to-print hook JSON shapes, {} on nothing-to-say

Uses a temp KG_STORAGE_ROOT and a temp project under ~/.cache — never touches
real graphs under ~/.knowledge-graph.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Storage isolation must precede the imports that read it.
_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from fastapi.testclient import TestClient

from core.constants import (
    NUDGE_MAX_PER_SESSION,
    PROMPT_RECALL_CHAR_BUDGET,
)
from mcp_http import ambient
from mcp_http.ambient import (
    FULL_READ_NUDGE,
    _events_path,
    _terms,
    build_prompt_recall,
    handle_tool_event,
)
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager

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


def main():
    print("=== v0.9.24 ambient memory tests ===")

    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)
    client = TestClient(create_rest_api(store, session_manager, ConnectionManager(), version="test"))

    try:
        # --- 1. term extraction ---------------------------------------------
        print("term extraction:")
        terms = _terms("Please make the compactor grace-stall logging less noisy")
        check("keeps signal terms", "compactor" in terms and "logging" in terms, terms)
        check("drops stopwords", "please" not in terms and "make" not in terms, terms)
        check("drops short terms", all(len(t) >= 4 for t in terms), terms)
        check("generic prompt yields nothing", _terms("yes go on do it") == [])
        check("dedupes preserving order", _terms("alpha beta alpha beta") == ["alpha", "beta"])
        check("caps term count", len(_terms(" ".join(f"term{i:04d}" for i in range(50)))) == 24)

        # --- 2. prompt recall ------------------------------------------------
        print("prompt recall:")
        check("no session -> None",
              build_prompt_recall(store, session_manager, project_dir, "compactor stall") is None)

        sid = session_manager.register(project_dir)["session_id"]
        check("pre-full-read -> THE nudge",
              build_prompt_recall(store, session_manager, project_dir, "anything at all")
              == FULL_READ_NUDGE)

        session_manager.mark_full_read(sid)
        store.put_node(level="project", node_id="compactor-grace-stall",
                       gist="Compactor stalls when all active nodes sit in grace period",
                       notes=["grace stall detail"], session_id=sid)
        store.put_node(level="project", node_id="unrelated-widget",
                       gist="Widget frobnication order matters", session_id=sid)

        text = build_prompt_recall(store, session_manager, project_dir,
                                   "why does the compactor grace stall keep happening?")
        check("matching prompt injects", text is not None and "compactor-grace-stall" in text,
              text)
        check("injection carries the gist", text and "stalls when all active" in text, text)
        check("injection cites kg_read depth path", text and "kg_read" in text and sid in text)
        check("unrelated node not injected", text and "unrelated-widget" not in text, text)
        check("fits budget", text and len(text) <= PROMPT_RECALL_CHAR_BUDGET, text and len(text))

        text2 = build_prompt_recall(store, session_manager, project_dir,
                                    "more about the compactor grace stall please")
        check("second hit on same node stays silent (seen)", text2 is None, text2)

        check("unrelated prompt -> None",
              build_prompt_recall(store, session_manager, project_dir,
                                  "quantum banana harvest festival") is None)
        check("generic prompt -> None",
              build_prompt_recall(store, session_manager, project_dir, "yes go on") is None)

        # --- 3. tool events --------------------------------------------------
        print("tool events:")

        def read_event(fp, claude_sid):
            return handle_tool_event(store, session_manager, {
                "cwd": project_dir, "tool_name": "Read",
                "session_id": claude_sid, "tool_input": {"file_path": fp},
            })

        target = os.path.join(project_dir, "src", "engine.py")
        check("noise path ignored",
              read_event(os.path.join(project_dir, "node_modules", "x.js"), "cs1") is None)
        check("tmp path ignored",
              handle_tool_event(store, session_manager, {
                  "cwd": project_dir, "tool_name": "Read", "session_id": "cs1",
                  "tool_input": {"file_path": "/tmp/scratch.txt"}}) is None)
        check("first read silent", read_event(target, "cs1") is None)
        check("same-session re-read still silent (1 distinct)",
              read_event(target, "cs1") is None)

        nudge = read_event(target, "cs2")
        check("2nd distinct session nudges", nudge is not None and "src/engine.py" in nudge,
              nudge)
        check("nudge names the kg session", nudge and sid in nudge, nudge)
        check("nudge suggests kg_put_node", nudge and "kg_put_node" in nudge)
        check("same target re-nudge blocked (target cooldown)",
              read_event(target, "cs3") is None)

        ev_path = _events_path(project_dir)
        data = json.loads(ev_path.read_text())
        key = "read:src/engine.py"
        check("counters persisted", data["events"][key]["count"] == 4
              and len(data["events"][key]["sessions"]) == 3, data["events"].get(key))

        # Covered target: a node touch suppresses the nudge entirely.
        store.put_node(level="project", node_id="engine-component",
                       gist="engine core", touches=["src/other.py"], session_id=sid)
        covered = os.path.join(project_dir, "src", "other.py")
        read_event(covered, "cs1")
        check("covered target never nudges", read_event(covered, "cs2") is None)

        # Session throttle: clear cooldown, exhaust the per-session budget.
        data = json.loads(ev_path.read_text())
        data["throttle"][sid] = {"count": NUDGE_MAX_PER_SESSION, "last_ts": 0}
        ev_path.write_text(json.dumps(data))
        t2 = os.path.join(project_dir, "src", "second.py")
        read_event(t2, "cs1")
        check("per-session nudge budget holds", read_event(t2, "cs2") is None)

        # Reset throttle; cooldown-seconds gate.
        data = json.loads(ev_path.read_text())
        data["throttle"][sid] = {"count": 0, "last_ts": time.time()}
        ev_path.write_text(json.dumps(data))
        t3 = os.path.join(project_dir, "src", "third.py")
        read_event(t3, "cs1")
        check("cooldown gate holds", read_event(t3, "cs2") is None)

        # Clear throttle entirely: web threshold is by count, not sessions.
        data = json.loads(ev_path.read_text())
        data["throttle"] = {}
        ev_path.write_text(json.dumps(data))

        def web_event(url):
            return handle_tool_event(store, session_manager, {
                "cwd": project_dir, "tool_name": "WebFetch",
                "session_id": "cs1", "tool_input": {"url": url}})

        check("first fetch silent", web_event("https://docs.example.com/api") is None)
        wn = web_event("https://docs.example.com/api")
        check("2nd fetch of same URL nudges", wn is not None and "docs.example.com" in wn, wn)

        check("unknown tool ignored",
              handle_tool_event(store, session_manager, {
                  "cwd": project_dir, "tool_name": "Grep",
                  "session_id": "cs1", "tool_input": {"pattern": "x"}}) is None)

        # --- 4. REST wrappers -------------------------------------------------
        print("rest wrappers:")
        r = client.post("/api/prompt_context", json={"cwd": project_dir, "prompt": "go on"})
        check("nothing to say -> {}", r.status_code == 200 and r.json() == {}, r.text)

        store.put_node(level="project", node_id="zephyr-telemetry-pipeline",
                       gist="Zephyr telemetry pipeline batches uplink frames",
                       session_id=sid)
        r = client.post("/api/prompt_context",
                        json={"cwd": project_dir,
                              "prompt": "debug the zephyr telemetry pipeline uplink"})
        body = r.json()
        check("recall arrives as ready hook JSON",
              body.get("hookSpecificOutput", {}).get("hookEventName") == "UserPromptSubmit"
              and "zephyr-telemetry-pipeline" in body["hookSpecificOutput"]["additionalContext"],
              body)

        data = json.loads(ev_path.read_text())
        data["throttle"] = {}
        ev_path.write_text(json.dumps(data))
        t4 = os.path.join(project_dir, "src", "fourth.py")
        client.post("/api/tool_event", json={
            "cwd": project_dir, "tool_name": "Read", "session_id": "csA",
            "tool_input": {"file_path": t4}})
        r = client.post("/api/tool_event", json={
            "cwd": project_dir, "tool_name": "Read", "session_id": "csB",
            "tool_input": {"file_path": t4}})
        body = r.json()
        check("tool nudge arrives as PostToolUse hook JSON",
              body.get("hookSpecificOutput", {}).get("hookEventName") == "PostToolUse"
              and "src/fourth.py" in body["hookSpecificOutput"]["additionalContext"],
              body)
        r = client.post("/api/tool_event", json={"tool_name": "Read"})
        check("malformed payload -> {}", r.status_code == 200 and r.json() == {}, r.text)

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
