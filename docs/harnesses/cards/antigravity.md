# Google Antigravity

Surveyed more lightly than Codex. Antigravity ships as a desktop app
("Antigravity 2.0"), an IDE and a CLI; the documentation covers all three
and notes where they differ. Version examined: Antigravity CLI `agy 1.2.11`,
installed with the official script
(<https://antigravity.google/cli/install.sh>) and no account. Running an
agent turn needs an account, so nothing below was observed end to end. What
was observed comes from `--help`, from `agy plugin import`, which works
offline, and from strings in the binary. All sources read 2026-09-26.

Labels are defined in the [README](../README.md#verification-levels).

## Extension points

### Hooks

| Claim | Level | Source |
|---|---|---|
| Five documented events: `PreToolUse`, `PostToolUse`, `PreInvocation` (before each model call), `PostInvocation` (after each model call), `Stop`. No documented event fires on prompt submission or at session start. | docs | [A1] |
| The binary contains `SessionStartHookArgs` and `SessionStartHookResult` message types. | observed (static) | Strings in `agy` 1.2.11. Undocumented; behaviour unknown. |
| Config lives in `.agents/hooks.json` (workspace), `~/.gemini/config/hooks.json` (global), the CLI's `settings.json`, or a plugin's `hooks.json`. Format: `{"<hook-name>": {"enabled": true, "<Event>": [{"matcher": "…", "hooks": [{"type": "command", "command": "…", "timeout": 30}]}]}}`. Invocation events and `Stop` take the handler list directly, without a matcher. | docs | [A1] |
| Field names are camelCase. Every hook receives `conversationId`, `workspacePaths`, `transcriptPath` (`<app_data_dir>/brain/<id>/.system_generated/logs/transcript.jsonl`), `artifactDirectoryPath`, `modelName`. | docs | [A1] |
| `PostToolUse` receives `toolCall {name, args}`, `stepIdx`, `error`, and returns `{}`. It cannot add context. | docs | [A1] |
| `PreInvocation` receives `invocationNum` and `initialNumSteps`, but not the prompt text. It may return `injectSteps`, a list of `{toolCall}`, `{userMessage}` or `{ephemeralMessage}`, which are injected before the model is called. `PostInvocation` returns `injectSteps` and `terminationBehavior`. | docs | [A1] |
| `PreToolUse` returns `decision` (`allow`, `deny`, `ask`, `force_ask`, `deny_unless_prior_grant`), `reason`, `permissionOverrides`. `Stop` may return `decision: "continue"` with a `reason` that is injected as a system message. | docs | [A1] |
| Tool names: `view_file` (`AbsolutePath`), `write_to_file` (`TargetFile`), `replace_file_content` and `multi_replace_file_content` (`TargetFile`), `run_command` (`CommandLine`, `Cwd`), `read_url_content` (`Url`), `search_web` (`query`). | docs | [A1] |

### MCP

| Claim | Level | Source |
|---|---|---|
| The CLI reads `~/.gemini/config/mcp_config.json` and `.agents/mcp_config.json`, format `{"mcpServers": {…}}`. Remote servers (SSE, streamable HTTP) must use `serverUrl`; `url` and `httpUrl` are not supported. | docs | [A2] |
| Unconfigured MCP tools run in Ask mode. The permission tokens are `mcp(server/tool)`, `mcp(server/*)` and `mcp(*)`. | docs | [A2] |

### Rules, skills and plugins

| Claim | Level | Source |
|---|---|---|
| A plugin is a directory with a root `plugin.json` (`name`, `description`) and optional `mcp_config.json`, `hooks.json`, `skills/<name>/SKILL.md`, `agents/*.md` and `rules/*.md`. Plugins live in `.agents/plugins/` or `~/.gemini/config/plugins/`. The CLI provides `agy plugin install\|list\|enable\|disable\|uninstall\|validate\|import`. | docs; observed (run) | [A3]; `agy plugin --help` |
| `agy plugin import <path>` on this repository's `knowledge-graph/` directory reported "skills 5 processed, mcpServers 1 processed, hooks 1 processed" and staged the result in `~/.gemini/config/plugins/knowledge-graph/`. | observed (run) | |
| In that import, `hooks.json` was copied verbatim, in Claude Code's format and event names (`SessionStart`, `UserPromptSubmit`, `PostToolUse`). Neither the format nor two of those events are documented for Antigravity. | observed (run) | Whether Antigravity runs them is undetermined without an account. |
| In that import, `mcp_config.json` came out as `{"kg": {"command": "", "args": null, "cwd": "", "env": null}}`. The HTTP URL was dropped, so the imported server has no transport. | observed (run) | |

## Headless mode and its permissions

| Claim | Level | Source |
|---|---|---|
| `agy -p` (`--print`) runs one prompt. Other flags: `--output-format text\|json\|stream-json`, `--input-format stream-json` for multi-turn over stdin, `--json-schema`, `--model`, `--effort`, `--agent`, `--sandbox`, `--print-timeout`, `--dangerously-skip-permissions`. It uses cached credentials from an earlier interactive login. | docs; observed (run) | [A4]; `agy --help` |
| In headless mode, a tool that needs approval is soft-denied: the run continues, exits 0, and prints a notice to stderr. Reading and writing files inside the workspace is auto-allowed. Shell commands default to Ask. Grants go in `permissions.allow` in `~/.gemini/antigravity-cli/settings.json`, for example `command(git)` or `write_file(src/)`. | docs | [A4] |

## Usage and quota signal

| Claim | Level | Source |
|---|---|---|
| A user-defined status line command receives a JSON payload on stdin. The payload includes `quota`, a map from bucket id to `{remaining_fraction, reset_time, reset_in_seconds}`; the docs' example shows a `gemini-weekly` bucket. It also carries `transcript_path`, `session_id`, `context_window`, `cwd`. It is configured in `~/.gemini/antigravity-cli/settings.json`. | docs | [A5] |
| The CLI has `/usage` and `/credits` commands. | docs | Documentation navigation ([A1] page) |

## Sources

- [A1] Hooks: <https://antigravity.google/docs/hooks/>, raw page text, read 2026-09-26.
- [A2] MCP: <https://antigravity.google/docs/mcp/>, raw page text, read 2026-09-26.
- [A3] Plugins: <https://antigravity.google/docs/plugins/>, raw page text, read 2026-09-26.
- [A4] Headless mode: <https://antigravity.google/docs/cli/headless/>, raw page text, read 2026-09-26.
- [A5] Status line: <https://antigravity.google/docs/cli/statusline/>, raw page text, read 2026-09-26.
- Antigravity CLI `agy 1.2.11`, from the install script above, run and
  inspected 2026-09-26.
