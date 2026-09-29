#!/usr/bin/env python3
"""Brief 10 regression fixtures. All data is synthetic; all temp dirs clean up.

Run directly with the repository venv Python. No local transcripts, storage,
network, JS runtime or pytest are needed.
"""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.data import GraphHistory, load_logs
from eval.followthrough import build_followthrough
from eval.transcripts import TranscriptIndex

ALPHA = "memory-alpha"
OLD_GIST = "Careful staged recall associates later actions with historical evidence only"
NEW_GIST = "Changed graph revisions preserve different distinctive prose across later requests"


def write_rows(path, rows, append=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def git(root, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    if date is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{date} +0000"
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def cc(ts, content, *, sid="claude-fixture", mid=None, role="assistant", **extra):
    return {"type": role, "sessionId": sid, "timestamp": ts, "uuid": f"{role}-{ts}-{mid}",
            "cwd": "/fixture/demo", "message": {"id": mid or f"message-{ts}", "content": content}, **extra}


def use(ts, name, args, **kwargs):
    return cc(ts, [{"type": "tool_use", "id": f"tool-{ts}", "name": name, "input": args}], **kwargs)


def cx(ts, typ, payload):
    return {"timestamp": ts, "type": typ, "payload": payload}


def direct(ts, cid, name, args):
    return cx(ts, "response_item", {"type": "function_call", "id": f"response-{cid}",
                                   "call_id": cid, "name": name, "arguments": json.dumps(args)})


def wrapper(ts, cid):
    return cx(ts, "response_item", {"type": "custom_tool_call", "call_id": cid, "name": "exec",
                                   "input": "throw new Error('fixtures must never execute JS');"})


def completed(ts, iid, kind, **fields):
    return cx(ts, "event_msg", {"type": "item_completed", "item": {"id": iid, "type": kind,
                                                                                "status": "completed", **fields}})


class FollowthroughTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kg-followthrough-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "storage"
        self.root.mkdir()
        self.claude_root = self.base / "claude"
        self.codex_home = self.base / "codex"
        self.project_graph = self.root / "projects/demo/graph.json"
        self.project_graph.parent.mkdir(parents=True)
        self.user = {"nodes": {"user-beta": {"gist": "A separate user fixture stores an unrelated observation"}}, "edges": {}}
        self.project = {"nodes": {ALPHA: {"gist": OLD_GIST, "touches": ["src/historical.py:12 (anchor)"]}}, "edges": {}}
        self.save_graphs()
        git(self.root, "init", "-q")
        self.commit(1000)
        (self.root / "unrelated").write_text("unchanged graphs")
        self.commit(2000)

    def save_graphs(self):
        (self.root / "user.json").write_text(json.dumps(self.user))
        self.project_graph.write_text(json.dumps(self.project))

    def commit(self, ts):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "synthetic snapshot", date=ts)

    def claude(self, rows, sid="claude-fixture", folder="demo"):
        path = self.claude_root / folder / f"{sid}.jsonl"
        write_rows(path, rows)
        return path

    def codex(self, rows, sid="codex-fixture"):
        path = self.codex_home / "sessions/old-date" / f"rollout-{sid}.jsonl"
        meta = cx(1000, "session_meta", {"id": sid, "session_id": sid, "cwd": "/fixture/demo", "timestamp": 1000})
        write_rows(path, [meta] + rows)
        return path

    def exposure(self, ts=1100, *, sid="claude-fixture", nodes=None, outcome=None):
        event = {"ts": ts, "kg_session": "kg-fixture", "claude_session": sid, "project": "/fixture/demo",
                 "reason": "file_recall" if outcome else "injected"}
        event["nodes" if outcome else "hits"] = nodes or [{"id": ALPHA, "level": "project", "seen": False}]
        if outcome:
            event["outcome"] = outcome
        return event

    def report(self, recall=None, useful=None, until=2500):
        write_rows(self.root / "recall.jsonl", recall or [self.exposure()])
        write_rows(self.root / "useful.jsonl", useful or [])
        rs, us = load_logs(self.root, references=True)
        return build_followthrough(GraphHistory(self.root), rs, us, {}, all_useful=us, until=until,
                                   claude_projects=self.claude_root, codex_home=self.codex_home)

    def test_post_hook_group_results_summaries_and_fork_echoes(self):
        self.claude([
            cc(1099, [{"type": "text", "text": "Already deciding."}], mid="same-response"),
            use(1110, "kg_read", {"id": ALPHA}, mid="same-response"),
            use(1120, "Edit", {"file_path": "src/historical.py"}, mid="same-response"),
            cc(1130, [{"type": "tool_result", "tool_use_id": "tool-1110", "content": ALPHA}], role="user"),
            cc(1140, [{"type": "text", "text": ALPHA}], isCompactSummary=True),
            cc(1150, [{"type": "text", "text": ALPHA}], sid="parent-fixture"),
            cc(1160, [{"type": "text", "text": ALPHA}], isSidechain=True),
            cc(1170, [{"type": "text", "text": f"<system-reminder>{ALPHA}</system-reminder>"}]),
            cc(1180, [{"type": "text", "text": f"I apply {ALPHA} here."}]),
        ])
        rep = self.report()
        detail = rep["details"][0]
        self.assertEqual(set(detail["signals"]), {"id_in_prose"})
        self.assertEqual(detail["signals"]["id_in_prose"][0]["line"], 9)
        self.assertTrue(detail["covered"])

    def test_request_cycles_and_five_minute_view(self):
        self.claude([
            cc(1200, "first human", role="user", userType="external"),
            cc(1300, "second human", role="user", origin={"kind": "human"}),
            use(1420, "kg_read", {"ids": [ALPHA]}),
            cc(1500, "third human", role="user", userType="external"),
            use(1510, "kg_put_node", {"id": ALPHA, "level": "project", "gist": "later"}),
        ])
        rep = self.report()
        detail = rep["details"][0]
        self.assertEqual(detail["until"], 1500)
        self.assertEqual(set(detail["signals"]), {"read_full"})
        self.assertEqual(detail["five_minute_signals"], {})
        self.assertEqual(next(iter(rep["summary"].values()))["five_minute"]["memory_specific"], 0)
        capped = self.report(until=1400)
        self.assertFalse(capped["details"][0]["signals"])

    def test_human_boundaries_exclude_metadata_peers_and_task_notifications(self):
        self.claude([
            cc(1150, "browser context", role="user", userType="external", isMeta=True),
            cc(1160, "peer report", role="user", userType="external", origin={"kind": "peer"}),
            cc(1170, "task finished", role="user", userType="external", origin={"kind": "task-notification"}),
            cc(1200, "first genuine request", role="user", userType="external", mid="human-a"),
            cc(1200, "second genuine request", role="user", userType="external", mid="human-b"),
            cc(1300, "third genuine request", role="user", userType="external", origin={"kind": "human"}),
            use(1250, "kg_read", {"id": ALPHA}),
            use(1310, "kg_put_node", {"id": ALPHA, "level": "project"}),
        ])
        detail = self.report()["details"][0]
        self.assertEqual(detail["until"], 1300)
        self.assertEqual(set(detail["signals"]), {"read_full"})

    def test_completion_and_prose_timestamps_respect_sensitivity_window(self):
        self.claude([
            use(1120, "Read", {"file_path": "src/historical.py"}),
            cc(1130, [{"type": "text", "text": "Beginning this response."}], mid="streamed"),
            cc(1420, [{"type": "text", "text": ALPHA}], mid="streamed"),
            cc(1450, [{"type": "tool_result", "tool_use_id": "tool-1120", "content": "file"}], role="user"),
            use(1500, "Bash", {"command": "python unobservable.py"}),
            use(1510, "kg_read", None),
        ])
        detail = self.report()["details"][0]
        self.assertEqual(detail["signals"]["touch_request"][0]["status"], "completed")
        self.assertEqual(detail["five_minute_signals"]["touch_request"][0]["status"], "requested")
        self.assertNotIn("touch_completed", detail["five_minute_signals"])
        self.assertNotIn("id_in_prose", detail["five_minute_signals"])
        self.assertIn("id_in_prose", detail["signals"])
        self.assertFalse(detail["observable"]["read_full"])
        self.assertTrue(detail["five_minute_observable"]["read_full"])
        self.assertFalse(detail["observable"]["touch_request"])
        self.assertTrue(detail["five_minute_observable"]["touch_request"])
        self.assertTrue(detail["unobservable_file_requests"])

    def test_wrapper_later_completion_is_coverage_and_thirty_minute_cap(self):
        self.codex([wrapper(1120, "future-completion"),
                    completed(1450, "future-inner", "McpToolCall", tool="kg_read", arguments={"id": ALPHA}),
                    cx(1451, "response_item", {"type": "custom_tool_call_output", "call_id": "future-completion"}),
                    direct(2901, "outside-clock-cap", "kg_put_node", {"id": ALPHA, "level": "project"})])
        detail = self.report([self.exposure(sid="codex-fixture")], until=9999)["details"][0]
        self.assertEqual(detail["until"], 2900)
        self.assertEqual(set(detail["signals"]), {"read_full"})
        self.assertEqual(detail["five_minute_signals"], {})
        self.assertFalse(detail["five_minute_observable"]["read_full"])
        self.assertEqual(detail["five_minute_unknown_operations"][0]["reason"], "inner_completion_after_window")

    def test_codex_direct_wrapped_parallel_and_ui_dedup(self):
        self.codex([
            wrapper(1099, "already-running"),
            completed(1105, "old-inner", "McpToolCall", tool="kg_put_node",
                      arguments={"id": ALPHA, "level": "project"}),
            cx(1106, "response_item", {"type": "custom_tool_call_output", "call_id": "already-running"}),
            direct(1110, "direct-read", "mcp__kg__kg_read", {"ids": [ALPHA]}),
            completed(1112, "direct-read", "McpToolCall", tool="kg_read", arguments={"ids": [ALPHA]}),
            completed(1113, "direct-read", "McpToolCall", tool="kg_read", arguments={"ids": [ALPHA]}),
            cx(1114, "response_item", {"type": "function_call_output", "call_id": "direct-read"}),
            wrapper(1120, "parallel-wrapper"),
            # Inner calls finish in reverse order; never associate by source order.
            completed(1122, "inner-second", "CommandExecution", command=["sh", "-lc", "cat src/historical.py"],
                      cwd="file:///fixture/demo", exit_code=0),
            completed(1123, "inner-first", "McpToolCall", tool="kg_put_node",
                      arguments={"id": ALPHA, "level": "project"}),
            cx(1124, "response_item", {"type": "custom_tool_call_output", "call_id": "parallel-wrapper"}),
            cx(1130, "response_item", {"type": "message", "id": "prose-one", "role": "assistant",
                                      "content": [{"type": "output_text", "text": ALPHA}]}),
            completed(1131, "prose-one", "AgentMessage", content=[{"type": "Text", "text": ALPHA}]),
        ])
        detail = self.report([self.exposure(sid="codex-fixture")])["details"][0]
        self.assertEqual(len(detail["signals"]["read_full"]), 1)
        self.assertEqual(len(detail["signals"]["node_update"]), 1)
        self.assertEqual(len(detail["signals"]["id_in_prose"]), 1)
        self.assertEqual(detail["signals"]["touch_request"][0]["request_line"], 9)
        self.assertEqual(detail["signals"]["touch_completed"][0]["status"], "completed")
        self.assertEqual(detail["signals"]["node_update"][0]["ts"], 1120)

    def test_codex_ui_only_prose_uses_native_text_kind(self):
        self.codex([completed(1120, "ui-only", "AgentMessage", content=[{"type": "Text", "text": ALPHA}]),
                    completed(1130, "reasoning", "Reasoning", content=[{"type": "Text", "text": ALPHA}]),
                    completed(1140, "summary", "AgentMessage", isSummary=True,
                              content=[{"type": "Text", "text": ALPHA}])])
        detail = self.report([self.exposure(sid="codex-fixture")])["details"][0]
        self.assertEqual(set(detail["signals"]), {"id_in_prose"})
        self.assertEqual(len(detail["signals"]["id_in_prose"]), 1)
        self.assertEqual(detail["signals"]["id_in_prose"][0]["line"], 2)

    def test_ambiguous_parallel_outer_calls_are_unknown(self):
        self.codex([wrapper(1110, "outer-a"), wrapper(1111, "outer-b"),
                    completed(1120, "unbound", "McpToolCall", tool="kg_read", arguments={"id": ALPHA}),
                    completed(1121, "bound", "McpToolCall", parent_call_id="outer-a", tool="kg_put_node",
                              arguments={"id": ALPHA, "level": "project"})])
        detail = self.report([self.exposure(sid="codex-fixture")])["details"][0]
        self.assertNotIn("read_full", detail["signals"])
        self.assertEqual(detail["unknown_operations"][0]["reason"], "unattributed_completion")
        self.assertEqual(len(detail["signals"]["node_update"]), 1)
        self.assertFalse(detail["observable"]["read_full"])

    def test_coverage_gaps_censor_rates_without_erasing_positive_counts(self):
        self.codex([wrapper(1110, "unknown-wrapper"),
                    cx(1111, "response_item", {"type": "custom_tool_call_output", "call_id": "unknown-wrapper"}),
                    direct(1120, "known-read", "kg_read", {"id": ALPHA}),
                    direct(1130, "unplaceable-file", "Read", {})])
        rep = self.report([self.exposure(sid="codex-fixture")],
                          [{"ts": 1001, "kg_session": "other", "id": "unrelated", "level": "user"}])
        cohort = rep["summary"]["prompt/codex/injected_unseen"]
        self.assertEqual(cohort["memory_specific"], 1)
        self.assertEqual(cohort["memory_specific_observable_exposures"], 0)
        self.assertIsNone(cohort["memory_specific_rate"])
        read = cohort["signals"]["read_full"]
        self.assertEqual(read["count"], 1)
        self.assertEqual(read["observable_exposures"], 0)
        self.assertIsNone(read["rate"])
        self.assertTrue(rep["details"][0]["unobservable_file_requests"])

    def test_codex_context_user_origin_and_inherited_prefix(self):
        path = self.codex([
            cx(900, "response_item", {"type": "message", "id": "parent-message", "role": "assistant",
                                     "content": [{"type": "output_text", "text": ALPHA}]}),
            cx(1110, "response_item", {"type": "message", "id": "summary", "role": "assistant",
                                      "isSummary": True, "content": [{"type": "output_text", "text": ALPHA}]}),
            cx(1120, "response_item", {"type": "function_call_output", "output": ALPHA}),
            cx(1130, "compacted", {"replacement_history": [{"role": "assistant", "content": ALPHA}]}),
            cx(1140, "response_item", {"type": "message", "id": "injection", "role": "user",
                                      "content": [{"type": "input_text", "text": ALPHA}]}),
            cx(1150, "response_item", {"type": "message", "id": "actual-user", "role": "user",
                                      "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]}}),
            direct(1160, "actual-read", "kg_read", {"id": ALPHA}),
        ])
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        rows[0]["payload"]["forked_from_id"] = "parent"
        write_rows(path, rows)
        parsed = TranscriptIndex({"codex-fixture"}, self.claude_root, self.codex_home).get("codex-fixture")
        self.assertEqual(parsed["users"], [1150])
        detail = self.report([self.exposure(sid="codex-fixture")])["details"][0]
        self.assertEqual(set(detail["signals"]), {"read_full"})

    def test_missing_ambiguous_identity_and_no_followup_are_coverage(self):
        self.claude([use(1099, "kg_read", {"id": ALPHA}, sid="quiet")], sid="quiet")
        self.claude([cc(1110, ALPHA, sid="duplicate")], sid="duplicate", folder="one")
        self.claude([cc(1110, ALPHA, sid="duplicate")], sid="duplicate", folder="two")
        self.codex([cx(1001, "session_meta", {"id": "different", "cwd": "/fixture/demo"})])
        rep = self.report([self.exposure(sid=x) for x in ("missing", "duplicate", "quiet", "codex-fixture")])
        self.assertEqual([d["transcript_status"] for d in rep["details"]],
                         ["missing_transcript", "ambiguous_transcript", "resolved", "ambiguous_identity"])
        self.assertEqual(rep["coverage"]["no_later_activity"], 1)
        for row in rep["summary"].values():
            self.assertIsNone(row["memory_specific_rate"])

    def test_resumed_rollout_append_invalidates_cache_and_partial_tail(self):
        path = self.codex([direct(1110, "first", "kg_read", {"id": ALPHA})])
        index = TranscriptIndex({"codex-fixture"}, self.claude_root, self.codex_home)
        first = index.get("codex-fixture")
        self.assertEqual(len(first["groups"]), 1)
        write_rows(path, [direct(1120, "second", "kg_put_node", {"id": ALPHA, "level": "project"})], append=True)
        raw = json.dumps(direct(1130, "third", "kg_read", {"id": ALPHA}))
        with path.open("a") as stream:
            stream.write(raw[:-1])
        second = index.get("codex-fixture")
        self.assertEqual(len(second["groups"]), 2)
        self.assertEqual(second["diagnostics"]["partial_tail"], 1)
        with path.open("a") as stream:
            stream.write("}\n")
        third = index.get("codex-fixture")
        self.assertEqual(len(third["groups"]), 3)

    def test_graph_revisions_never_use_current_gists_or_file_existence(self):
        self.project["nodes"][ALPHA] = {"gist": NEW_GIST, "touches": ["src/new.py"]}
        self.save_graphs()
        self.commit(1500)
        self.commit(2100)
        self.project["nodes"][ALPHA] = {"gist": "Uncommitted words cannot describe what was injected into older sessions",
                                       "touches": ["src/current.py"]}
        self.save_graphs()
        self.claude([
            cc(1110, [{"type": "text", "text": OLD_GIST}]),
            use(1120, "Bash", {"command": "cat src/historical.py"}),
            cc(1610, [{"type": "text", "text": NEW_GIST}]),
            use(1620, "Read", {"file_path": "src/new.py"}),
            cc(1630, [{"type": "text", "text": self.project["nodes"][ALPHA]["gist"]}]),
            use(1640, "Read", {"file_path": "src/current.py"}),
        ])
        details = self.report([self.exposure(1100), self.exposure(1600)])["details"]
        self.assertEqual([d["graph_state"] for d in details], ["approx", "known"])
        self.assertEqual(len(details[0]["signals"]["gist_phrase"]), 1)
        self.assertEqual(details[0]["signals"]["touch_request"][0]["files"], ["/fixture/demo/src/historical.py"])
        self.assertEqual(len(details[1]["signals"]["gist_phrase"]), 1)
        self.assertEqual(details[1]["signals"]["touch_request"][0]["files"], ["/fixture/demo/src/new.py"])
        # A graph without git is coverage, never a fallback for phrases/touches.
        bare = self.base / "no-history"
        bare.mkdir()
        (bare / "user.json").write_text(json.dumps(self.user))
        rs, us = load_logs(self.root, references=True)
        rep = build_followthrough(GraphHistory(bare), rs, us, {}, until=2500,
                                  claude_projects=self.claude_root, codex_home=self.codex_home)
        self.assertTrue(all(d["graph_state"] == "unavailable" for d in rep["details"]))
        self.assertTrue(all("gist_phrase" not in d["signals"] and "touch_request" not in d["signals"]
                            for d in rep["details"]))

    def test_same_id_both_levels_repeated_exposures_and_endorsement_refusals(self):
        shared = "shared-node"
        self.user["nodes"][shared] = {"gist": "The shared user principle has its own distinctive historical phrasing"}
        self.project["nodes"][shared] = {"gist": "The shared project rule names different code and particular file boundaries"}
        self.save_graphs()
        self.commit(1050)
        self.commit(2100)
        nodes = [{"id": shared, "level": level, "seen": False} for level in ("user", "project")]
        self.claude([
            use(1120, "kg_read", {"id": shared}),
            use(1130, "kg_put_node", {"id": shared, "level": "project"}),
            use(1140, "kg_useful", {"session_id": "kg-fixture", "ids": [shared, ALPHA]}),
            cc(1150, [{"type": "text", "text": shared}]),
        ])
        useful = [{"ts": 1141, "kg_session": "kg-fixture", "claude_session": "claude-fixture",
                   "id": shared, "level": "user"},
                  {"ts": 1141, "kg_session": "kg-fixture", "id": shared, "level": "project", "refused": "duplicate"},
                  {"ts": 1141, "kg_session": "other", "id": shared, "level": "project"},
                  {"ts": 1141, "kg_session": "kg-fixture", "id": ALPHA, "level": "project", "refused": "budget"}]
        rep = self.report([self.exposure(nodes=nodes), self.exposure(1110, nodes=nodes), self.exposure()], useful)
        ds = rep["details"]
        for user in (d for d in ds if d["id"] == shared and d["level"] == "user"):
            self.assertIn("read_full", user["signals"])
            self.assertIn("endorsed", user["signals"])
            self.assertNotIn("node_update", user["signals"])
        for project in (d for d in ds if d["id"] == shared and d["level"] == "project"):
            self.assertIn("node_update", project["signals"])
            self.assertNotIn("endorsed", project["signals"])
        self.assertTrue(all("id_in_prose" not in d["signals"] for d in ds if d["id"] == shared))
        alpha = next(d for d in ds if d["id"] == ALPHA)
        self.assertIn("endorsement_requested", alpha["signals"])
        self.assertNotIn("endorsed", alpha["signals"])
        summary = rep["summary"]["prompt/claude-code/injected_unseen"]
        self.assertEqual(summary["exposures"], 5)
        self.assertEqual(summary["distinct_session_nodes"], 3)
        self.assertEqual(summary["distinct_session_node_pairs"], 2)
        self.assertEqual(summary["distinct_with_followthrough"], 2)
        self.assertEqual(summary["memory_specific"], 4)

    def test_pre_hook_pending_endorsement_receipt_is_excluded(self):
        self.claude([use(1099, "kg_useful", {"session_id": "kg-fixture", "ids": [ALPHA]}),
                     cc(1110, [{"type": "tool_result", "tool_use_id": "tool-1099", "content": "accepted"}], role="user")])
        useful = [{"ts": 1105, "kg_session": "kg-fixture", "claude_session": "claude-fixture",
                   "id": ALPHA, "level": "project"}]
        detail = self.report(useful=useful)["details"][0]
        self.assertNotIn("endorsed", detail["signals"])

    def test_routes_controls_and_distinctive_phrase_collision(self):
        self.project["nodes"]["phrase-copy"] = {"gist": OLD_GIST}
        self.save_graphs()
        self.commit(1050)
        self.commit(2100)
        self.claude([cc(1120, [{"type": "text", "text": OLD_GIST}])])
        seen = [{"id": ALPHA, "level": "project", "seen": True}]
        rep = self.report([self.exposure(nodes=seen), self.exposure(outcome="injected"),
                           self.exposure(nodes=seen, outcome="all_seen"), self.exposure(outcome="throttled")])
        self.assertEqual({d["cohort"] for d in rep["details"]},
                         {"injected_seen", "injected_unseen", "withheld_all_seen", "withheld_throttled"})
        self.assertTrue(all(not d["signals"] for d in rep["details"]))

    def test_cli_opt_in_log_only_output_and_storage_unchanged(self):
        from eval.__main__ import main
        from eval.report import build_report, format_text
        self.claude([use(1120, "kg_read", {"id": ALPHA})])
        self.report()
        before = digest(self.root)
        kwargs = {"claude_projects": self.claude_root, "codex_home": self.codex_home, "until": 2500}
        plain = build_report(self.root, until=2500)
        with patch("eval.followthrough.TranscriptIndex", side_effect=AssertionError("must stay opt in")):
            self.assertEqual(build_report(self.root, **kwargs), plain)
        opt = build_report(self.root, transcripts=True, **kwargs)
        self.assertEqual({k: v for k, v in opt.items() if k != "followthrough"}, plain)
        self.assertNotIn("Activity after staged recall", format_text(plain))
        self.assertIn("Activity after staged recall", format_text(opt))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["--root", str(self.root), "--until", "2500", "--transcripts", "--json",
                       "--claude-projects", str(self.claude_root), "--codex-home", str(self.codex_home)])
        self.assertEqual(rc, 0)
        self.assertIn("followthrough", json.loads(out.getvalue()))
        self.assertEqual(digest(self.root), before)
        # The new adapters can be imported without any HTTP/live-store modules.
        code = "from eval import followthrough, transcripts, paths; import sys; assert 'mcp_http.store' not in sys.modules"
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], check=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
