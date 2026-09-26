# Instrument matrix

The plugin's jobs, taken from the verified table in the
[coupling map](coupling-map.md), against each harness. Each cell names the
cheapest instrument that does the job, meaning the one that needs the least
new code given what exists today. Where no instrument does the job, the cell
says **GAP**. Claude Code is the reference column.

Evidence levels and sources are in the cards: [Codex CLI](cards/codex-cli.md),
[Cursor](cards/cursor.md), [Antigravity](cards/antigravity.md). A cell marked
*(run)* was observed by running the harness; the rest rest on documentation
or on static reading of the tool.

| Job | Claude Code (today) | Codex CLI 0.157.1 | Cursor (Agent CLI) | Antigravity (`agy` 1.2.11) |
|---|---|---|---|---|
| Server start and memory preload at session start | `SessionStart` hook, `additionalContext` | `SessionStart` hook, `additionalContext`: the same script ran *(run)*. Hook context is truncated past about 2,500 tokens by default *(run)*; the per-handler `additionalContextLimit` raises that. | `sessionStart` hook, `additional_context`, fire-and-forget; the CLI translates Claude Code's output form. Not run in cloud agents. | **GAP** in the documented events. Nearest: `PreInvocation` with `invocationNum` 0 returning `injectSteps: [{ephemeralMessage}]`. An undocumented `SessionStart` type exists in the binary. |
| Ambient recall on each prompt | `UserPromptSubmit` hook, `additionalContext` | `UserPromptSubmit` hook, `additionalContext`, delivered right after the user's message *(run)* | **GAP** as documented: `beforeSubmitPrompt` returns only `continue` and `user_message`. The CLI forwards an `additional_context` field to the backend, but whether it reaches the model is undetermined. | `PreInvocation` `injectSteps`. The prompt text is not in the payload, so the hook must read the newest user message from `transcriptPath`. It fires on every model call, so the hook must also tell a new prompt from a tool round-trip. |
| Recall and nudges on tool use | `PostToolUse` hook, matcher on Claude tool names | `PostToolUse` hook. Shell calls arrive as `Bash` and recall works *(run)*. Edits arrive as `apply_patch` with the patch text, which the server does not parse yet *(run)*. There is no `Read` tool. **GAP** for web capture nudges: hosted web search does not pass through hooks. | `postToolUse` hook, `additional_context`; tool names `Shell`, `Read`, `Write`. `afterFileEdit` cannot add context. | **GAP** in `PostToolUse`, which returns `{}` only. Nearest: `PreInvocation` after a tool step, reading the last `toolCall` from the transcript and injecting an `ephemeralMessage`. |
| Reading and writing memory | MCP over streamable HTTP | Same; plugin `.mcp.json` loaded as-is *(run)* | Same; `~/.cursor/mcp.json` with `url` *(`mcp list-tools`, run)* | Same, but the key must be `serverUrl`. `agy plugin import` dropped the URL *(run)*. |
| Doctrine delivery | Hidden skill (`kg-core`), MCP tool descriptions, output style, preload header | Plugin skills listed with descriptions *(run)*; MCP tool descriptions; preload header; `AGENTS.md` | Plugin skills and rules; MCP tool descriptions; preload header | Plugin skills and rules; MCP tool descriptions |
| Runbooks | User-invoked skills | Plugin skills *(run: all five listed)* | Plugin skills | Plugin skills *(import: 5 processed, run)* |
| Maintenance trigger (a prompt arrived) | `UserPromptSubmit` hook posts to `/api/prompt_context` | Same hook *(run)* | `beforeSubmitPrompt`: it fires on each prompt and can post, and the trigger needs no context back | `PreInvocation` |
| Maintenance runner, with restricted tools | `claude -p --setting-sources user --settings <allowlist>` | `codex exec --ephemeral -s read-only --disable shell_tool -c web_search="disabled"` with MCP `enabled_tools` and `default_tools_approval_mode="approve"` *(run)* | `agent -p` with `Mcp(…)` allow and `Shell`/`Write` deny in `cli-config.json`. Behaviour without a terminal is undetermined. | `agy -p` with `mcp(kg/*)` in `permissions.allow`. Unlisted tools are soft-denied. |
| Budget gate for maintenance | Status line script writes `~/.claude/last-limits.json` (5-hour and 7-day used %) | Rollout file `token_count.rate_limits` (primary and secondary: `used_percent`, `window_minutes`, `resets_at`) *(run, with simulated headers)* | **GAP**: no per-user usage signal is documented outside the web dashboard | Status line command receives `quota` (`remaining_fraction`, `reset_time` per bucket) |
| Session and project identity | Hook stdin `session_id`, `cwd`, `transcript_path`, `source` | Same fields and the same `source` values *(run)* | `conversation_id`, `workspace_roots`, `transcript_path`; `cwd` only on tool hooks | `conversationId`, `workspacePaths`, `transcriptPath` |
| Packaging, install, update | `.claude-plugin/` manifest and marketplace | This repository's `.claude-plugin/` marketplace and plugin install unchanged *(run)* | Reads `.claude-plugin/plugin.json` and marketplace (static); native `.cursor-plugin/` | Native plugin directory with root `plugin.json`; `agy plugin import` converts a Claude Code plugin, losing the HTTP MCP URL *(run)* |
| Hook activation after install | Active after plugin install and reload | Skipped until the user trusts them in `/hooks` *(run)* | Third-party config import is on by default | Undetermined |
| Project discovery for the visual editor | Reads `~/.claude/projects/` | Session files under `~/.codex/sessions/YYYY/MM/DD/`, one per session, with `cwd` in the metadata *(run)* | Undetermined | Transcripts under `<app_data_dir>/brain/<conversationId>/`; the mapping to a project is undetermined |
| Mining past sessions (`kg-scout`) | Reads `~/.claude/history.jsonl` and project transcripts | Rollout JSONL files *(run: format seen)* | `transcript_path` files | `transcript.jsonl` files |

## Gaps, stated plainly

Each of these is a design problem, not a porting task.

1. **Cursor has no documented per-prompt context channel.** Ambient recall,
   the plugin's timing channel, has no instrument there. The CLI code
   suggests one may exist. Confirming it needs an account and a live turn.
2. **Antigravity's documented hooks cannot add context after a tool or at a
   prompt.** Everything must go through `PreInvocation` injection, reading
   the transcript to learn what just happened. It has no session start event
   either.
3. **Cursor exposes no usage signal a server could read**, so the budget
   gate has nothing to gate on.
4. **Codex runs web search as a hosted tool, outside hooks.** The web half of
   the capture nudges cannot fire there.
5. **Codex skips plugin hooks until the user reviews them in `/hooks`.**
   Until then, the preload, recall and maintenance trigger are all silent.
