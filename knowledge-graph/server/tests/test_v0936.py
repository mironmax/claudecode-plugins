#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.36 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0936.py

Both changes are instrumentation: the system was doing work and keeping no
record of it, so the questions that matter could only be answered from
sources that erase themselves.

  RECALL LOG. build_prompt_recall computed what it injected and then kept
  nothing. Every ambient-recall number — fire rate, payload, per-node
  frequency — was reconstructed after the fact by devdocs/audit/recall-audit.py
  from Claude Code transcripts, which expire in 30 days: the measurement had a
  shorter memory than the graph it measured. Silences are logged too, and
  deliberately: injections alone give no denominator, and the prompts that
  scored just under the bar are the only evidence a threshold change can be
  argued from — nothing had ever seen them.

  PROGRESS TRAIL. set_progress ASSIGNED the state dict, so every stamp
  destroyed the one before it. The maintenance pass is asked to carry
  "found-but-deferred" forward as the next pass's cursor and the storage
  could not hold it across two passes; the 20-minute dispatcher therefore
  reconsidered from scratch every tick, re-litigating merges it had already
  weighed and refused.

Covers:
  1. An injection writes one parseable record naming the nodes, their scores
     and the matched terms — enough to replay the ranking after the
     transcript that held the prompt is gone
  2. Silences are recorded with a reason, and a near-miss carries the top
     candidates it rejected
  3. No registered session writes nothing (there is nothing to attribute)
  4. Rotation rolls to .prev at the size ceiling and keeps appending
  5. A failing log write never propagates — a hook must not break a session
  6. Two stamps both survive in the trail, newest last
  7. Top-level state keys are written through unchanged (core.debt reads
     state["last_ts"] directly and must not notice the trail)
  8. The ring bounds at PROGRESS_TRAIL_MAX
  9. A caller-supplied _trail is ignored — the ring is server-owned
 10. Oversized values are clipped, not dropped
 11. `declined` — the field the maintain skill now stamps — survives
 12. The trail persists through a save/reload cycle
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from core.constants import (  # noqa: E402
    PROGRESS_TRAIL_KEY,
    PROGRESS_TRAIL_LIST_ITEMS,
    PROGRESS_TRAIL_MAX,
    PROGRESS_TRAIL_VALUE_CHARS,
)
from mcp_http import ambient  # noqa: E402
from mcp_http.ambient import build_prompt_recall  # noqa: E402
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


def read_log():
    """Every record currently in the live log file, oldest first."""
    path = ambient._recall_log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def clear_log():
    for p in (ambient._recall_log_path(),
              ambient._recall_log_path().parent / (ambient._recall_log_path().name + ".prev")):
        if p.exists():
            p.unlink()


def main():
    print("=== v0.9.36 instrumentation tests ===")

    project_dir = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    config = GraphConfig(save_interval=9999)  # saver thread stays asleep
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)

    try:
        sid = session_manager.register(project_dir, claude_sid="cc-log-1")["session_id"]

        # --- 1. the injection record -----------------------------------------
        print("injection record:")
        clear_log()
        session_manager.mark_full_read(sid)
        store.put_node(level="project", node_id="quixotic-tokenizer",
                       gist="The quixotic tokenizer normalizes xanthic glyphs",
                       session_id=sid)
        text = build_prompt_recall(store, session_manager, project_dir,
                                   "explain the quixotic tokenizer", claude_sid="cc-log-1")
        check("injection happened", text is not None and "quixotic-tokenizer" in text, text)

        records = read_log()
        injected = [r for r in records if r["reason"] == "injected"]
        check("one injected record written", len(injected) == 1, records)
        rec = injected[0] if injected else {}
        check("names the node it injected",
              any(h["id"] == "quixotic-tokenizer" for h in rec.get("hits", [])), rec)
        check("carries the level and score",
              bool(rec.get("hits")) and rec["hits"][0].get("level") == "project"
              and isinstance(rec["hits"][0].get("score"), float), rec)
        check("carries the matched terms for later replay",
              "quixotic" in (rec.get("terms") or []), rec)
        check("carries the payload size and the session identities",
              isinstance(rec.get("chars"), int) and rec.get("chars") > 0
              and rec.get("kg_session") == sid
              and rec.get("claude_session") == "cc-log-1", rec)
        check("record is one line of JSON, not a blob",
              all(isinstance(r, dict) for r in records), records)

        # --- 2. silences are recorded too ------------------------------------
        print("silences:")
        clear_log()
        sid2 = session_manager.register(project_dir, claude_sid="cc-log-2")["session_id"]
        build_prompt_recall(store, session_manager, project_dir,
                            "anything at all", claude_sid="cc-log-2")
        reasons = [r["reason"] for r in read_log()]
        check("the full-read nudge is a logged outcome",
              reasons == ["full_read_nudge"], reasons)

        clear_log()
        session_manager.mark_full_read(sid2)
        build_prompt_recall(store, session_manager, project_dir,
                            "zzyzx frobnicate wibble", claude_sid="cc-log-2")
        records = read_log()
        check("a miss is recorded with its reason",
              len(records) == 1 and records[0]["reason"] in ("no_hits", "all_seen"), records)
        check("the near-miss carries what it rejected",
              "best" in records[0] or "hits" in records[0], records[0])
        check("the miss carries the threshold it was judged against",
              isinstance(records[0].get("threshold"), float), records[0])

        clear_log()
        build_prompt_recall(store, session_manager, project_dir,
                            "<task-notification>build finished</task-notification>",
                            claude_sid="cc-log-2")
        reasons = [r["reason"] for r in read_log()]
        check("a harness record is logged as not-a-prompt",
              reasons == ["not_a_prompt"], reasons)

        # --- 3. nothing to attribute, nothing written ------------------------
        print("unattributable prompts:")
        clear_log()
        out = build_prompt_recall(store, session_manager, "/nonexistent/project",
                                  "hello", claude_sid="cc-unknown")
        check("no session -> no injection", out is None)
        check("no session -> no record", read_log() == [], read_log())

        # --- 4. rotation ------------------------------------------------------
        print("rotation:")
        clear_log()
        original_max = ambient.RECALL_LOG_MAX_BYTES
        ambient.RECALL_LOG_MAX_BYTES = 400
        live = ambient._recall_log_path()
        prev = live.parent / (live.name + ".prev")
        try:
            # Write one at a time and stop at the FIRST roll. Beyond it the
            # single .prev generation is overwritten and older records are
            # dropped on purpose — the audit window is weeks, not years — so
            # only the first roll can be asserted lossless.
            written = 0
            while not prev.exists() and written < 200:
                ambient.log_recall("probe", project_dir, f"cc-{written}", sid, terms=["t"] * 5)
                written += 1
            check("rolled to .prev at the ceiling", prev.exists(), written)
            check("live file stayed under the ceiling",
                  live.stat().st_size <= ambient.RECALL_LOG_MAX_BYTES, live.stat().st_size)
            check("appending continued after the roll", len(read_log()) >= 1)
            kept = len(read_log()) + len([l for l in prev.read_text().splitlines() if l.strip()])
            check("the first roll loses nothing", kept == written, (kept, written))
        finally:
            ambient.RECALL_LOG_MAX_BYTES = original_max

        # --- 5. a failing write must not propagate ---------------------------
        print("write failure containment:")
        clear_log()
        original_path_fn = ambient._recall_log_path
        ambient._recall_log_path = lambda: Path("/proc/version/nope/recall.jsonl")
        try:
            ambient.log_recall("probe", project_dir, "cc-x", sid)
            check("an unwritable log path raises nothing", True)
        except Exception as e:  # pragma: no cover — the failure this guards
            check("an unwritable log path raises nothing", False, repr(e))
        finally:
            ambient._recall_log_path = original_path_fn

        # --- 6-7. the trail accumulates, top-level keys survive ---------------
        print("progress trail:")
        store.set_progress("maintain", {"last_ts": 100.0, "gists_tightened": 2}, level="user")
        store.set_progress("maintain", {"last_ts": 200.0, "gists_tightened": 5}, level="user")
        state = store.get_progress("maintain", level="user")
        trail = state.get(PROGRESS_TRAIL_KEY, [])
        check("both stamps survive", len(trail) == 2, trail)
        check("newest is last", trail[-1]["last_ts"] == 200.0, trail)
        check("the earlier stamp is still readable",
              trail[0]["gists_tightened"] == 2, trail)
        check("top-level state is the latest stamp (core.debt reads it directly)",
              state["last_ts"] == 200.0 and state["gists_tightened"] == 5, state)

        # --- 8. the ring bounds ----------------------------------------------
        for i in range(PROGRESS_TRAIL_MAX + 5):
            store.set_progress("ring", {"n": i}, level="user")
        ring = store.get_progress("ring", level="user")[PROGRESS_TRAIL_KEY]
        check("ring bounds at PROGRESS_TRAIL_MAX", len(ring) == PROGRESS_TRAIL_MAX, len(ring))
        check("the ring keeps the NEWEST entries",
              ring[-1]["n"] == PROGRESS_TRAIL_MAX + 4, ring[-1])

        # --- 9. the ring is server-owned -------------------------------------
        store.set_progress("forged", {"n": 1, PROGRESS_TRAIL_KEY: [{"n": "planted"}]}, level="user")
        forged = store.get_progress("forged", level="user")
        check("a caller-supplied trail is ignored",
              len(forged[PROGRESS_TRAIL_KEY]) == 1
              and forged[PROGRESS_TRAIL_KEY][0].get("n") == 1, forged)
        check("the forged key is not written through to the stamp",
              forged[PROGRESS_TRAIL_KEY][0].get("n") != "planted", forged)

        # --- 10. oversized values are clipped, not dropped --------------------
        long_reason = "x" * (PROGRESS_TRAIL_VALUE_CHARS * 3)
        store.set_progress("big", {"note": long_reason,
                                   "items": [f"item-{i}" for i in range(PROGRESS_TRAIL_LIST_ITEMS * 3)]},
                           level="user")
        big = store.get_progress("big", level="user")[PROGRESS_TRAIL_KEY][-1]
        check("long strings are clipped",
              len(big["note"]) == PROGRESS_TRAIL_VALUE_CHARS, len(big["note"]))
        check("long lists are capped",
              len(big["items"]) == PROGRESS_TRAIL_LIST_ITEMS, len(big["items"]))
        check("clipped values are still present, not dropped",
              big["note"].startswith("x") and big["items"][0] == "item-0", big)
        check("every entry is stamped with its own time",
              isinstance(big.get("_ts"), float), big)

        # --- 11. the field the skill stamps ----------------------------------
        store.set_progress("maintain",
                           {"last_ts": 300.0,
                            "declined": ["merge a/b — overlap is superficial, kept both"]},
                           level="user")
        declined = store.get_progress("maintain", level="user")[PROGRESS_TRAIL_KEY][-1]["declined"]
        check("declined reaches the trail",
              declined and "overlap is superficial" in declined[0], declined)

        # --- 12. it survives a round trip to disk ----------------------------
        store._save_to_disk("user")
        store.reload_user_graph()
        reloaded = store.get_progress("maintain", level="user")
        check("the trail persists across a save/reload",
              len(reloaded.get(PROGRESS_TRAIL_KEY, [])) == 3, reloaded.get(PROGRESS_TRAIL_KEY))
        check("the reloaded trail keeps the declined line",
              "declined" in reloaded[PROGRESS_TRAIL_KEY][-1], reloaded[PROGRESS_TRAIL_KEY][-1])

    finally:
        store.running = False
        shutil.rmtree(project_dir, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
