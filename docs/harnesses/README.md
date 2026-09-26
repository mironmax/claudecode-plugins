# Harness instrument review

What it would cost to run this plugin in a second coding harness, and where
the line between core and adapter should fall. Written for roadmap item
[05](../../roadmap/tasks/05-harness-instrument-review.md). This is an
investigation: nothing under `knowledge-graph/` changed.

Start with the [proposal](proposal.md). Its first section answers the
deciding question, whether Codex CLI can add context to the model's input on
every prompt.

| Document | What it holds |
|---|---|
| [coupling-map.md](coupling-map.md) | Every place the repository depends on Claude Code, sorted into three layers, with a line count for the harness-specific layer |
| [cards/codex-cli.md](cards/codex-cli.md) | Codex CLI in depth: hooks, MCP, skills, packaging, headless mode, quota signal |
| [cards/cursor.md](cards/cursor.md) | Cursor (Agent CLI), lighter survey |
| [cards/antigravity.md](cards/antigravity.md) | Google Antigravity (CLI), lighter survey |
| [instrument-matrix.md](instrument-matrix.md) | The plugin's jobs against the harnesses: the cheapest instrument for each job, or the gap |
| [proposal.md](proposal.md) | Proposal: the deciding question, the core/adapter line, maintenance and budget, open questions |

## Verification levels

Every external claim carries one of these labels:

- **observed (run):** seen by running the tool during this investigation.
  Codex CLI was driven end to end against a local mock model endpoint, so no
  account was needed; the request Codex sent to the model was captured and
  read. This repository's own server (v0.9.42) ran alongside it.
- **observed (static):** read from the installed tool's own files (embedded
  JSON schemas, bundled source, `--help` output) without exercising the
  behaviour in an agent turn.
- **docs:** read in the vendor's official documentation. Where a page was
  read through a summarising fetch rather than as raw page text, the source
  line says so.
- **secondhand:** anything else, such as search-result snippets or third-party
  posts. Few claims rest on this level, and each is marked.

The task brief names three levels. "Observed" is split into run and static
here because the difference matters: a schema in a binary says what a tool
accepts, not what it does with it.

All sources were read on 2026-09-26. Versions examined: Codex CLI 0.157.1,
Cursor Agent CLI 2026.09.26-dd393fe, Antigravity CLI (`agy`) 1.2.11.
