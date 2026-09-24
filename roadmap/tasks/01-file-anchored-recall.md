# 01 — File-anchored recall

## Goal

When the agent opens or edits a file, the nodes whose `touches` point at that
file should reach its context, once, compactly, without anyone having to
search for them. The file the agent is in is the most reliable signal of what
it is about to need, and it is available at zero token cost.

## Why

Retrieval today fires at session start (preload) and on each user prompt
(ambient recall, matched on the prompt's words). Nothing fires at the tool
level. In observed use, most file reads open a file that one or more nodes
already describe, often archived ones, which is exactly the memory the
agent does not know to search for. Warnings about a file ("this config must
keep X", "this module breaks when Y") matter most just before an edit.

## Where things are

- `knowledge-graph/hooks/hooks.json` — PostToolUse currently matches
  `Read|WebFetch|WebSearch`; `hooks/kg-tool-event.sh` posts the raw hook
  payload to `POST /api/tool_event` and prints whatever hook output returns.
- `server/mcp_http/ambient.py` — `handle_tool_event`, `_extract_target`,
  `_normalize_file`, `_target_covered` (today touches are used only to decide
  whether a capture nudge is warranted); `build_prompt_recall` is the model
  for rendering, seen-dedup, char budget and logging (`log_recall`).
- `server/mcp_http/session_manager.py` — `mark_seen(sid, ids, via=...)`;
  surfaced routes are listed in `core/constants.py:SURFACE_VIAS`.
- PostToolUse `hookSpecificOutput.additionalContext` is known to reach the
  model (the capture nudge already uses it).

## What to build

1. A reverse index, artifact → node ids, derived from `touches` of both the
   user graph and the session's project graph. Normalise paths the way
   `_normalize_file` does (project-relative, `~` expanded, line ranges and
   trailing annotations stripped). Build lazily, invalidate on graph writes;
   a lookup must be O(1) — the hook has a one-second budget end to end.
2. On a tracked tool event, look the file up. Render the matching nodes the
   session has not seen yet (gist lines, compact, archived nodes included,
   capped — start with 3 and a char budget), mark them seen with a new route
   `file` (add it to `SURFACE_VIAS`: the system surfaced them), and log the
   decision to `recall.jsonl` with its own reason, silences included.
3. Widen the tracked tools: `Edit`, `Write`, `NotebookEdit` (and
   `MultiEdit` where the harness still has it; `file_path` /
   `notebook_path`), and `Bash` commands that read files (`cat`,
   `head`, `tail`, `sed -n`, `less`, `grep ... <file>`, `jq ... <file>`).
   Bash extraction must be conservative: take only clear file operands,
   resolve relative to `cwd`, ignore anything ambiguous. Missing a file is
   fine; inventing one is not.
4. Keep the existing capture nudge: covered file → recall; uncovered file
   that has proven itself → nudge, as today. Both never in one response.
5. Throttle per session so a long refactor over one directory does not
   become a stream of injections.

## Constraints

- Never raise from the hook path; never slow a tool call noticeably.
- Do not promote archived nodes by surfacing them (only reads promote);
  do not count surfacing as usefulness.
- Ranking inside a file's node set: reuse the existing node score; ties by
  recency. Do not add a new scoring formula in this task.
- Follow the repo's test convention: a self-contained
  `server/tests/test_v09NN.py`, runnable with the project venv, no pytest.
- Public repo: code comments are navigation, not history.

## Done when

- Tests cover: index build and invalidation, path normalisation (relative,
  absolute, `~`, `path:12-40 (anchor)`), dedup against seen, cap and budget,
  throttle, Bash extraction (positives and ambiguous negatives), the log
  record, and the nudge path still working.
- The whole suite stays green.
- `CHANGELOG.md` has an Unreleased entry describing the behaviour change.

## Out of scope

Line-range-aware ranking, PreToolUse injection (being verified separately),
glob anchors for practice lenses (possible follow-up on the same index).
