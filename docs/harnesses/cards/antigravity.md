# Google Antigravity CLI

Version examined: Antigravity CLI `agy 1.2.14`, installed with the official
script (<https://antigravity.google/cli/install.sh>) on 2026-10-01. This card
replaces the lighter survey of `agy 1.2.11` (2026-09-26), which was written
without running an agent turn. Antigravity also ships as a desktop app
("Antigravity 2.0") and an IDE; they share the hooks, plugin and MCP files
under `~/.gemini/config/`, but only the CLI was examined.

All sources read 2026-10-01. Labels are defined in the
[README](../README.md#verification-levels).

## How it was examined

The CLI accepts a Gemini API key instead of a signed-in account, and sends
model requests to `GOOGLE_GEMINI_BASE_URL` when it is set [A7]. It was pointed
at a local mock of the Gemini API that logged every request body and replied
with scripted tool calls or text, as the Codex review did with the Responses
API. Hooks were logged on every event. This repository's server (v0.10.2)
ran on a scratch port behind a proxy that logged each MCP request. Everything
ran under a scratch `HOME`. The tools and the procedure are in
[`tools/antigravity-probe/`](../tools/antigravity-probe/README.md).

The model requests went to
`POST /v1beta/models/<model>:streamGenerateContent?alt=sse` with the user agent
`google-genai-sdk/1.71.0 gl-go/…`, so the CLI uses the ordinary Gemini API in
this mode.

What this method cannot show: anything that needs a signed-in account
(quota figures, the marketplace, the desktop app's sync), and how a real
model weighs injected text. The mock shows placement, not effect.

Corrections to the 2026-09-26 card, each observed below: `SessionStart` exists
and can inject context; the CLI's app data directory is
`~/.gemini/antigravity-cli`, not `~/.gemini/antigravity`; the transcript a
hook is given is `transcript_full.jsonl`; `agy plugin install` stages into
`~/.gemini/config/plugins/`, not `~/.gemini/antigravity-cli/plugins/`.

## Extension points

### Hooks: events and configuration

| Claim | Level | Source |
|---|---|---|
| Documented events: `PreToolUse`, `PostToolUse`, `PreInvocation` (before each model call), `PostInvocation` (after each model call), `Stop`. | docs | [A1] |
| `SessionStart` is also accepted in `hooks.json`, listed by `agy -p /hooks`, and fires once when a conversation is created, before the first model call. It is undocumented. | observed (run) | |
| `UserPromptSubmit` is dropped from `hooks.json` without an error. The binary has no trace of the name. | observed (run); observed (static) | |
| Config files: `~/.gemini/config/hooks.json`, `<workspace>/.agents/hooks.json`, a plugin's root `hooks.json`, and a custom agent's `hooks:` list. Format: `{"<hook-name>": {"enabled": true, "<Event>": […]}}`. `PreToolUse` and `PostToolUse` take `[{"matcher": "<regex>", "hooks": [handler…]}]`; the other events take the handler list directly. A handler is `{"type": "command", "command": "…", "timeout": 30}`. | docs; observed (run) | [A1] |
| A hook command runs through `sh -c`. Its working directory is the directory of the `hooks.json` that defined it: `~/.gemini/config/` for global hooks, the plugin root for plugin hooks. It is not the workspace. | observed (run) | |
| A hook inherits the CLI's environment and gains `ANTIGRAVITY_CONVERSATION_ID`. No plugin-root variable is exported. | observed (run) | |
| `${PLUGIN_ROOT}` in a plugin hook command expands to an empty string, so `${PLUGIN_ROOT}/hooks/x.sh` runs `/hooks/x.sh`. A relative path such as `hooks/x.sh` works, because the command runs in the plugin root. `${CLAUDE_PLUGIN_ROOT}` is not substituted either. | observed (run) | The binary does contain `${PLUGIN_ROOT}` and `${PLUGIN_DATA}` handling, apparently for MCP server definitions; not tested. |
| One invalid entry makes the CLI drop every hook in that file. A plugin whose `hooks.json` has a handler without `command` loads no hooks at all. | observed (run) | CLI log: `failed to parse hooks for plugin … command hook must specify 'command'` |
| Global, workspace and plugin hooks all ran in headless mode without a trust prompt. Whether the interactive TUI asks first is untested. | observed (run) | The 1.2.13 changelog mentions a workspace trust dialog [A13]. |
| When several hooks handle the same event, their order varied between runs. | observed (run) | |
| A hook that fails (non-zero exit, timeout) is logged and the turn continues. | observed (run); docs | [A13] |
| Default handler timeout is 30 s. | docs | [A1] |
| Handlers also have an undocumented `prompt` type (`{prompt, model}`) that calls a model instead of running a command. | observed (static) | `HookHandlerConfig` in the embedded `hooks.proto` |

### Hooks: what a hook receives

Field names are camelCase. Payloads below were captured from hook stdin.

| Claim | Level | Source |
|---|---|---|
| Every hook receives `conversationId` (a UUID), `workspacePaths` (array), `transcriptPath`, `artifactDirectoryPath` and `modelName`. There is no `cwd` field. | observed (run) | |
| `transcriptPath` is `~/.gemini/antigravity-cli/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl`. The documentation names `transcript.jsonl` and `~/.gemini/antigravity` [A1]; both files exist, and hooks are given the `_full` one. | observed (run) | |
| `SessionStart` receives only the common fields. There is no `source` (startup, resume, clear) as in Claude Code and Codex. | observed (run) | |
| `PreInvocation` and `PostInvocation` receive `invocationNum` and `initialNumSteps`. `invocationNum` restarts at 0 for every user prompt and counts the model calls within that prompt's turn. `initialNumSteps` is the number of steps already in the transcript. | observed (run) | Two prompts in one process: 0, 1, then 0, 1 again. |
| The prompt text is not in any payload. The transcript already holds it when `PreInvocation` runs: its line count equals `initialNumSteps`, and its last `USER_INPUT` step has the prompt inside `<USER_REQUEST>…</USER_REQUEST>`, followed by CLI metadata blocks. | observed (run) | |
| `PreToolUse` and `PostToolUse` receive `toolCall: {name, args}` and `stepIdx`; `PostToolUse` adds `error`. The tool's output is not included. | observed (run) | |
| `Stop` receives `executionNum`, `terminationReason` (`NO_TOOL_CALL` when the model answered), `error`, `fullyIdle`. | observed (run) | |
| The embedded schema has more fields than the JSON hook receives: `lastUserInput`, `agentName` and `parentConversationId` in the common args, `result` in `PostToolUse`, `modelOutput` in `PostInvocation`. None appeared in a payload. | observed (static); observed (run) | `third_party/jetski/hooks_pb/hooks.proto`, decoded from the binary |

Tool names and arguments, as the model is offered them and as hooks see them:

| Tool | Arguments a hook sees | Level |
|---|---|---|
| `view_file` | `AbsolutePath`, `StartLine`, `EndLine` | observed (run) |
| `run_command` | `CommandLine`, `Cwd` (required), `WaitMsBeforeAsync` | observed (run) |
| `write_to_file` | `TargetFile`, `CodeContent`, `Overwrite` | observed (static): request tool schema |
| `replace_file_content`, `multi_replace_file_content` | `TargetFile`, … | observed (static): request tool schema |
| `read_url_content` | `Url` | observed (static): request tool schema |
| `search_web` | `query`, `domain` | observed (static): request tool schema |
| `call_mcp_tool` | `ServerName`, `ToolName`, `Arguments` | observed (run) |

Every built-in tool also requires `toolSummary` and `toolAction`. The legacy
`list_dir`, `find_by_name` and `grep_search` left the default tool set in
1.2.7 [A13].

### Hooks: what a hook may return

A reply is parsed with protojson into the event's result message.

| Claim | Level | Source |
|---|---|---|
| A reply with a key the result message does not have, a value of the wrong type, or plain text is discarded whole. The model sees nothing, the user sees nothing, and only the CLI log records it. Plain text on stdout is therefore never context, unlike Claude Code and Codex. | observed (run) | CLI log: `failed to unmarshal result … via protojson` |
| `SessionStart`, `PreInvocation` and `PostInvocation` may return `injectSteps`. A step is one of `userMessage` (string), `ephemeralMessage` (string), `systemMessage` (`{"systemMessage": "…"}`), `toolCall` (`{"name", "args"}`), `hookUserMessage`, `hookEphemeralMessage` (`{"content"}`), `errorMessage`, `checkpoint`. Only the first three are documented. | observed (static); docs | `HookInjectedStep` in the embedded schema; [A1] |
| A `toolCall` injected from `SessionStart` failed the turn: `unknown injected step type`, exit code 3. | observed (run) | |
| `PostToolUse` returns `{}`. The schema's `overwriteResult` was accepted but changed nothing. | observed (run); docs | [A1] |
| `PreToolUse` must return `decision` (`allow`, `deny`, `ask`, `force_ask`, `deny_unless_prior_grant`). A reply of `{}` denies the tool. | observed (run); docs | [A1] |
| In headless mode, `decision: "allow"` from `PreToolUse`, with or without `permissionOverrides`, did not satisfy the MCP permission check; the call was still refused. A hook can deny but could not grant. | observed (run) | |

### Hooks: where injected text lands

Observed by reading the model requests the mock received.

| Injected step | Where it lands | Persists to later model calls | Level |
|---|---|---|---|
| `systemMessage` from `SessionStart` | A user-role part right after the first prompt, framed: `The following is a <SYSTEM_MESSAGE> not actually sent by the user… The following system message was injected by a SessionStart hook: <text>` | Yes, and after resume | observed (run) |
| `systemMessage` from `PreInvocation`, `invocationNum` 0 | The same framing, right after the prompt that started the turn | Yes | observed (run) |
| `systemMessage` from `PreInvocation`, `invocationNum` 1 or more | Appended to the end of the preceding tool result's text | Yes | observed (run) |
| `ephemeralMessage` | Same positions, without framing | No: only the next model call sees it | observed (run) |
| `userMessage` | `<USER_REQUEST>…</USER_REQUEST>`, indistinguishable from the user typing it; repeated on every call it is injected, and each counts as a user turn (`num_turns` 3 for one prompt) | Yes | observed (run) |

Injected steps are written to the transcript (`SYSTEM_MESSAGE`,
`EPHEMERAL_MESSAGE`), so their text can be read back later. *(observed (run))*

| Claim | Level |
|---|---|
| A `systemMessage` of about 20,000 characters arrived whole. One of 58,443 characters was cut after 48,926 characters, keeping the head, and ended `<truncated 9535 bytes>`. | observed (run) |

### MCP

| Claim | Level | Source |
|---|---|---|
| Servers are configured in `~/.gemini/config/mcp_config.json`, `.agents/mcp_config.json`, a plugin's `mcp_config.json`, or with `agy mcp add <name> <url>`, which wrote `{"serverUrl": …, "disabled": false}`. Remote servers use `serverUrl`; the desktop app's changelog says `url` is now accepted too [A13]. | observed (run); docs | [A2] |
| This repository's server worked unchanged. The client speaks MCP `2026-07-28`: it opened with `server/discover` and sent an `Mcp-Method` header on each request. | observed (run) | |
| The model is not given the MCP tools. It is given one generic tool, `call_mcp_tool(ServerName, ToolName, Arguments)`, and the system prompt lists each server's tool names under "Lazy". The schemas, descriptions included, are written to `~/.gemini/antigravity-cli/mcp/<server>/<tool>.json`, and the prompt tells the model to read a schema file before calling a lazy tool. | observed (run) | |
| An "Eager" mode registers tools natively as `mcp_<server>_<tool>`. `"eager": true` on the server entry did not enable it; the binary names `force_all_tools_eager` and a `tools.eager` setting, untested. | observed (run); observed (static) | |
| A server's MCP `instructions`, if it sends any, are written to `instructions.md` in the same directory for the model to read on demand. This repository's server sends none. | observed (run) | System prompt text |
| The HTTP client's User-Agent is `Go-http-client/1.1`. The client names itself in `_meta["io.modelcontextprotocol/clientInfo"]` as `antigravity-client` `v1.0.0`. | observed (run) | |
| Every `tools/call` carries `_meta["antigravity.google/conversation_id"]`, the same id hooks receive, and `_meta["antigravity.google/artifacts_dir"]`. | observed (run) | |
| A plugin's MCP server is named `<plugin>_<server>`: a plugin `kg-native` with server `kgp` appeared as `kg-native_kgp`. Permission tokens and `ServerName` use that name. | observed (run) | |
| Unconfigured MCP calls default to Ask. Tokens: `mcp(server/tool)`, `mcp(server/*)`, `mcp(*)`, in `permissions.allow`, `deny` or `ask` of `~/.gemini/antigravity-cli/settings.json`. | docs; observed (run) | [A2], [A6] |
| A tool result longer than 4,096 characters is not shown to the model. The result is saved to `brain/<conversationId>/.system_generated/steps/<n>/output.txt`, and the model receives only `The output was large and was saved to: file://…`. 4,096 characters arrived inline; 4,100 did not. | observed (run) | Measured with a stub MCP server |
| `view_file` returns at most 800 lines per call, with a note to call again for the rest. | observed (run) | A 44,420-byte file |

### Rules, skills and plugins

| Claim | Level | Source |
|---|---|---|
| A plugin is a directory with a root `plugin.json` (`name` required; the published schema allows only `name` and `description`), and optional root `hooks.json`, `mcp_config.json`, `skills/<name>/SKILL.md`, `rules/*.md`, `agents/`. | docs; observed (run) | [A3] |
| `agy plugin install <dir>` copies the directory to `~/.gemini/config/plugins/<name>/`. Installing from a git URL is refused (`install target must be a directory`). `<name>@<marketplace>` resolves only marketplaces the CLI knows: `unknown marketplace: maxim-plugins`. How a third party registers a marketplace was not found. | observed (run) | The docs point third parties to an interest form [A10]. |
| A plugin's `always_on` rule is placed in the system prompt, under "user-defined rules that you MUST ALWAYS FOLLOW WITHOUT ANY EXCEPTION". Rules share a 20,000-token budget; a rule file is cut at 24,000 bytes. | observed (run); docs | [A8] |
| Plugin skills are listed in the system prompt with name, description and path, and become slash commands. | observed (run); docs | [A9] |
| `agy plugin import <path>` (also `import claude`, `import gemini`) converted this repository's `knowledge-graph/`: skills 5, MCP servers 1, hooks 1. It copied the whole directory, wrote a root `plugin.json` with `author` and `version`, copied `hooks.json` verbatim in Claude Code's format, and wrote `mcp_config.json` as `{"kg": {"command": "", "args": null, "cwd": "", "env": null}}`, dropping the URL. | observed (run) | |
| In the imported plugin, the Claude Code `hooks.json` reads as one hook named `hooks`. Its `SessionStart` entry has no `command` in Antigravity's shape, so the CLI dropped all of the plugin's hooks; the `PostToolUse` matcher names only Claude Code tools. The imported plugin therefore runs no hooks and has no MCP transport. | observed (run) | |
| A plugin dropped directly into `~/.gemini/config/plugins` whose MCP server needs configuration variables starts disabled. | docs | [A13], 1.2.11 |

### Custom agents

| Claim | Level | Source |
|---|---|---|
| A custom agent is `<dir>/agent.md` with YAML front matter, in `~/.gemini/config/agents/` or `<workspace>/.agents/agents/`, selected with `--agent <name>`. A workspace agent in the run's working directory loaded in headless mode. | docs; observed (run) | [A12] |
| `tools: []` left the model `call_mcp_tool`, `list_resources`, `read_resource` and `manage_task`: no shell, no file tools, no web. `inheritMcp: false` removes `call_mcp_tool`. | observed (run) | |
| Front matter keys found in the binary include `tools`, `hooks`, `mcpServers`, `inheritMcp`, `inheritCustomizations`, `excludeDefaultComponents`, `preloadSkills`, `model`. An `mcpServers` map made the agent fail to load. | observed (static); observed (run) | |
| An agent that fails to load is replaced by the default agent, with every tool, and the run goes on. Only the CLI log says so: `Agent "kg-maintainer" not found, falling back to default`. | observed (run) | |
| An agent's `hooks:` entry must be an absolute path in the CLI: a relative one was ignored (`AgentBasePath is not set`). | observed (run) | |

## Sessions, subagents and transcripts

| Claim | Level | Source |
|---|---|---|
| `--conversation <id>` resumes in a new process under the same `conversationId` and appends to the same transcript. `SessionStart` does not fire again. Context injected before the resume is still in the model's input. | observed (run) | |
| Subagents (`invoke_subagent`) run as conversations of their own, each with its own `conversationId`, transcript, `SessionStart` and `PreInvocation` hooks. | observed (run) | |
| `~/.gemini/antigravity-cli/conversation_summaries.db` (SQLite) has one row per conversation: `conversation_id`, `title`, `workspace_uris`, `step_count`, `last_modified_time`, `parent_conversation_id`, `nesting_depth`, `agent_name`, `last_user_input_time`. Each conversation also has `conversations/<id>.db`. | observed (run) | |
| A transcript line is one step: `step_index`, `source` (`USER_EXPLICIT`, `MODEL`, `SYSTEM_SDK`), `type` (`USER_INPUT`, `PLANNER_RESPONSE`, `GENERIC` for a tool result, `SYSTEM_MESSAGE`, `EPHEMERAL_MESSAGE`), `status`, `created_at`, and `content` or `tool_calls`. In `transcript.jsonl` tool arguments are JSON-encoded strings; in `transcript_full.jsonl` they are objects. | observed (run) | |
| A `kg_read` result in the transcript kept this server's `Session: <id>` footer. | observed (run) | |
| `/fork` clones a conversation into a new id; `/clear` starts a new one. Context compaction exists and rewrites the transcript. None of the three was tested. | docs | [A11], [A13] |
| Each conversation gets `brain/<id>/scratch/`, a working directory for the agent's own files. | observed (run) | |

## Headless mode and its permissions

| Claim | Level | Source |
|---|---|---|
| `agy -p <prompt>` runs one prompt. `-p` requires an argument; a prompt on stdin needs `--input-format stream-json --output-format stream-json`. `--output-format json` prints one envelope; `stream-json` starts with an `init` event that lists the tools offered. | observed (run); docs | [A4] |
| A tool that needs approval is soft-denied: the run exits 0, `denied_actions` names it, stderr explains it. In this version the turn also ended there, with an empty response, instead of returning the denial to the model. | observed (run) | |
| Reading and writing files inside the workspace is allowed by default; `deny` rules override that, and deny beats ask beats allow. | docs | [A6] |
| There is no flag or variable to point the CLI at another configuration directory, so a headless run uses the user's own settings, hooks and plugins. | observed (static) | No `--settings` in `--help`; no such variable in the binary |
| `agy -p "/hooks"`, `"/usage"`, `"/credits"`, `"/model"` answer without a model call, and `--output-format json` gives a structured payload. | observed (run) | |

## Usage and quota signal

| Claim | Level | Source |
|---|---|---|
| Google AI Pro and Ultra quotas refresh every five hours up to a weekly limit; other plans have a weekly quota. | docs | [A14] |
| `agy -p /usage --output-format json` returns `command.data.groups`, which was empty under an API key. The binary's schema has `groups`, `buckets`, `bucket_id`, `remaining_fraction`, `reset_time`, `reset_in_seconds`. The values under a signed-in account were not seen. | observed (run); observed (static) | |
| A status line command receives a `quota` map of bucket to `{remaining_fraction, reset_time, reset_in_seconds}`; the docs' example has one bucket, `gemini-weekly`. It runs only while the interactive TUI is open. Under an API key the payload had no `quota`; before a conversation existed, its `transcript_path` pointed at `~/.gemini/antigravity/…`, not the CLI's directory. | docs; observed (run) | [A5] |
| With `useG1Credits` (default off), a signed-in user's model calls continue on paid AI credits once plan quota is exhausted. No per-run switch was found. | docs | [A7], [A14] |
| Under an API key, a run stops at once when the Gemini API reports an exhausted daily quota or spend cap (1.2.12). | docs | [A13] |

## Built-in memory

| Claim | Level | Source |
|---|---|---|
| The CLI's system prompt had no memory or knowledge section; its sections were `identity`, `user_information`, `mcp_servers`, `user_rules`, `skills`, `subagents`, `messaging`, `conversation_transcript`, `artifacts`, `slash_commands`, `guidelines`, `communication_style`. | observed (run) | API-key mode |
| The IDE has "knowledge items" and a setting to turn them off. | docs | [A13] |

## Sources

All pages were fetched as HTML and read as page text on 2026-10-01.

- [A1] Hooks: <https://antigravity.google/docs/hooks/>
- [A2] MCP: <https://antigravity.google/docs/mcp/>
- [A3] Plugins: <https://antigravity.google/docs/plugins/>
- [A4] Headless mode: <https://antigravity.google/docs/cli/headless/>
- [A5] Status line: <https://antigravity.google/docs/cli/statusline/>
- [A6] Permissions: <https://antigravity.google/docs/permissions/>
- [A7] Installation and auth: <https://antigravity.google/docs/cli/install/>; AI credits: <https://antigravity.google/docs/cli/credits/>
- [A8] Rules: <https://antigravity.google/docs/rules/>
- [A9] Skills: <https://antigravity.google/docs/skills/>
- [A10] Marketplace: <https://antigravity.google/docs/marketplace/>
- [A11] Conversations: <https://antigravity.google/docs/cli/conversations/>
- [A12] Agents command: <https://antigravity.google/docs/cli/commands/agents/>; subagents: <https://antigravity.google/docs/subagents/>
- [A13] Changelog: <https://antigravity.google/docs/changelog/>, and `agy changelog` for 1.2.13 and 1.2.14
- [A14] Plans: <https://antigravity.google/docs/plans/>
- Antigravity CLI `agy 1.2.14`, run and inspected 2026-10-01. Static claims
  come from `strings` over the binary and from the `hooks.proto` file
  descriptor embedded in it.
