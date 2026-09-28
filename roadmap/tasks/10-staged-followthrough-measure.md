# Staged recall follow-through (proposal)

Extend `knowledge-graph/server/eval/report.py` with an opt-in offline
follow-through report. Endorsement alone misses observable uses of a recalled
node. This measure describes subsequent activity; it cannot establish that
recall caused that activity or improved the outcome.

## Unit and window

Use one `(recall event, graph level, node id)` exposure. Keep prompt and file
routes separate and report each harness separately. Report unseen injected
nodes, already-seen anchors in the same injection, all-seen file candidates,
and throttled unseen file candidates as distinct cohorts. These are descriptive
comparisons, not randomized controls. Also summarize distinct session/node
pairs so repeated or overlapping exposures cannot inflate apparent evidence.

Look forward through the current request/response cycle and two subsequent
human requests, capped at 30 minutes. Include a five-minute sensitivity view.
Exclude tools the assistant had already requested when the hook fired:
PostToolUse cannot cause the triggering read or edit. Report transcript absence,
ambiguous identity, missing future activity and unobservable operations
explicitly; do not count these as negative evidence.

## Observable signals

- Exact node id in an explicit `kg_read(id/ids)` call.
- Exact id or a distinctive historical gist phrase in assistant prose.
- A requested read or edit of an exact historical touched path, with completion
  status when the transcript records it. Label requested versus successful.
- A `kg_put_node` update of that id.
- Accepted endorsement from useful.jsonl, distinct from an endorsement request.

Keep memory-specific signals separate from touched-file activity: a file may
have been central to the task before recall, and multiple nodes may touch it.
Gist/touches require historical graph state, with known/approx/current coverage
reported separately. An unchanged filename or a current gist is not proof of
what was injected. Exclude results, injected memories, replayed history,
summaries and inherited fork prefixes from model-use signals. Report prior use
of the same id and sensitivity to overlapping exposure windows.

## Inputs and integration

Add an explicit transcript-root option; default evaluation remains log-only.
Resolve sessions by exact transcript/session identity, never newest-by-project.
Claude tool requests come from assistant tool_use blocks. Codex completed
CommandExecution/McpToolCall/FileChange items supply structured inner calls;
parse direct JSON calls and never execute JavaScript wrappers. Deduplicate
response and UI copies by ids, retain source file/line provenance, and mark
unattributable calls unknown. Private transcripts and report details stay local.

Return an optional `followthrough` block containing cohort counts, coverage,
per-signal rates and evidence references. Render it under a clearly descriptive
heading, alongside existing endorsement statistics. Reuse GraphHistory and
log loaders without importing a live store or writing to storage.

## Validation gate before implementation

Use synthetic fixtures for post-hook exclusion, result/summary echo, direct and
wrapped Codex calls, reversed parallel completion, missing transcripts, resumed
append, graph revisions, same ids in different levels, repeated exposures and
accepted-versus-refused endorsements. Manually review sampled positive and
negative local events. A causal-value claim needs a separately designed trial.

Status: proposal only. The local exploratory audit is not a production-quality
adapter and should not be folded into the evaluator without these gates.
