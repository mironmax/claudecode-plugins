# Cursor

Surveyed more lightly than Codex. Version examined: Cursor Agent CLI
`2026.09.26-dd393fe`, installed with the official script
(<https://cursor.com/install>) and no account. The editor was not examined.
Running an agent turn needs a Cursor account, so nothing below was observed
end to end. What was observed comes from `--help`, from `agent mcp`, which
works without logging in, and from reading the CLI's bundled JavaScript.
All sources read 2026-09-26.

Labels are defined in the [README](../README.md#verification-levels).

## Extension points

### Hooks

| Claim | Level | Source |
|---|---|---|
| Agent hooks: `sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`, `postToolUseFailure`, `subagentStart`, `subagentStop`, `beforeShellExecution`, `afterShellExecution`, `beforeMCPExecution`, `afterMCPExecution`, `beforeReadFile`, `afterFileEdit`, `beforeSubmitPrompt`, `preCompact`, `stop`, `afterAgentResponse`, `afterAgentThought`. Tab hooks: `beforeTabFileRead`, `afterTabFileEdit`. App hook: `workspaceOpen`. | docs | [C1] |
| Config lives in `~/.cursor/hooks.json`, `<project>/.cursor/hooks.json`, enterprise MDM paths, and a team dashboard. Format: `{"version": 1, "hooks": {"<event>": [{"command": "…"}]}}`. | docs | [C1] |
| Every hook receives `conversation_id`, `generation_id`, `model`, `model_id`, `model_params`, `hook_event_name`, `cursor_version`, `workspace_roots`, `user_email`, `transcript_path`. | docs | [C1] |
| `beforeSubmitPrompt` receives `prompt` and `attachments`, and returns only `continue` and `user_message`. It "can prevent submission"; the documentation lists no field that adds context. | docs | [C1] |
| The CLI's code copies an `additional_context` field from `beforeSubmitPrompt` output into its response to the backend. Whether the backend puts that text in front of the model is undetermined. | observed (static) | Bundled `index.js` |
| `sessionStart` receives `session_id`, `is_background_agent`, `composer_mode`, and returns `env` and `additional_context`, which is "added to the conversation's initial system context". It is fire-and-forget. It does not run in cloud agents. | docs | [C1] |
| `postToolUse` receives `tool_name`, `tool_input`, `tool_output` (a JSON string), `tool_use_id`, `cwd`, `duration` and model fields. It returns `updated_mcp_tool_output` and `additional_context`, which is "injected into the conversation after the tool result". `postToolUseFailure` also returns `additional_context`. | docs | [C1] |
| `afterFileEdit` receives `file_path` and `edits` and supports no output fields. `beforeReadFile` is matched as `Read` and `afterFileEdit` as `Write`. | docs | [C1] |
| The shell tool is named `Shell`, with `tool_input.command`. | docs | [C1] examples |

### Claude Code compatibility

| Claim | Level | Source |
|---|---|---|
| Cursor loads Claude Code hooks from `~/.claude/settings.json`, `.claude/settings.json` and `.claude/settings.local.json` when "Include Third-Party Plugins, Skills, and Other Configs" is on, which is the default. | docs (summarised) | [C2] |
| The event mapping sends `UserPromptSubmit` to `beforeSubmitPrompt`, `SessionStart` to `sessionStart`, and `PostToolUse` to `postToolUse`. `Notification` and `PermissionRequest` are unsupported. `Bash` becomes `Shell`. | docs (summarised); observed (static) | [C2]; the same table is in the bundled code. |
| The CLI turns Claude Code's nested `hookSpecificOutput.additionalContext` into Cursor's `additional_context` for `sessionStart`, `beforeSubmitPrompt`, `preToolUse`, `postToolUse` and `postToolUseFailure`. It checks `hookEventName` against the mapped Claude name. The CLI enables this compatibility mode. | observed (static) | Bundled `index.js` and `1699.index.js` (`enableClaudeNestedHookSpecificOutputCompatibility: true`) |
| The plugin loader reads `.cursor-plugin/plugin.json`, `.claude-plugin/plugin.json` or a root `plugin.json`, and marketplaces from `.cursor-plugin/marketplace.json` or `.claude-plugin/marketplace.json`. It substitutes both `${CURSOR_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_ROOT}`. | observed (static); docs | Bundled code; [C3] |

### MCP

| Claim | Level | Source |
|---|---|---|
| Servers are configured in `~/.cursor/mcp.json` or `.cursor/mcp.json`; remote servers use `url`. | observed (run) | `agent mcp --help`; `agent mcp list-tools kg` listed all ten tools from this repository's server over streamable HTTP, without logging in. |
| MCP permissions use the token `Mcp(server:tool)`. | docs (summarised) | [C4] |

### Rules, skills and plugins

| Claim | Level | Source |
|---|---|---|
| Plugins bundle rules, agents, skills, commands, hooks, MCP servers and variables. The manifest is `.cursor-plugin/plugin.json`, or `plugin.json` at the root for the open "Agent Plugins" format. A multi-plugin marketplace is `.cursor-plugin/marketplace.json` at the repository root. | docs (summarised) | [C3] |
| The CLI takes `--plugin-dir <path>` and has `agent plugin marketplace`. | observed (run) | `agent --help`, `agent plugin --help` |

## Headless mode and its permissions

| Claim | Level | Source |
|---|---|---|
| `agent -p` (`--print`) with `--output-format text\|json\|stream-json`. `--help` says print mode "has access to all tools, including write and shell". Other flags: `--force` (`--yolo`), `--mode plan\|ask` (read-only), `--sandbox enabled\|disabled`, `--approve-mcps`, `--trust`, `--workspace`. | observed (run) | `agent --help` |
| Permissions live in `~/.cursor/cli-config.json` and `<project>/.cursor/cli.json`, using the tokens `Shell(cmd)`, `Read(glob)`, `Write(glob)`, `WebFetch(domain)` and `Mcp(server:tool)`. Deny wins over allow. Print mode respects allow and deny and prompts for unlisted actions unless `--force` is set. | docs (summarised) | [C4] |
| What happens to an unlisted action in print mode with no terminal to prompt on is undetermined. | — | Needs an account to test |

## Usage and quota signal

| Claim | Level | Source |
|---|---|---|
| Usage appears on the dashboard's Spending tab as monthly pools (Cursor models, other models) with a monthly reset. Editor notifications fire at limits. No CLI command, local file, hook field or API for current usage is documented for individual users. | docs (summarised) | [C5] |
| The CLI has a configurable status line. What its payload carries is undetermined. | observed (static) | Bundled code |

## Sources

- [C1] Hooks: <https://cursor.com/docs/agent/hooks>, raw page text, read
  2026-09-26.
- [C2] Third-party hooks: <https://cursor.com/docs/reference/third-party-hooks>,
  read 2026-09-26 through a summarising fetch.
- [C3] Plugins reference: <https://cursor.com/docs/reference/plugins>, read
  2026-09-26 through a summarising fetch.
- [C4] CLI permissions: <https://cursor.com/docs/cli/reference/permissions>,
  read 2026-09-26 through a summarising fetch.
- [C5] Usage and limits: <https://cursor.com/help/models-and-usage/usage-limits>,
  read 2026-09-26 through a summarising fetch.
- Cursor Agent CLI `2026.09.26-dd393fe`, from
  `https://downloads.cursor.com/lab/2026.09.26-dd393fe/linux/x64/agent-cli-package.tar.gz`,
  run and inspected 2026-09-26.
