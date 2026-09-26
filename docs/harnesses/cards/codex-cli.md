# Codex CLI

Version examined: `codex-cli 0.157.1`, installed from npm (`@openai/codex`,
published 2026-09-26). All sources read 2026-09-26.

## How it was examined

Codex was installed into a scratch directory with an isolated `CODEX_HOME`.
No OpenAI account was used. Instead, `config.toml` pointed Codex at a local
mock of the Responses API:

```toml
model_provider = "mock"
[model_providers.mock]
name = "mock"
base_url = "http://127.0.0.1:<port>/v1"
wire_api = "responses"
```

The mock logged each request body and replied with canned events: a text
answer, an `exec_command` call, a `kg_search` MCP call, or an `apply_patch`
call. Reading the logged request shows exactly what Codex put in front of
the model, including where hook output lands. This repository's server
(v0.9.42) ran on localhost with a scratch storage root, and its unmodified
hooks and plugin were installed into Codex. What this method cannot show is
how OpenAI's backend and models treat that input; it shows only what Codex
sends.

Labels are defined in the [README](../README.md#verification-levels).

## Extension points

### Hooks

| Claim | Level | Source |
|---|---|---|
| Hooks are a stable feature, on by default (`hooks  stable  true`). | observed (run) | `codex features list` |
| Hooks are configured in `hooks.json` or in `[hooks]` tables of `config.toml`, at `~/.codex/` and `<repo>/.codex/`; project-local hooks need a trusted `.codex/` layer. Plugins bundle hooks at `hooks/hooks.json` or at a path named in the manifest. | docs | [S1] |
| The file format is Claude Code's: `{"hooks": {"<Event>": [{"matcher": "…", "hooks": [{"type": "command", "command": "…"}]}]}}`. | observed (run) | This repository's `knowledge-graph/hooks/hooks.json` ran unchanged. |
| Events: `SessionStart`, `SessionEnd`, `SubagentStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SubagentStop`, `Stop`, `Interrupt`. | docs; observed (static) | [S1]; the binary embeds an input and output JSON schema for each. |
| Every hook receives `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`; turn-scoped hooks add `turn_id`. `SessionStart` adds `source` (`startup`, `resume`, `clear`, `compact`). `UserPromptSubmit` adds `prompt`. `PostToolUse` adds `tool_name`, `tool_input`, `tool_response`, `tool_use_id`. | observed (run) | Payloads captured verbatim from hook stdin. |
| Hooks may return `continue`, `stopReason`, `systemMessage`, `suppressOutput`, `decision`, `reason`, and `hookSpecificOutput: {hookEventName, additionalContext}` for `SessionStart`, `UserPromptSubmit` and `PostToolUse` (`PostToolUse` also takes `updatedMCPToolOutput`). | observed (static) | Output schemas in the binary, titled `user-prompt-submit.command.output` and so on. |
| `additionalContext` becomes a `developer` message in the next model request. For `SessionStart` it follows the environment context. For `UserPromptSubmit` it directly follows the user's message. For `PostToolUse` it sits between the tool call and its output. | observed (run) | Request bodies captured at the mock. |
| For `SessionStart` and `UserPromptSubmit`, plain text on stdout also becomes developer context. | docs | [S1] |
| Hook output longer than about 2,500 tokens is cut to about 10,100 characters, keeping the head and the tail. It is prefixed `Warning: truncated output (original token count: N)`, and the full text is saved to `/tmp/hook_outputs/<session>/<id>.txt`. | observed (run) | 12,000- and 40,000-character payloads were truncated; this repository's 9,680-character preload was not. |
| A handler can raise its own limit with `additionalContextLimit`. | docs | [S1], not tested |
| A shell command reaches hooks as `tool_name: "Bash"`, `tool_input: {"command": "…"}`, even though the model-facing tool is `exec_command`. | observed (run) | `PreToolUse` and `PostToolUse` payloads |
| A file edit reaches hooks as `tool_name: "apply_patch"`, `tool_input: {"command": "*** Begin Patch\n*** Update File: notes.txt\n…"}`. A matcher of `Edit\|Write` matches it. | observed (run); docs | Captured payload; [S1] says `Edit` and `Write` are aliases. |
| MCP tools are matched by full name (`mcp__server__tool`). Hosted tools such as web search do not pass through tool hooks. | docs | [S1] |
| New or changed hooks are skipped until the user trusts them in `/hooks`; trust is recorded against the hook's hash. `--dangerously-bypass-hook-trust` skips the check for one run. | docs; observed (run) | [S1]. The same configuration ran no hooks without the flag and all of them with it. |
| Hook commands get `PLUGIN_ROOT` and `PLUGIN_DATA`, plus `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` for compatibility. | docs; observed (run) | [S1]. `${CLAUDE_PLUGIN_ROOT}` in this repository's `hooks.json` resolved to the installed plugin. |
| Default hook timeout is 600 s (`SessionEnd` and `Interrupt`: 1 s). `"async": true` runs a hook in the background, at most 8 per session. | docs | [S1] |

### MCP

| Claim | Level | Source |
|---|---|---|
| Stdio and streamable HTTP servers: `codex mcp add <name> --url <url>`, or `[mcp_servers.<name>] url = "…"`. | observed (run) | `codex mcp add --help`; this repository's server exposed its ten tools to the model under the namespace `mcp__kg`. |
| A plugin's `.mcp.json` is loaded; this repository's `{"type": "http", "url": …}` entry worked as-is. | observed (run) | Plugin install, then a session |
| Per-server `enabled_tools` and `disabled_tools`, and `default_tools_approval_mode`, one of `auto`, `prompt`, `writes`, `approve`. | observed (run) | Config accepted; the enum came from Codex's own error message. |

### Skills and instructions

| Claim | Level | Source |
|---|---|---|
| Plugin skills are listed to the model in a developer message (`<skills_instructions>`) with name, description and file path. All five of this repository's skills appeared as `knowledge-graph:kg-*`, including `kg-core`, which declares `user-invocable: false`. | observed (run) | Captured request |
| Instruction files `AGENTS.md` and `AGENTS.override.md`; config keys `developer_instructions`, `model_instructions_file`, `project_doc_fallback_filenames`. | observed (static) | Strings in the binary |

## Packaging and distribution

| Claim | Level | Source |
|---|---|---|
| `codex plugin marketplace add <path or git>`, `codex plugin add <plugin>@<marketplace>`, `codex plugin list`, `codex plugin remove`. | observed (run) | `codex plugin --help` |
| The native manifest is `.codex-plugin/plugin.json` with `skills`, `hooks`, `mcpServers`, `apps` and `interface` fields. The native marketplace file is `.agents/plugins/marketplace.json`. | observed (static) | A plugin-JSON spec and paths embedded in the binary |
| Codex also reads `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` and `.cursor-plugin/…`. | observed (static); observed (run) | This repository's marketplace was added and `knowledge-graph@maxim-plugins` installed with no changes, into `$CODEX_HOME/plugins/cache/maxim-plugins/knowledge-graph/0.9.42`. |
| Codex contains an importer for Claude Code configuration (`enabledPlugins`, `known_marketplaces.json`, `CLAUDE.md`). | observed (static) | Strings in the binary; not exercised |

## Headless mode and its permissions

| Claim | Level | Source |
|---|---|---|
| `codex exec` runs non-interactively, with `--json` (JSONL events), `--output-schema`, `--ephemeral`, `-s read-only\|workspace-write\|danger-full-access`, `--disable <feature>`, `-c key=value`, `--ignore-user-config`, `--ignore-rules`. | observed (run) | `codex exec --help` |
| This command offered the model no shell and no web search, and only two KG tools: `codex exec --ephemeral -s read-only --disable shell_tool -c web_search="disabled" -c 'mcp_servers.kg.enabled_tools=["kg_read","kg_search","kg_put_node"]'`. The tools offered were MCP resource helpers, `request_user_input`, `view_image`, the `multi_agent_v1` namespace, the goal tools, and `mcp__kg` with only the allowed tools. | observed (run) | Tool list in the captured request |
| In `exec`, an MCP call fails with "MCP tool call requires approval, but approval policy is never" unless the server is set to `default_tools_approval_mode = "approve"`. With that set, the call completed and returned results from this repository's server. | observed (run) | |
| The `multi_agent_v1` tools stayed available in that run. Whether `--disable multi_agent` removes them was not tested. | observed (run) | |

## Usage and quota signal

| Claim | Level | Source |
|---|---|---|
| Codex parses response headers `x-codex-primary-used-percent`, `-primary-window-minutes`, `-primary-reset-at`, and the `secondary` equivalents. | observed (static) | Strings in the binary |
| Codex writes them into the session rollout file (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`) on each turn, as a `token_count` event carrying `rate_limits.primary` and `rate_limits.secondary`, each `{used_percent, window_minutes, resets_at}`. | observed (run) | The mock sent those headers with 300- and 10,080-minute windows, and the rollout recorded them. |
| OpenAI's real backend sends these headers for ChatGPT-plan users with a 5-hour primary and a weekly secondary window. | secondhand | Inferred from field names and the values used in the test; needs an account to confirm. |
| `codex exec --json` did not print the rate limits to stdout in the test run. | observed (run) | |
| The TUI status line is configured by choosing built-in items (`/statusline`), not by a user command, so nothing like Claude Code's status line script receives the figures. | observed (static) | Strings in the binary |

## Session identity and transcripts

| Claim | Level | Source |
|---|---|---|
| `session_id` is a UUID. `transcript_path` is the rollout file under `$CODEX_HOME/sessions/`. The rollout stores developer messages verbatim, including hook context. | observed (run) | |
| This repository's transcript-marker regex (`knowledge-graph/server/mcp_http/session_manager.py:20-24`) recovered the KG session id from a Codex rollout. | observed (run) | |

## Sources

- [S1] Codex hooks documentation: <https://developers.openai.com/codex/hooks>,
  which redirects to <https://learn.chatgpt.com/docs/hooks>. Read 2026-09-26
  through a summarising fetch. The claims taken from it were cross-checked
  against the binary or a run where the table says so.
- Codex CLI 0.157.1: npm package `@openai/codex`, run and inspected
  2026-09-26.
