#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.29 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0929.py

Covers session identity across resume/compact and concurrent sessions (the
week-1 audit observed mid-session session_id drift, and resume was seen
re-preloading + re-nagging — both reset the seen-set that recall dedup
depends on):
  1. Claude-sid binding: register binds, rebind moves, lookup resolves
  2. Transcript recovery: the KG sid our renders left in a forked transcript
  3. Recall resolves by Claude sid — concurrent sessions keep separate
     seen-sets (no drift)
  4. Bootstrap reuse: compact (same Claude sid) and resume (new Claude sid,
     recovered via transcript) reuse the session; clear starts fresh
  5. Reused sessions keep full-read state — no renewed full-read nudge

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

from fastapi.testclient import TestClient

from mcp_http.ambient import FULL_READ_NUDGE, build_prompt_recall
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager, recover_kg_sid_from_transcript
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager

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
    print("=== v0.9.29 session identity tests ===")

    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)
    rest_api = create_rest_api(store, session_manager, ConnectionManager(), version="test")
    client = TestClient(rest_api)

    try:
        # --- 1. claude-sid binding -------------------------------------------
        print("binding:")
        sid_a = session_manager.register(project_dir, claude_sid="cc-aaaa")["session_id"]
        check("register binds claude sid",
              session_manager.find_by_claude_sid("cc-aaaa")[0] == sid_a)
        sid_b = session_manager.register(project_dir, claude_sid="cc-bbbb")["session_id"]
        session_manager.bind_claude_sid(sid_b, "cc-aaaa")
        check("rebind moves the claude sid",
              session_manager.find_by_claude_sid("cc-aaaa")[0] == sid_b)
        check("old holder unbound",
              session_manager._sessions[sid_a].get("claude_sid") is None)
        session_manager.bind_claude_sid(sid_a, "cc-aaaa")  # restore for later

        # --- 2. transcript recovery ------------------------------------------
        print("transcript recovery:")
        tf = Path(tempfile.mkdtemp(prefix="kg-test-tr-")) / "t.jsonl"
        tf.write_text(
            '{"x":"KG MEMORY PRELOADED ... session_id: deadbeef (pass it to every kg_* call)"}\n'
            '{"x":"...\\nSession: cafe0123"}\n'
        )
        check("last KG sid marker wins",
              recover_kg_sid_from_transcript(str(tf)) == "cafe0123",
              recover_kg_sid_from_transcript(str(tf)))
        check("missing transcript -> None",
              recover_kg_sid_from_transcript("/nonexistent/x.jsonl") is None)

        # --- 3. concurrent sessions keep separate seen-sets ------------------
        print("no drift across concurrent sessions:")
        session_manager.mark_full_read(sid_a)
        session_manager.mark_full_read(sid_b)
        store.put_node(level="project", node_id="xanthic-parser",
                       gist="The xanthic parser normalizes quixotic tokens",
                       session_id=sid_a)
        text = build_prompt_recall(store, session_manager, project_dir,
                                   "explain the xanthic parser", claude_sid="cc-aaaa")
        check("older session (by claude sid) gets the injection",
              text is not None and "xanthic-parser" in text, text)
        check("seen marked in OWN session only",
              "xanthic-parser" in session_manager.get_seen(sid_a)
              and "xanthic-parser" not in session_manager.get_seen(sid_b))
        text = build_prompt_recall(store, session_manager, project_dir,
                                   "explain the xanthic parser", claude_sid="cc-bbbb")
        check("other session still gets its own injection",
              text is not None and "xanthic-parser" in text, text)

        # --- 4. bootstrap reuse ----------------------------------------------
        print("bootstrap reuse:")
        r = client.get("/api/session_bootstrap", params={
            "project_path": project_dir, "claude_session_id": "cc-boot-1",
            "source": "startup"}).json()
        sid1 = r["session_id"]
        check("startup registers fresh", r["reused"] is False)

        r = client.get("/api/session_bootstrap", params={
            "project_path": project_dir, "claude_session_id": "cc-boot-1",
            "source": "compact"}).json()
        check("compact reuses via claude sid",
              r["reused"] is True and r["session_id"] == sid1, r)
        check("compact re-renders the preload (context lost it)",
              bool(r["stats"]) and "resumed" not in r["context"][:40],
              r["context"][:80])

        resumed_tf = tf.parent / "resumed.jsonl"
        resumed_tf.write_text(
            f'{{"x":"KG MEMORY PRELOADED ... session_id: {sid1} (pass it to every kg_* call)"}}\n')
        r = client.get("/api/session_bootstrap", params={
            "project_path": project_dir, "claude_session_id": "cc-boot-2",
            "source": "resume", "transcript_path": str(resumed_tf)}).json()
        check("resume recovers session from transcript",
              r["reused"] is True and r["session_id"] == sid1, r)
        check("resume rebinds the new claude sid",
              session_manager.find_by_claude_sid("cc-boot-2")[0] == sid1)
        check("resume injects a continuity note, not a duplicate preload",
              r["context"].startswith("KG memory session resumed")
              and r["text"] == "" and not r["stats"], r["context"][:80])

        r = client.get("/api/session_bootstrap", params={
            "project_path": project_dir, "claude_session_id": "cc-boot-3",
            "source": "clear", "transcript_path": str(resumed_tf)}).json()
        check("clear starts fresh despite recoverable transcript",
              r["reused"] is False and r["session_id"] != sid1, r)

        # --- 5. reuse preserves full-read state ------------------------------
        print("full-read state survives resume:")
        session_manager.mark_full_read(sid1)
        text = build_prompt_recall(store, session_manager, project_dir,
                                   "anything at all", claude_sid="cc-boot-2")
        check("no renewed full-read nudge after resume", text != FULL_READ_NUDGE, text)

    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
