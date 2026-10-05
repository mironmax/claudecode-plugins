# Instrument matrix

**Historical baseline, 2026-09-26.** The cells below describe the versions
examined in the original survey. They are not today's support table.
Codex now supports `apply_patch`, verified shell execution directories,
explicit `nl`/`rg` file reads, rollout scouting and server-owned project
discovery; see the [current support table](../../knowledge-graph/README.md#codex-cli).
Antigravity's later native hooks and queued delivery are described in
[Antigravity status](antigravity-status.md); that adapter remains experimental.

The plugin's jobs, taken from the verified table in the
[coupling map](coupling-map.md), against each harness. Each cell names the
cheapest instrument that does the job, meaning the one that needs the least
new code given what exists today. Where no instrument does the job, the cell
says **GAP**. Claude Code is the reference column.

Evidence levels and sources are in the cards: [Codex CLI](cards/codex-cli.md),
[Cursor](cards/cursor.md), [Antigravity](cards/antigravity.md). A cell marked
*(run)* was observed by running the harness; the rest rest on documentation
or on static reading of the tool. The Antigravity column was redone on
2026-10-01 by running `agy` 1.2.14 against a mock model; the
[Antigravity mapping](antigravity-mapping.md) builds on it.

| Job | Claude Code (today) | Codex CLI 0.157.1 | Cursor (Agent CLI) | Antigravity (`agy` 1.2.14) |
|---|---|---|---|---|
| Server start and memory preload at session start | `SessionStart` hook, `additionalContext` | `SessionStart` hook, `additionalContext`: the same script ran *(run)*. Hook context is truncated past about 2,500 tokens by default *(run)*; the per-handler `additionalContextLimit` raises that. | `sessionStart` hook, `additional_context`, fire-and-forget; the CLI translates Claude Code's output form. Not run in cloud agents. | `SessionStart` (undocumented) returning `injectSteps: [{systemMessage}]`; it persists and lands after the first prompt *(run)*. Plain text, or any key the CLI does not know, discards the reply *(run)*. |
| Ambient recall on each prompt | `UserPromptSubmit` hook, `additionalContext` | `UserPromptSubmit` hook, `additionalContext`, delivered right after the user's message *(run)* | **GAP** as documented: `beforeSubmitPrompt` returns only `continue` and `user_message`. The CLI forwards an `additional_context` field to the backend, but whether it reaches the model is undetermined. | `PreInvocation` with `invocationNum` 0, which restarts on every prompt, returning a `systemMessage` placed after the prompt *(run)*. The prompt is read from the transcript, which is current when the hook runs *(run)*. |
| Recall and nudges on tool use | `PostToolUse` hook, matcher on Claude tool names | `PostToolUse` hook. Shell calls arrive as `Bash` and recall works *(run)*. Edits arrive as `apply_patch` with the patch text, which the server does not parse yet *(run)*. There is no `Read` tool. **GAP** for web capture nudges: hosted web search does not pass through hooks. | `postToolUse` hook, `additional_context`; tool names `Shell`, `Read`, `Write`. `afterFileEdit` cannot add context. | `PostToolUse` sees the call (`view_file`, `write_to_file`, `replace_file_content`, `run_command` with `Cwd`) but cannot inject. The next `PreInvocation`'s `systemMessage` is appended to the tool result *(run)*. |
| Reading and writing memory | MCP over streamable HTTP | Same; plugin `.mcp.json` loaded as-is *(run)* | Same; `~/.cursor/mcp.json` with `url` *(`mcp list-tools`, run)* | MCP over streamable HTTP with `serverUrl` *(run)*. Tools are lazy, behind one `call_mcp_tool` *(run)*. **GAP** for reads as designed: a result over 4,096 characters reaches the model only as a file path *(run)*. |
| Doctrine delivery | Hidden skill (`kg-core`), MCP tool descriptions, output style, preload header | Plugin skills listed with descriptions *(run)*; MCP tool descriptions; preload header; `AGENTS.md` | Plugin skills and rules; MCP tool descriptions; preload header | Plugin skills *(run)*; a plugin `always_on` rule in the system prompt *(run)*; MCP tool descriptions are not in context *(run)* |
| Runbooks | User-invoked skills | Plugin skills *(run: all five listed)* | Plugin skills | Plugin skills, which also become slash commands *(run)* |
| Maintenance trigger (a prompt arrived) | `UserPromptSubmit` hook posts to `/api/prompt_context` | Same hook *(run)* | `beforeSubmitPrompt`: it fires on each prompt and can post, and the trigger needs no context back | `PreInvocation` at `invocationNum` 0 *(run)* |
| Maintenance runner, with restricted tools | `claude -p --setting-sources user --settings <allowlist>` | `codex exec --ephemeral -s read-only --disable shell_tool -c web_search="disabled"` with MCP `enabled_tools` and `default_tools_approval_mode="approve"` *(run)* | `agent -p` with `Mcp(…)` allow and `Shell`/`Write` deny in `cli-config.json`. Behaviour without a terminal is undetermined. | `agy --agent <name>` whose agent has `tools: []`, leaving only the MCP tools *(run)*. MCP grants come only from `permissions.allow` *(run)*. An agent that fails to load falls back to the default one silently *(run)*. |
| Budget gate for maintenance | Status line script writes `~/.claude/last-limits.json` (5-hour and 7-day used %) | Rollout file `token_count.rate_limits` (primary and secondary: `used_percent`, `window_minutes`, `resets_at`) *(run, with simulated headers)* | **GAP**: no per-user usage signal is documented outside the web dashboard | `agy -p /usage --output-format json`, structure only (empty under an API key) *(run)*; the status line's `quota` while the TUI is open |
| Session and project identity | Hook stdin `session_id`, `cwd`, `transcript_path`, `source` | Same fields and the same `source` values *(run)* | `conversation_id`, `workspace_roots`, `transcript_path`; `cwd` only on tool hooks | `conversationId`, `workspacePaths`, `transcriptPath`, no `source` *(run)*; the same id in every MCP call's `_meta` *(run)* |
| Packaging, install, update | `.claude-plugin/` manifest and marketplace | This repository's `.claude-plugin/` marketplace and plugin install unchanged *(run)* | Reads `.claude-plugin/plugin.json` and marketplace (static); native `.cursor-plugin/` | Native plugin: root `plugin.json`, `hooks.json`, `mcp_config.json`; `agy plugin install <dir>` *(run)*. `agy plugin import` of this plugin keeps no working hook and no MCP URL *(run)*. |
| Hook activation after install | Active after plugin install and reload | Skipped until the user trusts them in `/hooks` *(run)* | Third-party config import is on by default | No trust step in headless mode *(run)*; one invalid entry drops every hook in its file *(run)* |
| Project discovery for the visual editor | Reads `~/.claude/projects/` | Session files under `~/.codex/sessions/YYYY/MM/DD/`, one per session, with `cwd` in the metadata *(run)* | Undetermined | `conversation_summaries.db` maps each conversation to `workspace_uris` *(run)* |
| Mining past sessions (`kg-scout`) | Reads `~/.claude/history.jsonl` and project transcripts | Rollout JSONL files *(run: format seen)* | `transcript_path` files | `conversation_summaries.db` as the inventory, `transcript_full.jsonl` per conversation *(run)* |

## Gaps at the time of the survey

Each of these is a design problem, not a porting task.

1. **Cursor has no documented per-prompt context channel.** Ambient recall,
   the plugin's timing channel, has no instrument there. The CLI code
   suggests one may exist. Confirming it needs an account and a live turn.
2. **Antigravity hides large tool results from the model.** A result over
   4,096 characters arrives as a file path, so a full `kg_read` reaches the
   model only if it opens the file, while the server has already counted it
   as seen. (The gap stated here on 2026-09-26, no context channel at a
   prompt, after a tool or at session start, did not survive running the
   CLI: an injected `systemMessage` serves all three.)
3. **Cursor exposes no usage signal a server could read**, so the budget
   gate has nothing to gate on.
4. **Codex runs web search as a hosted tool, outside hooks.** The web half of
   the capture nudges cannot fire there.
5. **Codex skips plugin hooks until the user reviews them in `/hooks`.**
   Until then, the preload, recall and maintenance trigger are all silent.
