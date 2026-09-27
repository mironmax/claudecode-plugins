#!/usr/bin/env python3
"""Tests for the harness seam: runners, the Codex gauge, harness profiles.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_harness.py

Covers:
  1. detection: transcript paths and MCP User-Agents name the harness
  2. the Codex gauge: the newest rate-limit event across active rollouts, mapped
     to gauge keys by window length; a passed reset reads as empty
  3. runners: the Claude command is unchanged; the Codex command has no
     shell, no web and only the tier's kg tools, pre-approved; selection
     prefers Claude, pins a configured binary, falls back to Codex
  4. end to end: a dispatch through a fake codex binary gets the prompt on
     stdin and logs the runner
  5. the Codex preload fits its smaller budget; the session records its harness
  6. apply_patch paths and Codex shell reads reach recall and capture
  7. kg_read from a Codex client with no hooks says how to turn them on,
     once hooks are seen it does not
"""

import asyncio
import json
import os
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE
os.environ.pop("KG_CHORES", None)
_CODEX_HOME = Path(tempfile.mkdtemp(prefix="kg-test-codex-"))
os.environ["CODEX_HOME"] = str(_CODEX_HOME)

from core.constants import BOOTSTRAP_CHAR_BUDGET, CHORE_LOG_NAME, CODEX_BOOTSTRAP_CHAR_BUDGET  # noqa: E402
from mcp_http import chore_dispatch as cd  # noqa: E402
from mcp_http import harness  # noqa: E402
from mcp_http.file_recall import file_targets, patch_files  # noqa: E402
from mcp_http.read_format import build_bootstrap  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402
from mcp_http.store import GraphConfig, MultiProjectGraphStore  # noqa: E402

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


def rollout(day: str, name: str, events: list, mtime: float | None = None) -> Path:
    d = _CODEX_HOME / "sessions" / day
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-{name}.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in events))
    if mtime:
        os.utime(f, (mtime, mtime))
    return f


def limits_event(p5, p7, r5, r7, ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat()
    return {"timestamp": ts, "type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {
            "primary": {"used_percent": p5, "window_minutes": 300, "resets_at": r5},
            "secondary": {"used_percent": p7, "window_minutes": 10080, "resets_at": r7},
            "plan_type": "plus"}}}


def test_detection():
    print("1. detection")
    check("a Codex rollout transcript is Codex",
          harness.from_transcript("/home/u/.codex/sessions/2026/09/27/rollout-x.jsonl") == harness.CODEX)
    check("a relocated CODEX_HOME is still Codex by file name",
          harness.from_transcript("/data/cx/sessions/rollout-2026-x.jsonl") == harness.CODEX)
    check("a Claude Code transcript is Claude Code",
          harness.from_transcript("/home/u/.claude/projects/p/abc.jsonl") == harness.CLAUDE_CODE)
    check("no transcript defaults to Claude Code", harness.from_transcript(None) == harness.CLAUDE_CODE)
    check("Codex's MCP client is Codex",
          harness.from_user_agent("codex-mcp-client/0.157.1") == harness.CODEX)
    check("any other client is Claude Code",
          harness.from_user_agent("claude-code/2.1.202") == harness.CLAUDE_CODE
          and harness.from_user_agent(None) == harness.CLAUDE_CODE)


def test_codex_gauge():
    print("2. Codex gauge")
    now = time.time()
    stamp = lambda age: datetime.fromtimestamp(now - age, timezone.utc).isoformat()
    rollout("2026/09/25", "old", [limits_event(90, 90, now + 900, now + 9000, stamp(7200))], mtime=now - 7200)
    newest = rollout("2026/09/27", "new", [
        {"type": "response_item", "payload": {"role": "user"}},
        limits_event(10, 20, now + 1000, now + 500000, stamp(90)),
        limits_event(12, 21, now + 1000, now + 500000, stamp(60)),
        {"type": "event_msg", "payload": {"type": "agent_message"}},
    ], mtime=now - 60)
    data = cd.RUNNERS["codex"].gauge({}, now)
    check("the last reading wins, mapped by window length",
          data["five_hour_pct"] == 12 and data["seven_day_pct"] == 21
          and data["seven_day_resets_at"] == now + 500000, data)
    expected = datetime.fromisoformat(stamp(60)).timestamp()
    check("its timestamp is the event's own", data["updated_at"] == expected, data)
    reading, raw, err = cd._gauge_read({"gauge_max_age_s": 10 ** 10}, now, cd.RUNNERS["codex"])
    check("the shared gates read it like the Claude gauge", not err and reading["5h"] == 12, (reading, err))
    passed = rollout("2026/09/27", "reset", [limits_event(80, 30, now - 5, now + 1000, stamp(1))], mtime=now)
    check("a window whose reset has passed reads as empty",
          cd.codex_limits(passed)["five_hour_pct"] == 0.0)
    empty = rollout("2026/09/27", "empty", [{"type": "event_msg", "payload": {}}], mtime=now + 5)
    check("a rollout with no reading is no reading", cd.codex_limits(empty) is None)
    _, raw, err = cd._gauge_read({}, now, cd.RUNNERS["codex"])
    check("an empty new rollout does not hide a fresh reading", not err and raw["five_hour_pct"] == 0, err)
    empty.unlink()


def test_runners():
    print("3. runners")
    chore_settings = cd._settings_path({}, "chore")
    pass_settings = cd._settings_path({}, "pass")
    job = {"tier": "chore", "bin": "/usr/bin/claude", "settings": chore_settings}
    check("the Claude command is the one chores always ran",
          cd.RUNNERS["claude"].command({}, job)
          == cd.chore_command("/usr/bin/claude", cd.CHORE_MODEL, chore_settings))
    chore_tools = cd.allowed_tools(chore_settings)
    pass_tools = cd.allowed_tools(pass_settings)
    check("tool names come from the shipped allowlists, bare",
          "kg_read" in chore_tools and all(not t.startswith("mcp__") for t in chore_tools))
    check("a chore may not delete; a pass may",
          "kg_delete_node" not in chore_tools and "kg_delete_node" in pass_tools)
    cmd = cd.RUNNERS["codex"].command({}, {**job, "bin": "/usr/bin/codex"})
    joined = " ".join(cmd)
    check("Codex runs exec, ephemeral, without the user's config",
          cmd[:4] == ["/usr/bin/codex", "exec", "--ephemeral", "--ignore-user-config"], cmd[:4])
    check("no shell, no web, read-only sandbox",
          "--disable shell_tool" in joined and 'web_search="disabled"' in joined and "-s read-only" in joined)
    check("only the tier's kg tools, pre-approved",
          f"mcp_servers.kg.enabled_tools={json.dumps(chore_tools)}" in cmd
          and 'mcp_servers.kg.default_tools_approval_mode="approve"' in cmd)
    check("low effort for a chore, no model unless configured",
          'model_reasoning_effort="low"' in cmd and "-m" not in cmd)
    pcmd = cd.RUNNERS["codex"].command({"codex_model": "gpt-5.5"},
                                       {"tier": "pass", "bin": "/usr/bin/codex", "settings": pass_settings})
    check("medium effort for a pass; a configured model is passed",
          'model_reasoning_effort="medium"' in pcmd and pcmd[-2:] == ["-m", "gpt-5.5"])
    check("an explicit runner wins", cd._runner({"runner": "codex"}).name == "codex")
    check("a configured Claude binary pins Claude even when missing",
          cd._runner({"claude_bin": "/nonexistent/claude"}).name == "claude")
    claude = cd.RUNNERS["claude"]
    real = claude.binary
    claude.binary = lambda cfg: None
    try:
        check("auto falls back to Codex when Claude Code is absent",
              cd._runner({"codex_bin": "/usr/bin/codex"}).name == "codex")
    finally:
        del claude.binary
    check("the restored runner is the class's own", claude.binary.__func__ is real.__func__)


def test_codex_dispatch():
    print("4. dispatch through Codex")
    tmp = Path(tempfile.mkdtemp(prefix="kg-test-fakecodex-"))
    argv_file, stdin_file = tmp / "argv", tmp / "stdin"
    fake = tmp / "codex"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {argv_file}\ncat > {stdin_file}\n'
                    f'printf "%s" "$KG_CHORE" > {tmp / "env"}\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    saved = {k: getattr(cd, k) for k in ("_config", "_pick_target", "_chore_gauge_ok",
                                         "_gauge_read", "build_chore_prompt")}
    from types import SimpleNamespace
    payload = SimpleNamespace(level="user", graph="user", project_path=None, kind="edge",
                              targets=["n1"], debt=1.0, pool="p", context={})
    cd._config = lambda: {"enabled": True, "runner": "codex", "codex_bin": str(fake),
                          "min_interval_s": 0}
    cd._gauge_read = lambda cfg, now, runner: ({"5h": 1}, {"five_hour_pct": 1}, "")
    cd._pick_target = lambda *a: ("chore", payload)
    cd._chore_gauge_ok = lambda cfg, raw: ""
    cd.build_chore_prompt = lambda *a, **k: "one edge chore on n1"

    class Store:
        def maintain_lessons(self):
            return []
    try:
        cd.maybe_dispatch(Store(), None, None)
        deadline = time.time() + 10
        while cd._running.is_set() and time.time() < deadline:
            time.sleep(0.05)
    finally:
        for k, v in saved.items():
            setattr(cd, k, v)
    argv = argv_file.read_text().split("\n") if argv_file.exists() else []
    check("the fake codex ran as exec", argv[:1] == ["exec"], argv[:3])
    check("the prompt arrived on stdin", "edge" in stdin_file.read_text().lower()
          if stdin_file.exists() else False)
    check("the run carries KG_CHORE, silencing the plugin's own hooks",
          (tmp / "env").read_text() == "1" if (tmp / "env").exists() else False)
    log = [json.loads(line) for line in open(Path(_TMP_STORAGE) / CHORE_LOG_NAME)]
    check("dispatch and done both name the runner",
          any(r.get("event") == "dispatch" and r.get("runner") == "codex" for r in log)
          and any(r.get("event") == "done" and r.get("runner") == "codex" for r in log),
          [(r.get("event"), r.get("runner")) for r in log])


def test_preload_budget():
    print("5. preload budget and session harness")
    sm = HTTPSessionManager()
    store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm)
    sid = sm.register(None)["session_id"]
    for i in range(160):
        store.put_node("user", f"budget-node-{i:03d}",
                       f"a gist long enough to fill the preload quickly, number {i} " * 3,
                       session_id=sid)
    graphs, scores = store.read_graphs(sid), store.scores_for_read(sid)
    claude = build_bootstrap(graphs, scores, sid)
    codex = build_bootstrap(graphs, scores, sid, budget=CODEX_BOOTSTRAP_CHAR_BUDGET)
    check("the Codex preload fits its budget", len(codex["context"]) <= CODEX_BOOTSTRAP_CHAR_BUDGET,
          len(codex["context"]))
    check("the Claude preload keeps its own",
          CODEX_BOOTSTRAP_CHAR_BUDGET < len(claude["context"]) <= BOOTSTRAP_CHAR_BUDGET,
          len(claude["context"]))
    kid = sm.register(None, claude_sid="codex-thread", harness=harness.CODEX)["session_id"]
    check("a session records the harness that registered it",
          sm.lookup(kid).get("harness") == harness.CODEX)
    store.shutdown()


def test_codex_tools():
    print("6. apply_patch and shell reads")
    patch = ("*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n"
             "*** Add File: docs/new.md\n+hi\n*** Delete File: old.txt\n"
             "*** Update File: src/b.py\n*** Move to: src/c.py\n*** End Patch\n")
    check("every file a patch names, resolved against cwd",
          patch_files(patch, "/p") == ["/p/src/a.py", "/p/docs/new.md", "/p/old.txt",
                                       "/p/src/b.py", "/p/src/c.py"])
    check("apply_patch reaches file recall",
          file_targets("apply_patch", {"command": patch}, "/p")[:1] == ["/p/src/a.py"])
    check("a non-string patch touches nothing", patch_files(None, "/p") == [])

    from mcp_http import ambient
    home_proj = Path(tempfile.mkdtemp(prefix="kg-test-proj-", dir=Path.home() / ".cache"))
    target = home_proj / "notes.md"
    target.write_text("hello\n")
    sm = HTTPSessionManager()
    store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm)
    sm.register(str(home_proj))

    def event(transcript):
        return {"cwd": str(home_proj), "tool_name": "Bash", "session_id": "s",
                "transcript_path": transcript,
                "tool_input": {"command": f"cat {target}"}}
    events_file = ambient._events_path(str(home_proj.resolve()))
    ambient.handle_tool_event(store, sm, event("/h/.claude/projects/x/s.jsonl"))
    before = json.loads(events_file.read_text()) if events_file.exists() else {}
    check("a Claude Code shell read is not counted as a read",
          not any(k.startswith("read:") for k in json.dumps(before).split('"')))
    ambient.handle_tool_event(store, sm, event("/h/.codex/sessions/2026/09/27/rollout-s.jsonl"))
    after = events_file.read_text() if events_file.exists() else ""
    check("a Codex shell read is counted as a read", "read:notes.md" in after, after[:300])
    store.shutdown()


def test_no_hooks_hint():
    print("7. no-hooks hint")
    from types import SimpleNamespace
    import mcp.types as types
    import mcp_streamable_server as srv
    sm = HTTPSessionManager()
    store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm)
    srv.store, srv.session_manager = store, sm
    handler = srv.create_mcp_server().get_request_handler("tools/call").handler

    def kg_read(cwd, user_agent):
        ctx = SimpleNamespace(request=SimpleNamespace(headers={"user-agent": user_agent}))
        params = types.CallToolRequestParams(name="kg_read", arguments={"cwd": cwd})
        return asyncio.run(handler(ctx, params)).content[0].text

    proj = str(Path(tempfile.mkdtemp(prefix="kg-test-proj-", dir=Path.home() / ".cache")).resolve())
    codex_ua = "codex-mcp-client/0.157.1"
    check("a Codex kg_read with no hooks says how to turn them on",
          "/hooks" in kg_read(proj, codex_ua))
    check("a Claude Code kg_read never does", "/hooks" not in kg_read(proj, "claude-code/2.1"))
    sid = sm.register(proj, claude_sid="codex-t", harness=harness.CODEX)["session_id"]
    sm.set_preloaded(sid, [])
    check("once a Codex hook has preloaded here, no hint", "/hooks" not in kg_read(proj, codex_ua))
    store.shutdown()


def main():
    test_detection()
    test_codex_gauge()
    test_runners()
    test_codex_dispatch()
    test_preload_budget()
    test_codex_tools()
    test_no_hooks_hint()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
