# 05 — Harness instrument review

## Goal

Before the plugin supports a second coding harness, know what the port
actually costs and where the line between core and adapter should fall.
This is an investigation, not a port: no server code changes.

The finished state it prepares for is one shared product with thin
per-harness adapters and full functionality everywhere, including ambient
recall and maintenance, not a reduced tier. Treat MCP, hooks, skills and
prompts as instruments that answer a Claude Code-shaped question. For each
job, ask again which instrument is cheapest in each harness.

## The deciding question, answered first

Does Codex CLI have a hook that runs on every user prompt and can add
context to the model's input, as `UserPromptSubmit` does here? If it does
not, ambient recall has no way to reach the model in Codex, and the plan
changes more than any other single finding could change it. Answer this
before anything else and put the answer, with its evidence, at the top of
the proposal.

## Jobs the system does today

| Job | Current instrument | Where |
|---|---|---|
| Server start and memory preload at session start | `SessionStart` hook | `hooks/kg-autostart.sh` |
| Ambient recall on each prompt | `UserPromptSubmit` hook, `additionalContext` | `hooks/kg-remind.sh`, `server/mcp_http/ambient.py` |
| Recall and nudges on tool use (file reads, edits, Bash, web) | `PostToolUse` hook | `hooks/kg-tool-event.sh` |
| Reading and writing memory | MCP tools over streamable HTTP, plus REST | `server/mcp_streamable_server.py`, `server/mcp_http/` |
| Doctrine delivery | hidden auto-loaded skill, tool descriptions, output style | `skills/kg-core/`, `recommended-setup/output-styles/` |
| Runbooks | user-invoked skills | `skills/kg-*` |
| Maintenance dispatch | headless `claude` binary with a scoped `--settings` allowlist | `server/mcp_http/chore_dispatch.py`, `chores/*.json` |
| Budget gate for maintenance | Claude subscription quota file written by the status line | `chore_dispatch.py` (`last-limits.json`), `recommended-setup/statusline.sh` |
| Session and project identity | hook stdin `session_id`, `cwd`, `transcript_path` | hooks, `server/mcp_http/` |
| Packaging, install, update | plugin manifest, marketplace, installer | `.claude-plugin/`, `install_command.sh` |
| Project discovery for the visual editor | reads `~/.claude/projects/` | `visual-editor/backend/project_discovery.py` |

Verify this table against the repository and correct it; it was written
from memory of the code, not generated from it.

## What to produce

Under `docs/harnesses/`:

1. `coupling-map.md`: every place the repository depends on Claude Code,
   with file and line references, sorted into three layers. Layer 0 is
   harness-neutral (the graph, scoring, search, archival, chore selection).
   Layer 1 is protocol (the MCP tool surface, REST). Layer 2 is
   harness-specific. Count the lines in layer 2 so the size of the port is
   a number.
2. One card per harness. Go deep on Codex CLI and survey Cursor and Google
   Antigravity more lightly; add another harness only if it clearly matters.
   Each card covers:
   - its extension points: MCP support and transport, hooks and their event
     names, what a hook receives and what it may return, and skill,
     instruction or rule files;
   - how it packages and distributes an extension;
   - whether it has a headless mode that a server could start for
     maintenance, and how that mode restricts permissions;
   - whether it exposes any usage or quota signal;
   - for every claim, a source URL and the date it was read, plus a
     verification level: read in docs, observed by running it, or secondhand.
3. `instrument-matrix.md`: the jobs above against the harnesses. Each cell
   gives the cheapest instrument that does the job, or states that there is
   a gap. A job with no instrument in a harness is a design problem, and the
   matrix says so plainly.
4. `proposal.md`: where the line between core and adapter should fall.
   - Cover what moves out of Claude Code-specific code and what stays.
   - Treat maintenance dispatch and the budget gate as the hardest part,
     since today they assume the `claude` binary and a Claude quota file.
     Weigh two options: a per-harness budget provider, or maintenance
     driven by the server itself.
   - List the open questions only the maintainer can decide.
   - Label this document as a proposal throughout.

## Constraints

- Use first-hand sources: official docs, source code, changelogs, or the
  tool itself. Several of these harnesses changed in 2026, so do not plan
  from prior knowledge. Where a CLI installs without an account, install it
  and read its config schema, `--help` and hook docs directly, and mark
  those findings as observed.
- No changes under `knowledge-graph/`. This task adds documents only.
- Neutral voice in the cards and the matrix. Recommendations belong in
  `proposal.md` and nowhere else.
- No claims about this plugin's internals beyond what the repository shows.

## Done when

The coupling map, the harness cards, the matrix and the proposal are
committed under `docs/harnesses/`. The top of the proposal answers the
deciding question with its evidence. Every external claim carries a source
and a verification level.
