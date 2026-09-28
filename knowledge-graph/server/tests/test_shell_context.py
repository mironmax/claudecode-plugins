#!/usr/bin/env python3
"""Exact-call shell recall: real rollout shape, fail-closed resolution, REST.

Run directly with the server venv. All storage and project files are temporary;
no live KG state or server is used. The live 0.158 probe established the record
shape; these tests pin deterministic edge cases, not transcript timing.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_storage = tempfile.TemporaryDirectory(prefix="kg-shell-storage-")
os.environ["KG_STORAGE_ROOT"] = _storage.name

from fastapi.testclient import TestClient
from mcp_http import file_recall, shell_context
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager


def completion(call_id, cwd, *, thread="thread", turn="turn", status="completed"):
    return {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": thread,
            "turn_id": turn, "item": {"type": "CommandExecution", "id": call_id,
            "cwd": cwd, "status": status}}}


class Files(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="kg-shell-project-", dir=Path.home() / ".cache")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        for name in ("a", "b"):
            (self.root / name).mkdir()
            (self.root / name / "same.py").write_text(name + "\n")
        (self.root / "same.py").write_text("wrong-root\n")
        self.transcript = self.root / "rollout-test.jsonl"
        self.payload = {"cwd": str(self.root), "tool_name": "Bash", "session_id": "thread",
                        "turn_id": "turn", "tool_use_id": "exec-a",
                        "transcript_path": str(self.transcript),
                        "tool_input": {"command": "cat same.py"}}
        with shell_context._cache_lock:
            shell_context._cache.clear()

    def write_records(self, *records):
        self.transcript.write_text("".join(json.dumps(r) + "\n" for r in records))

    def resolve(self, **updates):
        return shell_context.resolve_shell_context(dict(self.payload, **updates), shell_cwd_known=False)


class ResolutionTests(Files):
    def test_parallel_duplicate_commands_resolve_by_id_not_order(self):
        self.write_records(completion("exec-b", (self.root / "b").as_uri()),
                           completion("exec-a", (self.root / "a").as_uri()))
        self.assertEqual(self.resolve().cwd, str(self.root / "a"))
        self.assertEqual(self.resolve(tool_use_id="exec-b").cwd, str(self.root / "b"))

    def test_session_cwd_is_only_used_by_a_profile_that_knows_it(self):
        self.assertIsNone(self.resolve().cwd)
        known = shell_context.resolve_shell_context(self.payload, shell_cwd_known=True)
        self.assertEqual(known.cwd, str(self.root))
        self.assertEqual(known.source, "hook_cwd")

    def test_explicit_workdir_precedes_transcript(self):
        got = self.resolve(tool_input={"command": "cat same.py", "workdir": str(self.root / "b")})
        self.assertEqual(got.cwd, str(self.root / "b"))
        self.assertEqual(got.source, "explicit_workdir")
        for bad in ("relative", 42, "/bad\x00path"):
            self.assertIsNone(self.resolve(tool_input={"workdir": bad}).cwd)

    def test_omitted_workdir_uses_only_correlated_actual_cwd(self):
        self.write_records(completion("exec-a", self.root.as_uri()))
        self.assertEqual(self.resolve().cwd, str(self.root))
        self.assertEqual(self.resolve().source, "transcript")

    def test_uri_decoding_and_local_paths(self):
        for value, expected in [("file:///tmp/a%20b", "/tmp/a b"),
                                ("file://localhost/tmp/a", "/tmp/a"), ("/tmp/plain", "/tmp/plain")]:
            with self.subTest(value=value):
                self.write_records(completion("exec-a", value))
                self.assertEqual(self.resolve().cwd, expected)
        for bad in ("file://remote/tmp/a", "file:///tmp/a?query", "file:///tmp/a#frag",
                    "file:///tmp/%00", "file:///tmp/%FF", "https://host/a", "relative", None):
            with self.subTest(bad=bad):
                self.write_records(completion("exec-a", bad))
                self.assertIsNone(self.resolve().cwd)

    def test_missing_id_file_and_record_never_borrow(self):
        self.write_records(completion("exec-other", self.root.as_uri()))
        for updates in ({"tool_use_id": None}, {"session_id": None}, {"transcript_path": None},
                        {"transcript_path": str(self.root / "missing.jsonl")}, {}):
            with self.subTest(updates=updates):
                self.assertIsNone(self.resolve(**updates).cwd)

    def test_wrong_session_or_turn_is_rejected(self):
        for updates in ({"thread": "other"}, {"turn": "other"}, {"thread": None}):
            self.write_records(completion("exec-a", self.root.as_uri(), **updates))
            self.assertEqual(self.resolve().reason, "identity_mismatch")

    def test_duplicate_ids_are_ambiguous_even_if_identical(self):
        a = completion("exec-a", self.root.as_uri())
        for b in (a, completion("exec-a", (self.root / "b").as_uri())):
            self.write_records(a, b)
            self.assertEqual(self.resolve().reason, "ambiguous_call")
            self.assertIsNone(self.resolve().cwd)

    def test_failed_commands_match_but_incomplete_items_do_not(self):
        self.write_records(completion("exec-a", self.root.as_uri(), status="failed"))
        self.assertEqual(self.resolve().cwd, str(self.root))
        self.write_records(completion("exec-a", self.root.as_uri(), status="inProgress"))
        self.assertIsNone(self.resolve().cwd)

    def test_wrapper_arguments_and_result_echo_are_not_evidence(self):
        self.write_records({"type": "response_item", "payload": {"type": "function_call",
            "call_id": "exec-a", "arguments": '{"workdir":"/fake"}'}},
            {"type": "response_item", "payload": {"type": "function_call_output",
             "output": completion("exec-a", self.root.as_uri())}})
        self.assertEqual(self.resolve().reason, "missing_record")

    def test_truncated_and_malformed_records_fail_closed(self):
        for data in ('{"type":', json.dumps(completion("exec-a", self.root.as_uri())), "junk\n"):
            self.transcript.write_text(data)
            self.assertIsNone(self.resolve().cwd)

    def test_yielded_call_is_found_only_after_its_complete_record_is_appended(self):
        self.write_records({"type": "event_msg", "payload": {"type": "item_started"}})
        self.assertEqual(self.resolve().reason, "missing_record")
        row = json.dumps(completion("exec-a", (self.root / "a").as_uri()))
        with self.transcript.open("a") as f:
            f.write(row[:30])
        self.assertIsNone(self.resolve().cwd)
        with self.transcript.open("a") as f:
            f.write(row[30:] + "\n")
        self.assertEqual(self.resolve().cwd, str(self.root / "a"))

    def test_byte_and_record_caps(self):
        target = json.dumps(completion("exec-a", self.root.as_uri())) + "\n"
        padding = json.dumps({"padding": "x" * shell_context.MAX_BYTES}) + "\n"
        self.transcript.write_text(target + padding)
        self.assertIsNone(self.resolve().cwd)
        self.transcript.write_text(padding + target)
        self.assertEqual(self.resolve().cwd, str(self.root))
        self.transcript.write_text("{}\n" * (shell_context.MAX_RECORDS + 1) + target)
        self.assertEqual(self.resolve().reason, "record_limit")

    def test_time_budget_fails_closed_even_on_cached_result(self):
        self.write_records(completion("exec-a", self.root.as_uri()))
        self.assertIsNotNone(self.resolve().cwd)
        with patch.object(shell_context, "MAX_SECONDS", 0):
            self.assertEqual(self.resolve().reason, "time_limit")
            self.assertIsNone(self.resolve().cwd)

    def test_cache_reused_but_append_rewrite_and_replacement_invalidate(self):
        self.write_records(completion("exec-a", self.root.as_uri()))
        self.assertIsNotNone(self.resolve().cwd)
        with patch.object(shell_context.json, "loads", side_effect=AssertionError("reparsed")):
            self.assertIsNotNone(self.resolve().cwd)
        with self.transcript.open("a") as f:
            f.write(json.dumps(completion("exec-b", (self.root / "b").as_uri())) + "\n")
        self.assertEqual(self.resolve(tool_use_id="exec-b").cwd, str(self.root / "b"))
        self.write_records(completion("exec-a", (self.root / "a").as_uri()))
        self.assertEqual(self.resolve().cwd, str(self.root / "a"))
        self.transcript.unlink()
        self.write_records(completion("exec-a", (self.root / "b").as_uri()))
        self.assertEqual(self.resolve().cwd, str(self.root / "b"))

    def test_snapshot_cache_is_bounded(self):
        for i in range(shell_context.MAX_SNAPSHOTS + 3):
            self.write_records(completion("exec-a", f"/tmp/{i}"))
            self.resolve()
        self.assertLessEqual(len(shell_context._cache), shell_context.MAX_SNAPSHOTS)

    def test_outside_home_and_non_regular_transcripts_are_rejected(self):
        self.assertIsNone(self.resolve(transcript_path="/etc/passwd").cwd)
        self.transcript.mkdir()
        self.assertIsNone(self.resolve().cwd)
        self.transcript.rmdir()
        os.mkfifo(self.transcript)
        self.assertIsNone(self.resolve().cwd)  # O_NONBLOCK: cannot hang on a FIFO


class OperandTests(Files):
    def test_nl_and_rg_explicit_operands(self):
        expected = [str(self.root / "same.py")]
        for cmd in ("nl -ba same.py", "nl --body-numbering=a --number-width 4 same.py",
                    "nl -s ' ' same.py", "rg needle same.py", "rg -n -e needle same.py",
                    "rg --glob '*.py' --max-count 1 needle same.py", "rg -nF needle -- same.py",
                    "rg --regexp=needle same.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(file_recall.bash_read_files(cmd, str(self.root)), expected)

    def test_rg_never_expands_a_directory_or_infers_an_implicit_file(self):
        for cmd in ("rg same.py", "rg needle .", "rg --files same.py", "rg --type-list",
                    "rg --pre cat needle same.py", "rg --unknown same.py", "rg -e",
                    "nl --unknown same.py", "rg needle *.py", "cd b; rg needle same.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(file_recall.bash_read_files(cmd, str(self.root)), [])


class HookTests(Files):
    def setUp(self):
        super().setUp()
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)
        self.sid = self.sm.register(str(self.root), claude_sid="thread")["session_id"]
        self.client = TestClient(create_rest_api(self.store, self.sm, ConnectionManager(), "test"))
        self.addCleanup(self.client.close)
        file_recall.reset_throttle()
        for suffix, touched in (("root", "same.py"), ("a", "a/same.py"), ("b", "b/same.py")):
            self.store.put_node("project", f"memory-{suffix}", f"Memory of {suffix}",
                                touches=[touched], session_id=self.sid)

    def post(self, **updates):
        return self.client.post("/api/tool_event", json=dict(self.payload, **updates)).json()

    def last_log(self):
        return json.loads((Path(_storage.name) / "recall.jsonl").read_text().splitlines()[-1])

    def test_rest_recalls_execution_file_never_the_same_named_root_file(self):
        self.write_records(completion("exec-a", (self.root / "a").as_uri()))
        response = json.dumps(self.post())
        self.assertIn("memory-a", response)
        self.assertNotIn("memory-root", response)
        self.assertNotIn("memory-b", response)
        log = self.last_log()
        self.assertEqual(log["cwd_source"], "transcript")
        self.assertEqual(log["cwd_reason"], "matched")
        self.assertEqual(self.sm.lookup(self.sid)["project_path"], str(self.root))
        self.assertNotIn("command", log)
        self.assertNotIn("transcript_path", log)

    def test_no_record_no_relative_recall_and_reason_logged(self):
        self.assertEqual(self.post(), {})
        self.assertEqual(self.last_log()["outcome"], "unresolved_cwd")
        self.assertNotIn("memory-root", self.sm.get_seen(self.sid))

    def test_user_only_scope_stays_user_only_with_a_verified_execution_directory(self):
        sid = self.sm.register(None, claude_sid="user-thread")["session_id"]
        self.store.put_node("user", "user-file-memory", "User-level fixture memory",
                            touches=[str(self.root / "a/same.py")], session_id=sid)
        self.write_records(completion("exec-a", (self.root / "a").as_uri(), thread="user-thread"))
        response = json.dumps(self.post(session_id="user-thread"))
        self.assertIn("user-file-memory", response)
        self.assertNotIn("memory-a", response)
        self.assertIsNone(self.sm.lookup(sid).get("project_path"))

    def test_ambiguity_is_logged_and_never_falls_back(self):
        self.write_records(completion("exec-a", (self.root / "a").as_uri()),
                           completion("exec-a", (self.root / "b").as_uri()))
        self.assertEqual(self.post(), {})
        self.assertEqual(self.last_log()["outcome"], "ambiguous_call")

    def test_absolute_operands_still_work_without_transcript_evidence(self):
        response = self.post(tool_input={"command": f"cat {self.root}/b/same.py"})
        self.assertIn("memory-b", json.dumps(response))
        self.assertEqual(self.last_log()["cwd_reason"], "unreadable_transcript")

    def test_explicit_cwd_wins_and_invalid_explicit_cwd_stays_unknown(self):
        self.write_records(completion("exec-a", (self.root / "a").as_uri()))
        self.assertEqual(self.post(tool_input={"command": "cat same.py", "workdir": "relative"}), {})
        response = self.post(tool_input={"command": "cat same.py", "workdir": str(self.root / "b")})
        self.assertIn("memory-b", json.dumps(response))
        self.assertEqual(self.last_log()["cwd_source"], "explicit_workdir")

    def test_unsupported_commands_have_compact_diagnostics(self):
        self.assertEqual(self.post(tool_input={"command": "echo nothing-to-recall"}), {})
        log = self.last_log()
        self.assertEqual(log["outcome"], "unsupported_command")
        self.assertIn("cwd_elapsed_ms", log)
        self.assertNotIn("nothing-to-recall", json.dumps(log))


if __name__ == "__main__":
    unittest.main()
