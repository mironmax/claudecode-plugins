# Antigravity CLI status

Reviewed 2026-10-04. The native KG adapter is experimental on
`codex/antigravity-cli` (330df15), separate from current main. Installing
the main plugin does not install this adapter. The
[recommended rule and status-line setup](../../recommended-setup/antigravity.md)
works independently of it.

## What has been verified

- On agy **1.2.15**, native mock-model tests loaded the plugin, rules and
  eager MCP schemas. Eleven transport checks passed, including exact Unicode
  content across queued chunks and file recall reaching model input.
- On signed-in agy **1.2.16**, real sessions used the shared memory server,
  read/search/sync/write/useful tools, prompt and file recall, and queued
  delivery through `PreInvocation`.
- Fifteen adapter boundary tests pass. These test transport and state
  boundaries; they do not establish complete lifecycle or model-behaviour parity.

MCP replies at or below **3,500 UTF-8 bytes** can be inline. Larger replies
use a persistent per-conversation queue and `PreInvocation` injection, with
a **40,000-byte** hook packet budget including framing. Large replies without
current-conversation hook evidence are refused with a setup hint. Eager MCP
tools alone do not prevent larger replies from being truncated by the CLI.

## Stable-support blockers

1. **Compaction loses context without clearing seen state.** A real checkpoint
   omitted 27 of 28 preloaded node IDs, but the next full read still suppressed
   their gists in the same KG session. Checkpoint reconciliation must restore
   missing context, including when output is queued across the checkpoint.
2. **Rejected reads still mutate the graph.** An isolated read of a large
   archived node without hooks returned an error while promoting the node
   and stamping its read time. Deferred delivery currently delays session
   bookkeeping but not all underlying graph effects.

Signed-in fork, clear and resume checks, plus failure paths during queued
delivery, remain acceptance gates. Current main has also advanced since the
adapter's base and needs an integrated verification pass.

## Feature scope

There is no Antigravity maintenance runner, subscription quota gate or
history-scout recipe. The status line persists weekly bucket observations,
but does not provide those features. The signed-in test did not exercise
quota exhaustion or establish payment behaviour. IDE and desktop support
were not verified by these CLI tests.

The original [1.2.11 survey](cards/antigravity.md) remains useful as a dated
record. Its missing-hook conclusions have been superseded by native tests;
they should not be read as the current implementation status.
