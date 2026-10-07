# 10 — Staged recall follow-through

## Goal

An opt-in offline report in the evaluation harness that says what a session
did after recall showed it a memory: whether the agent read the node, used
it, updated it or endorsed it. The report describes activity and makes no
claim that recall caused it.

## Why

Endorsements are the only usefulness signal the evaluator reads, and they
barely credit staged recall. Of the 143 endorsements logged when the audit
below began, 6 had first reached the session through prompt recall and 1
through file recall. A local exploratory
audit then looked at what followed each injection. It covered 447 injections
(374 prompt, 73 file) over four weeks; 441 had a transcript. Within the next
three human requests, capped at 30 minutes, it found:

| Route | Cohort | Exposures | Memory-specific follow-through |
|---|---|---:|---:|
| Prompt, Claude Code | unseen, injected | 1,809 | 35 (1.9%) |
| Prompt, Claude Code | already seen (anchor) | 1,399 | 66 (4.7%) |
| Prompt, Codex | unseen, injected | 89 | 3 (3.4%) |
| File, Claude Code | unseen, injected | 118 | 4 (3.4%) |
| File, Claude Code | all seen, withheld | 341 | 50 (14.7%) |
| File, Claude Code | throttled, withheld | 368 | 10 (2.7%) |

So follow-through exists beyond endorsements, but it is sparse, and the
comparison cohorts show activity too. The cohorts are selected differently,
so none of this is causal. It still gives a far richer signal than 7
endorsements, and the evaluator should compute it reproducibly instead of
through a one-off script.

## Where things are

- `knowledge-graph/server/eval/`: `__main__.py` (CLI: `--root`, `--recall`,
  `--useful`, `--since`, `--until`, `--json`), `data.py` (`load_logs`,
  `GraphHistory`, the graph as it stood at a given time), `report.py`
  (`build_report`, `describe`, `format_text`), `replay.py` (`is_file_record`).
- Inputs: `recall.jsonl` (reasons `injected` and `file_recall`; file outcomes
  include `injected`, `all_seen` and `throttled`) and `useful.jsonl` (accepted
  endorsements; records with `via: "recurrence"` are server-side recurrence
  credits, not agent endorsements — filter them out of follow-through).
- Transcripts. Claude Code: `~/.claude/projects/<encoded-path>/<session>.jsonl`,
  where tool requests are assistant `tool_use` blocks. Codex:
  `${CODEX_HOME:-~/.codex}/sessions/**/rollout-*.jsonl`, where completed
  `CommandExecution`, `McpToolCall` and `FileChange` items describe inner
  calls. The Codex recipe in `knowledge-graph/skills/kg-scout/SKILL.md`
  documents the rollout format.
- Tests: `knowledge-graph/server/tests/test_v0940.py` covers the evaluator.

## What to build

1. **Unit.** One exposure is (recall event, graph level, node id). Keep
   prompt and file routes separate, and report each harness separately. Report
   these cohorts apart: unseen injected nodes, already-seen anchors in the
   same injection, all-seen file candidates, and throttled unseen file
   candidates. Also count distinct (session, node) pairs, so repeated or
   overlapping exposures cannot inflate the evidence.
2. **Window.** Look forward through the current request/response cycle and
   two more human requests, capped at 30 minutes, and add a five-minute view.
   Exclude tool calls already requested when the hook fired: a PostToolUse
   hook cannot cause the read or edit that triggered it.
3. **Signals, memory-specific.** The exact node id in an explicit
   `kg_read(id/ids)`; the exact id, or a distinctive gist phrase as it read at
   the time, in assistant prose; a `kg_put_node` update of that id; an
   accepted endorsement in `useful.jsonl`, not merely requested.
4. **Signals, file.** A later read or edit request of an exact path the node
   touched at the time, labelled requested or completed. Report this apart
   from the memory-specific signals: the file may have been central to the
   task before recall, and several nodes may touch it.
5. **Output.** An optional `followthrough` block with cohort counts,
   coverage, per-signal rates and evidence references (file and line), shown
   under a plainly descriptive heading next to the endorsement statistics.
   Enable it with a new `--transcripts` option; without it the evaluator stays
   log-only.

## Constraints

- Read-only. Reuse `GraphHistory` and the log loaders; never import a live
  store or write to storage.
- Resolve sessions by exact transcript and session identity, never by the
  newest session in a project.
- Never execute Codex JavaScript wrappers. Parse direct JSON calls, prefer
  completed items, deduplicate response and UI copies by id, and mark calls
  that cannot be attributed as unknown.
- Report as coverage, not as negative evidence: a missing transcript,
  ambiguous identity, no later activity, or an unobservable operation.
- Exclude tool results, injected memories, replayed history, summaries and
  inherited fork prefixes from the model-use signals.
- Graph state for gists and touches must come from history. Report known and
  approximate coverage separately. A current gist or an unchanged filename
  does not show what was injected.
- Transcripts are private. They and any per-exposure detail stay local, and
  tests use synthetic fixtures only.

## Done when

- Fixtures cover: post-hook exclusion; results and summaries that echo an
  id; direct and wrapped Codex calls; parallel calls completing in reverse
  order; missing transcripts; a resumed rollout that was appended to; graph
  revisions; the same id at both levels; repeated exposures; and accepted
  versus refused endorsements.
- `python -m eval --root ~/.knowledge-graph --transcripts` prints the block,
  and without the flag the output is unchanged.
- A local run over the real logs reproduces the audit's cohort counts, within
  the differences its coverage notes explain, and a person has reviewed sampled
  positive and negative events.
- Running the fixture tests needs no local data, so a cloud session can do
  everything except the local run.
