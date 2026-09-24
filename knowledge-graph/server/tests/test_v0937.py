#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.37 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0937.py

Maintenance was a 25-call PASS fired by a systemd timer whose gate is a
five-way conjunction of anti-correlated terms. Measured over the 45 days
after arming: 28 firings across 12 graphs, one per graph per ~19 days,
against a staleness horizon of 14. This change re-cuts the work into CHORES
the server names and dispatches on user activity, and gives the maintenance
agent a memory of its own craft.

  HONEST STAMP. The pass was told to stamp {"last_ts": <unix now>} and an
  MCP-only agent has no clock, so it guessed — every live stamp was a round
  hour, one landed six days in the future, one a year in the past. Staleness
  is the leading term of debt, so the dispatcher's target choice ran on
  invented numbers. The server owns the stamp now.

  MAINTAIN LEVEL. A third graph holding what chores learn about gardening,
  isolated by construction: absent from read_graphs, from search, and from
  the debt survey, so a lesson can never surface as prompt recall.

  CHORE SELECTION. One category, one or two targets, chosen against three
  rules: never a node a live session holds in context, never one an earlier
  pass considered and declined, and never the same category forever.

Covers:
  1. A caller-supplied last_ts is replaced by the server clock (stamp + trail)
  2. Staleness computed from a stamped pass is ~0 regardless of what was sent
  3. maintain-level writes land in their own file
  4. The maintain graph is absent from read_graphs and from search
  5. The maintain graph is absent from the on-disk debt survey
  6. Chore kinds rank by the weight debt gives them; a clean graph yields none
  7. A rename never takes a node a live session holds; other kinds demote it
  8. Ids named in a maintain `declined` line are excluded
  9. Recent chore targets are not re-chewed
 10. Three chores of one kind rotate to the next non-empty category
 11. The prompt names targets, level and lessons, and forbids a self-supplied ts
 12. The agent reads user settings only, from the store directory, in an
     environment stripped of the session that launched the server
 13. Weekly pace funds the pass tier; each tier's gates refuse independently
 14. recently_seen_ids unions seen + preloaded for active sessions only
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE
os.environ.pop("KG_CHORES", None)

from core.chores import (  # noqa: E402
    build_chore_prompt, candidates_by_kind, pick_chore,
)
from core.constants import (  # noqa: E402
    CHORE_CONFIG_NAME, PROGRESS_TRAIL_KEY, get_storage_root, maintain_graph_path,
)
from core.debt import STALENESS_FULL_DAYS, compute_debt, survey_debt  # noqa: E402
from mcp_http import chore_dispatch  # noqa: E402
from mcp_http.chore_dispatch import weekly_pace  # noqa: E402
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


def node(nid, gist="a gist", notes=None):
    n = {"id": nid, "gist": gist}
    if notes:
        n["notes"] = notes
    return n


def chore_log():
    path = get_storage_root() / "chores.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main():
    print("=== v0.9.37 chore + maintain-memory tests ===")

    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    config = GraphConfig(save_interval=9999)   # saver thread stays asleep
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)
    sid = session_manager.register(project_dir, claude_sid="cc-chore-1")["session_id"]

    # --- 1-2. the stamp the server owns --------------------------------------
    print("honest stamp:")
    bogus = time.time() + 6 * 86400          # the live failure: six days ahead
    before = time.time()
    store.set_progress("maintain", {"last_ts": bogus, "gists_tightened": 3},
                       level="project", session_id=sid)
    after = time.time()
    stored = store.get_progress("maintain", level="project", session_id=sid)
    check("supplied last_ts replaced by server clock",
          before <= stored["last_ts"] <= after, stored.get("last_ts"))
    check("the trail copy is corrected too, not just the stamp",
          before <= stored[PROGRESS_TRAIL_KEY][-1]["last_ts"] <= after,
          stored[PROGRESS_TRAIL_KEY][-1])
    check("other stamped keys survive untouched", stored["gists_tightened"] == 3, stored)
    # The stamp must survive the project graph being lazily loaded afterwards:
    # naming the graph key without resolving it wrote into a dict the load
    # replaced, and skipped persistence for having no file behind it.
    store.read_graphs(sid)
    check("a stamp made before the graph was loaded survives the load",
          store.get_progress("maintain", level="project", session_id=sid)
              .get("gists_tightened") == 3,
          store.get_progress("maintain", level="project", session_id=sid))

    debt = compute_debt([node("a", "g")], [], stored["last_ts"], 0)
    check("a fresh stamp reads as untended ~0d", debt["untended_days"] < 0.01, debt)
    debt_never = compute_debt([node("a", "g")], [], None, 0)
    check("never maintained still saturates staleness",
          debt_never["untended_days"] == float(STALENESS_FULL_DAYS), debt_never)

    # --- 3-5. the maintain level is a graph, and an isolated one -------------
    print("maintain level:")
    store.put_node(level="maintain", node_id="rename-keeps-prompt-terms",
                   gist="A rename that drops the term prompts use costs recall.",
                   session_id=sid)
    check("lesson persisted to its own file", maintain_graph_path().exists(),
          str(maintain_graph_path()))
    disk = json.loads(maintain_graph_path().read_text())
    check("lesson is in maintain.json", "rename-keeps-prompt-terms" in disk["nodes"],
          list(disk["nodes"]))

    store.put_node(level="user", node_id="ordinary-user-node",
                   gist="A rename that drops the term prompts use costs recall.",
                   session_id=sid)
    graphs = store.read_graphs(sid)
    rendered = {n["id"] for lvl in ("user", "project") for n in graphs[lvl]["nodes"]}
    check("maintain graph absent from read_graphs",
          "rename-keeps-prompt-terms" not in rendered and "ordinary-user-node" in rendered,
          sorted(rendered))

    hits = store.search("rename drops term prompts recall costs", session_id=sid)
    found = {r["id"] for r in hits.get("top", []) + hits.get("more", [])}
    check("maintain graph absent from search (identical text, user node found)",
          "rename-keeps-prompt-terms" not in found and "ordinary-user-node" in found,
          sorted(found))

    rows = survey_debt(get_storage_root())
    check("maintain graph absent from the debt survey",
          all(r["graph"] != "maintain" for r in rows), [r["graph"] for r in rows])
    check("lesson is readable when explicitly named",
          store.read_node("rename-keeps-prompt-terms", level="maintain",
                          session_id=sid)["node"]["gist"].startswith("A rename"))
    check("maintain_lessons returns it",
          [n["id"] for n in store.maintain_lessons()] == ["rename-keeps-prompt-terms"])

    # --- 6-10. chore selection ------------------------------------------------
    print("chore selection:")
    long_gist = "x" * 400
    nodes = [
        node("wide-node-one", long_gist),
        node("wide-node-two", long_gist),
        node("an-id-that-carries-its-whole-claim-in-the-name", "short"),
        node("dated-thing-2026-08-25", "short"),
        node("lonely-node", "a reasonably substantial gist for an edge chore"),
        node("changelog-notes-node", "short",
             notes=["actually this was wrong", "turns out the fix was elsewhere"]),
    ]
    edges = [
        {"from": "wide-node-one", "to": "wide-node-two", "rel": "relates-to"},
        {"from": "an-id-that-carries-its-whole-claim-in-the-name",
         "to": "dated-thing-2026-08-25", "rel": "relates-to"},
        {"from": "changelog-notes-node", "to": "wide-node-one", "rel": "relates-to"},
    ]
    pools = candidates_by_kind(nodes, edges)
    check("every kind detected", (len(pools["gist"]) == 2 and len(pools["id"]) == 2
                                  and pools["edge"] == ["lonely-node"]
                                  and pools["notes"] == ["changelog-notes-node"]), pools)

    c = pick_chore(nodes, edges)
    check("gists lead — the weight debt itself gives them", c.kind == "gist", c)
    check("two gist targets, longest first", len(c.targets) == 2, c.targets)

    no_gists = [n for n in nodes if len(n.get("gist", "")) < 300]
    c2 = pick_chore(no_gists, edges)
    check("ids next when no gist is oversized", c2.kind == "id", c2)
    check("the dated id outranks the merely long one",
          c2.targets[0] == "dated-thing-2026-08-25", c2.targets)

    check("a clean graph yields no chore",
          pick_chore([node("tidy-node", "short")],
                     [{"from": "tidy-node", "to": "other", "rel": "r"}]) is None)

    c3 = pick_chore(no_gists, edges, in_context={"dated-thing-2026-08-25"})
    check("a rename never takes a node a live session is holding",
          c3.kind == "id" and "dated-thing-2026-08-25" not in c3.targets, c3)
    c3b = pick_chore(nodes, edges, in_context={"wide-node-one"})
    check("a gist rewrite may still take one, but takes the fresher node first",
          c3b.kind == "gist" and c3b.targets[0] == "wide-node-two", c3b)
    only_seen = pick_chore([node("lonely-node", "a substantial gist here")], [],
                           in_context={"lonely-node"})
    check("an edge chore is not blocked by context at all — adding one is invisible",
          only_seen is not None and only_seen.kind == "edge", only_seen)

    declined_trail = [{"declined": ["merging dated-thing-2026-08-25 into its sibling "
                                    "was weighed and refused — different subjects"]}]
    c4 = pick_chore(no_gists, edges, maintain_trail=declined_trail)
    check("a declined id is excluded, the category is not",
          c4.kind == "id" and "dated-thing-2026-08-25" not in c4.targets, c4)

    recent = [{"kind": "gist", "targets": ["wide-node-one", "wide-node-two"]}]
    c5 = pick_chore(nodes, edges, chore_trail=recent)
    check("recent chore targets are not re-chewed", c5.kind != "gist", c5)

    run = [{"kind": "gist", "targets": []}] * 3
    c6 = pick_chore(nodes, edges, chore_trail=run)
    check("three of a kind rotates to the next non-empty category",
          c6.kind != "gist", c6)

    # --- 11. the prompt -------------------------------------------------------
    print("chore prompt:")
    c.level = "project"
    prompt = build_chore_prompt(c, project_dir, lessons=store.maintain_lessons())
    check("names its targets", all(t in prompt for t in c.targets), prompt[:200])
    check("names the level for every write", 'level="project"' in prompt)
    check("carries the lessons inline", "rename-keeps-prompt-terms" in prompt)
    check("forbids a self-supplied timestamp",
          "you have no clock" in prompt.lower())
    check("reads only its targets, never the whole graph",
          "Do NOT read the" in prompt and f'kg_read(cwd="{project_dir}", ids=[' in prompt)

    # --- 12. weekly pace, and the two tiers -----------------------------------
    print("weekly economics:")
    week = 7 * 86400
    def gauge(pct7, days_elapsed, pct5=10):
        return {"five_hour_pct": pct5, "seven_day_pct": pct7,
                "seven_day_resets_at": time.time() + week - days_elapsed * 86400,
                "updated_at": time.time()}
    check("a week burning above the linear line has no surplus",
          weekly_pace(gauge(60, 3.5), time.time()) > 1.0,
          weekly_pace(gauge(60, 3.5), time.time()))
    check("a quiet week is under pace and can fund a pass",
          weekly_pace(gauge(22, 3.4), time.time()) < 1.0,
          weekly_pace(gauge(22, 3.4), time.time()))
    check("the same usage reads as more surplus later in the week",
          weekly_pace(gauge(40, 6.0), time.time())
          < weekly_pace(gauge(40, 2.0), time.time()))
    check("the first hours of a window give no usable reading",
          weekly_pace(gauge(1, 0.05), time.time()) is None)

    print("dispatch gates:")
    # Enough graph to be worth a pass (PASS_MIN_ACTIVE_NODES) and enough wear
    # to be worth a chore.
    for i in range(9):
        store.put_node(level="user", node_id=f"oversized-node-{i}",
                       gist="y" * 400, session_id=sid)
    cfg_path = get_storage_root() / CHORE_CONFIG_NAME
    gauge_path = get_storage_root() / "gauge.json"
    if cfg_path.exists():
        cfg_path.unlink()
    chore_dispatch._config_cache["mtime"] = "force-reread"
    check("off unless switched on", chore_dispatch.enabled() is False)
    chore_dispatch.maybe_dispatch(store, session_manager, project_dir)
    check("a disabled dispatcher logs nothing at all", chore_log() == [], chore_log())

    def configure(**over):
        cfg = {"enabled": True, "limits": str(gauge_path),
               "claude_bin": "/nonexistent/claude", "min_interval_s": 0}
        cfg.update(over)
        cfg_path.write_text(json.dumps(cfg))
        chore_dispatch._config_cache["mtime"] = "force-reread"

    configure(limits=str(get_storage_root() / "no-such-gauge.json"))
    check("config file switches it on", chore_dispatch.enabled() is True)
    chore_dispatch.maybe_dispatch(store, session_manager, project_dir)
    log = chore_log()
    check("an unreadable gauge refuses, and says which gate refused",
          len(log) == 1 and log[0]["event"] == "skip"
          and "gauge" in log[0]["reason"], log)

    # The user graph has never had a stamped pass, so the pass tier claims it.
    print("pass tier:")
    gauge_path.write_text(json.dumps(gauge(60, 3.5, pct5=10)))
    configure()
    chore_dispatch.maybe_dispatch(store, session_manager, project_dir)
    log = chore_log()
    check("a week at full burn refuses the pass — no surplus to spend",
          log[-1]["event"] == "skip" and "pace" in log[-1]["reason"], log[-1])

    gauge_path.write_text(json.dumps(gauge(95, 6.8, pct5=10)))
    configure()
    chore_dispatch.maybe_dispatch(store, session_manager, project_dir)
    check("the absolute backstop refuses a nearly spent week the line would wave through",
          chore_log()[-1]["reason"].startswith("pass: 7d"), chore_log()[-1])
    check("a too-small graph is never worth a pass",
          chore_dispatch._pick_target(
              store, session_manager, {}, {}, time.time(),
              [("project", "project:nope", None)])[0] is None)

    gauge_path.write_text(json.dumps(gauge(22, 3.4, pct5=50)))
    configure()
    chore_dispatch.maybe_dispatch(store, session_manager, project_dir)
    check("a pass keeps further from the 5h ceiling than a chore does",
          chore_log()[-1]["reason"] == "pass: 5h 50%", chore_log()[-1])

    gauge_path.write_text(json.dumps(gauge(22, 3.4, pct5=10)))
    configure()
    chore_dispatch.maybe_dispatch(store, session_manager, project_dir)
    log = chore_log()
    check("an overdue graph plus weekly surplus reaches the launch as a PASS",
          log[-1]["reason"].startswith("claude binary") and log[-1]["tier"] == "pass",
          log[-1])

    # Passed ten days ago: no longer pass-due (21d), but stale enough to carry
    # debt. A graph passed seconds ago correctly has neither.
    print("chore tier:")
    store.set_progress("maintain", {"gists_tightened": 0}, level="user")
    store._progress["user"]["maintain"]["last_ts"] = time.time() - 10 * 86400
    chore_dispatch.maybe_dispatch(store, session_manager, project_dir)
    log = chore_log()
    check("a freshly passed graph falls back to the chore tier",
          log[-1].get("tier") == "chore"
          and log[-1]["reason"].startswith("claude binary"),
          log[-1])

    gauge_path.write_text(json.dumps(gauge(22, 3.4, pct5=99)))
    configure()
    chore_dispatch.maybe_dispatch(store, session_manager, project_dir)
    check("a busy 5h window refuses the chore while the user works",
          "5h" in chore_log()[-1]["reason"], chore_log()[-1])

    # --- 13. what a live session is holding -----------------------------------
    print("live context:")
    session_manager.mark_seen(sid, ["seen-a", "seen-b"], via="search")
    session_manager.set_preloaded(sid, ["preloaded-c"])
    live = session_manager.recently_seen_ids(3600)
    check("seen and preloaded ids both count as in-context",
          {"seen-a", "seen-b", "preloaded-c"} <= live, live)
    stale_sid = session_manager.register(project_dir)["session_id"]
    session_manager.mark_seen(stale_sid, ["stale-node"], via="search")
    session_manager._sessions[stale_sid]["last_activity"] = time.time() - 86400
    check("a session idle past the window no longer protects its nodes",
          "stale-node" not in session_manager.recently_seen_ids(3600))

    # --- 14. a chore is system-wide: it wears nothing random -------------------
    # A project's .claude/ may route ANTHROPIC_BASE_URL elsewhere or add
    # hooks, permissions and MCP servers; the server's own environment may
    # carry the identity of the session that launched it. Neither reaches
    # the agent.
    print("isolation:")
    cmd = chore_dispatch.chore_command("/usr/bin/claude", "claude-sonnet-5", "/x/settings.json")
    i = cmd.index("--setting-sources")
    check("the agent reads USER settings only — never the project's",
          cmd[i + 1] == "user", cmd)
    check("the explicit allowlist still rides on top", "--settings" in cmd
          and cmd[cmd.index("--settings") + 1] == "/x/settings.json", cmd)
    launcher = {"PATH": "/usr/bin", "HOME": "/home/u", "ANTHROPIC_API_KEY": "k",
                "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
                "CLAUDE_CODE_SESSION_ID": "dead-session", "CLAUDE_CODE_CHILD_SESSION": "1",
                "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_CODE_BRIDGE_SESSION_ID": "b",
                "CLAUDECODE": "1", "CLAUDE_PID": "123", "CLAUDE_EFFORT": "xhigh",
                "AI_AGENT": "claude-code_agent"}
    env = chore_dispatch.chore_env(launcher)
    check("the launching session's identity is stripped",
          not any(k.startswith("CLAUDE_CODE_") or k in
                  ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT") for k in env), env)
    check("what the shell legitimately provides survives",
          env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u"
          and env["ANTHROPIC_API_KEY"] == "k", env)
    check("the plugin's own hooks are told to stand down", env.get("KG_CHORE") == "1", env)
    check("the launcher's copy of a user setting goes too — user settings re-apply it",
          "CLAUDE_CODE_ENABLE_TELEMETRY" not in env, env)

    store.shutdown()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
