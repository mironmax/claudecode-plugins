# Antigravity CLI: mapping the Codex integration

> **Status: proposal.** Every recommendation here is for the maintainer to
> accept, change or reject. The facts are in the
> [Antigravity card](cards/antigravity.md), each with its verification level,
> observed on `agy 1.2.14` against this repository's server v0.10.2 on
> 2026-10-01. Where this document recommends something, it says so.

The Codex integration (0.10.0 to the unreleased fixes) taught a list of
nuances, most of them found only after the first version shipped. This
document takes each one, says what Antigravity CLI does at that point, and
proposes a mapping. It also reports two problems Codex did not have.

## 1. The deciding questions

### Can the server's context reach the model at the three moments?

**Yes, at all three, through one instrument:** a `systemMessage` step that a
hook injects. Observed by reading the requests the CLI sent to a mock model:

| Moment | Claude Code | Codex CLI | Antigravity CLI |
|---|---|---|---|
| Session start | `SessionStart`, `additionalContext` | Same, a developer message | `SessionStart` (undocumented) returning `injectSteps: [{systemMessage}]`. Lands right after the first prompt, framed as a system message "not actually sent by the user". |
| Each prompt | `UserPromptSubmit`, `additionalContext` | Same, right after the prompt | `PreInvocation` with `invocationNum` 0, which marks a new prompt. Same landing place and framing. The prompt text is read from the transcript. |
| After a tool | `PostToolUse`, `additionalContext` | Same, between the call and its result | `PostToolUse` cannot inject. The next `PreInvocation` can, and the CLI appends its message to the end of that tool's result. |

All three persist into later model calls, as in the other two harnesses. The
documented `ephemeralMessage` does not: it is gone after one call. The
documented `userMessage` persists but reads as the user's own words.

The 2026-09-26 review, which read only the documentation, found no
session-start event, expected tool recall to lag one model call, and named
`PreInvocation` with an `ephemeralMessage` as the nearest instrument. Running
the CLI showed a session-start event, no lag (the tool result and the
injected text reach the model in the same request), and that
`ephemeralMessage` would have been the wrong step: it is gone from the next
call. The remaining differences are in mechanics: replies are strict JSON,
the prompt is not in the payload, and tool recall needs a hand-off between
two hooks.

### Does reading memory through MCP work as elsewhere?

**No. This is the new hard problem** (section 4). Two observed facts:

1. A tool result over 4,096 characters is saved to a file, and the model is
   given only its path. `kg_read` renders up to 40,000 characters and
   `kg_search` up to 10,000. The server marks what it rendered as seen, so
   a render the model never opened would be suppressed from later recall.
2. MCP tools are lazy. The model sees their names but not their descriptions,
   and must read a schema file before its first call. Some of the plugin's
   doctrine lives in those descriptions.

## 2. Nuance by nuance

"Where" points at the code or changelog entry that carries the Codex lesson.
Each row's facts are in the [card](cards/antigravity.md); a fact not observed
by running says so.

### A. Who is calling: harness, session, project

| # | Codex lesson (where) | Antigravity | Proposed mapping |
|---|---|---|---|
| A1 | Hook events are told apart structurally, by the transcript path, because Codex mirrors Claude Code's fields (`harness.py:54-60`). | The payload differs wholesale (camelCase, `conversationId`, `workspacePaths`, no `cwd`), so a dedicated adapter script is needed anyway. The transcript lives under `~/.gemini/antigravity-cli/brain/…` (`antigravity-ide` and `antigravity` for the other surfaces). | The adapter says which harness it is in what it posts (`"harness": "antigravity"`), answering [proposal](proposal.md) open question 4 for this harness. `from_transcript` also recognises `/.gemini/antigravity` as a fallback. |
| A2 | MCP calls are told apart by User-Agent; 0.10.2 had to read the SDK's real request headers to get it right (`mcp_streamable_server.py:765`). | The User-Agent is `Go-http-client/1.1`. Today the server records an Antigravity session as `claude-code` (observed). The client names itself in `_meta` `clientInfo` as `antigravity-client`, and every call carries `antigravity.google/conversation_id`. | Detect from `params.meta` (the MCP 2.x library exposes it, checked) before falling back to the User-Agent. |
| A3 | The harness session id is bound to a KG session; a session `kg_read` registered stays unbound until a hook binds it, which is where F12 lived (Unreleased). | `conversationId` is the same in hooks and in every MCP call's `_meta`. | Map `conversationId` to `session_id`. *Recommendation:* also bind on the first MCP call that carries a conversation id. An Antigravity session is then never unbound, which removes the window F12 needed. |
| A4 | Project from the hook's `cwd`, with `CLAUDE_PROJECT_DIR` or `$PWD` as fallback (`kg-autostart.sh:40`). | No `cwd`. `workspacePaths` is a list (`--add-dir` adds more). A hook's working directory is its `hooks.json` directory or the plugin root, never the workspace. | Use the first workspace path. Never fall back to `$PWD`. Multi-root workspaces are open question 5. |
| A5 | `source` (`startup`, `resume`, `clear`, `compact`) decides reuse and re-rendering; forks are recovered from transcript markers and cloned (`rest.py:93-199`, 0.10.2 F8). | No `source`. `SessionStart` fires for a new conversation only. A resume (`--conversation`) fires nothing, keeps the id, and keeps the injected preload in context. `/fork`, `/clear` and compaction were not tested. | A `SessionStart` is a new conversation. Marker recovery stays as it is: the transcript passes `safe_transcript_path`, and the `Session:` footer was found in it. A resumed conversation resolves through its binding, or through its markers once the binding has expired. Compaction is the risk: no JSON hook sees it, and a summarised preload is lost (section 6). |
| A6 | Subagents get no preload in Claude Code, and the per-prompt nudge tells the model to brief them. | Every subagent is a conversation of its own and fires `SessionStart` and `PreInvocation`. The payload does not say it is a subagent; `conversation_summaries.db` does (`parent_conversation_id`, `nesting_depth`). | Open question 3. *Recommendation:* detect subagents and give them a short pointer instead of a full preload, so delegation does not multiply the preload cost. |
| A7 | A folderless Codex app chat runs in `~/Documents/Codex/<date>/<slug>` and is user-only (`constants.py:505-527`). | The CLI runs in the directory it was started from, and the home directory is already user-only. The desktop app was not examined. | Nothing for the CLI. Check the desktop app before claiming it. |

### B. Putting context in front of the model

| # | Codex lesson (where) | Antigravity | Proposed mapping |
|---|---|---|---|
| B1 | The same hook envelope served both harnesses (`rest.py:307-312`, `327-332`). | A reply must be exactly the event's result message. One unknown key, a wrong type, or plain text discards the whole reply, and only the CLI log says so. | The profile owns the envelope, as the proposal planned: for Antigravity, `{"injectSteps": [{"systemMessage": {"systemMessage": text}}]}` and nothing else. |
| B2 | Codex cut hook output past about 2,500 tokens, so the preload dropped to 8,000 characters (`constants.py:42-46`). | A `systemMessage` of about 20,000 characters arrived whole; the cap is near 48,900 characters, keeping the head. | *Recommendation:* 10,000 characters, as in Claude Code. The cap does not bind, and one budget for two harnesses is simpler. |
| B3 | Claude Code shows the user a one-line `systemMessage` that memory was loaded. | No field for a user-facing notice, and an extra key voids the reply. | Drop the notice for Antigravity. Whether the TUI displays the injected step is untested. |
| B4 | When the server is down or failed to start, `kg-autostart.sh` prints plain text, which both harnesses turn into context (`kg-autostart.sh:93-119`). | Plain text is discarded. | Every message the hooks produce, including server down and start failed, goes out as a `systemMessage`. Only the adapter writes to stdout. |
| B5 | `UserPromptSubmit` carries `prompt`. | `PreInvocation` does not. The transcript is current when the hook runs, and its last `USER_INPUT` holds the prompt inside `<USER_REQUEST>`, followed by CLI metadata blocks. | The adapter reads the last `USER_INPUT`, keeps only the `<USER_REQUEST>` body, and posts it as `prompt`. Injected `systemMessage` steps are never `USER_INPUT`, so they cannot be mistaken for the prompt. Never use `userMessage`, which would be. |
| B6 | The fallback nudge pools size session depth from transcript bytes (`kg-remind.sh:53-66`). | `transcript_full.jsonl` grows with tool results, as Claude Code's does. | Reuse the pools and the byte thresholds. Calibrate later from logged sessions. |
| B7 | Hooks ran once per prompt. | `PreInvocation` runs before every model call, often many per prompt. Default timeout 30 s. | The adapter answers `{}` at once when `invocationNum` > 0 and nothing is queued (B8). Set the handler timeout to a few seconds; keep `curl --max-time 1`. |
| B8 | `PostToolUse` context sat between the call and its result. | `PostToolUse` gets the call but cannot reply. The next `PreInvocation`'s `systemMessage` is appended to the tool result, which is the same position. | *Recommendation:* `PostToolUse` posts the event, and the server decides as today but holds the text for that conversation. The next `PreInvocation` with `invocationNum` ≥ 1 collects and injects it. Parallel calls queue in order. A held text still waiting at the next `invocationNum` 0 is dropped: a soft-denied tool ends a turn with no further model call. The alternative, reading the tool calls back from the transcript, avoids the queue but parses the transcript on every model call. |
| B9 | Codex edits arrive as `apply_patch`, so file recall learned patch headers (`file_recall.py:349-366`). | Edits are `write_to_file`, `replace_file_content`, `multi_replace_file_content`, all with `TargetFile`. Reads are `view_file` with `AbsolutePath`. | Profile table: `view_file` read, the three edit tools edit, `run_command` shell. Matcher: `view_file\|write_to_file\|replace_file_content\|multi_replace_file_content\|run_command\|read_url_content\|search_web`. |
| B10 | Codex omits the shell's directory, so `shell_context.py` finds it in the rollout by call id, and refuses to guess. | `run_command` requires `Cwd`. | Pass `Cwd` as `workdir`; the existing explicit-`workdir` path handles it. No transcript lookup. |
| B11 | Codex reads files only through the shell, so shell reads count toward capture nudges (`shell_reads_count`). | `view_file` exists. | `shell_reads_count` off, as in Claude Code. |
| B12 | `~/.codex` joined the paths never worth a nudge (`ambient.py:458-462`). | Before a lazy MCP call the model reads `~/.gemini/antigravity-cli/mcp/<server>/<tool>.json`. Spilled results live in `brain/<id>/.system_generated/steps/`, scratch files in `brain/<id>/scratch/`. | Add `/.gemini/`. Without it, every first `kg_*` call of a session would count a "read" of a schema file. |
| B13 | Codex's hosted web search never reaches a hook, so web capture nudges cannot fire there. | `read_url_content` (`Url`) and `search_web` (`query`) are ordinary tools in the request. | Map them to `web` and `search`. Hooks seeing them was not run; check it. |

### C. MCP

| # | Codex lesson (where) | Antigravity | Proposed mapping |
|---|---|---|---|
| C1 | The server's name differs per install (`mcp__plugin_knowledge-graph_kg__…`, `mcp__kg`), and every allowlist must follow it. | A plugin's server is `knowledge-graph_kg`. Permission tokens are `mcp(knowledge-graph_kg/<tool>)`; calls go through `call_mcp_tool` with that `ServerName`. | Derive the Antigravity names from the same allowlist files, as the Codex runner does (`chore_dispatch.py:208-216`). |
| C2 | `codex exec` needed `default_tools_approval_mode = "approve"` (0.10.0). | Unconfigured MCP calls ask the user in the TUI and are refused in headless mode, which also ends the turn. Only `permissions.allow` in the user's settings grants them; a hook cannot. | The install step adds the grants. Which tools is open question 6 (`kg_delete_node` may stay on Ask). |
| C3 | Tool descriptions reach the model and carry part of the call contract (first `kg_read` with `cwd`, pass `session_id`, `notes` replace the stored list). | Lazy tools: the model sees names only until it reads a schema file. The eager switch was not found. | The preload header and a plugin rule (D4) carry the call contract. Optionally the server sends MCP `instructions`, which Antigravity writes to a file the model is pointed at; Claude Code and Codex would put them into context too, so that is open question 8. |
| C4 | `READ_CHAR_BUDGET` (40,000) and `SEARCH_CHAR_BUDGET` (10,000) sit under Claude Code's inline limit (`constants.py:17-33`). | Results over 4,096 characters spill to a file. | Section 4. |

### D. Packaging, install and operations

| # | Codex lesson (where) | Antigravity | Proposed mapping |
|---|---|---|---|
| D1 | Codex installs the `.claude-plugin/` plugin unchanged. | Needs a root `plugin.json` (`name`, `description` only), a root `hooks.json` in its own format, a root `mcp_config.json` with `serverUrl`. `agy plugin import` produced a plugin with no working hooks and no MCP transport. | *Recommendation:* make `knowledge-graph/` a dual plugin by adding those three files. Claude Code and Codex read `hooks/hooks.json` and `.claude-plugin/`, so the root files should not collide, but that is untested (open question 2). Do not use `agy plugin import`. |
| D2 | `${CLAUDE_PLUGIN_ROOT}` locates the hook scripts. | Neither `${PLUGIN_ROOT}` nor `${CLAUDE_PLUGIN_ROOT}` is substituted in hook commands; the command runs in the plugin root. | Commands use relative paths (`hooks/kg-agy.sh <event>`); scripts locate themselves from `BASH_SOURCE`, as `kg-autostart.sh` already does. |
| D3 | Codex keeps plugin hooks off until the user trusts them; the first `kg_read` gives a hint (`harness.py:43-51`). | No trust step was seen in headless mode. Instead, one invalid entry silently drops every hook in the file. | Antigravity's `no_hooks_hint` points at `agy -p /hooks` and the CLI log rather than at trust. The install check runs `agy -p /hooks` and expects the plugin's three hooks. |
| D4 | Doctrine reaches the model through a hidden skill, tool descriptions and the preload header. | Skills are listed with descriptions. A plugin's `always_on` rule goes into the system prompt as a rule the model "MUST ALWAYS FOLLOW". | Ship a short rule with the call contract and the capture duty, and keep the full doctrine in `kg-core`. Rule strength and trigger are open question 7. |
| D5 | Install and update: `codex plugin marketplace add/upgrade`, then `codex plugin add`. | `agy plugin install <dir>` copies into `~/.gemini/config/plugins/knowledge-graph/`. No route to a third-party marketplace was found. | Document clone, then `agy plugin install <clone>/knowledge-graph`; update by pulling and installing again. `install_command.sh` learns the new plugin path. |
| D6 | Restart advice differs per harness (`/mcp` Reconnect, or a new session). | The MCP client starts with each CLI process; whether a running TUI reconnects was not tested. | Advise a new session, as for Codex, until tested. |
| D7 | A chore run strips the launching session's variables (`chore_dispatch.py:146-162`). | Hooks get `ANTIGRAVITY_CONVERSATION_ID`. A server started by the session-start hook would inherit it, and so would its chore runs (inferred, not run). | Strip `ANTIGRAVITY_*` in `chore_env`. `KG_CHORE` reaches hooks, since they inherit the environment (observed). |

### E. Maintenance

| # | Codex lesson (where) | Antigravity | Proposed mapping |
|---|---|---|---|
| E1 | A runner owns its binary, command and gauge; Codex's run disables shell and web, uses a read-only sandbox, and lists the kg tools (`chore_dispatch.py:317-352`). | No way to point the CLI at another configuration. A custom agent with `tools: []` leaves only the MCP tools and `manage_task`. A missing or invalid agent silently falls back to the default agent with every tool. | *Recommendation:* the server writes `<storage root>/.agents/agents/kg-maintainer/agent.md` with `tools: []`, and runs `agy --agent kg-maintainer --input-format stream-json --output-format stream-json` from the storage root, the prompt on stdin. It reads the `init` event and kills the run if any tool beyond `call_mcp_tool`, `list_resources`, `read_resource`, `manage_task` is offered, which catches the silent fallback. |
| E2 | Tier allowlists differ (chore and pass). | Grants live in the user's global settings and apply to interactive sessions too. An agent's own `PreToolUse` hook can deny but not grant. | Grant the union once (C2). The agent carries a `PreToolUse` gate that denies anything outside its tier's list. |
| E3 | A refused call fails visibly. | A soft-denied call ends the run with exit 0 and lists it in `denied_actions`. | Record `denied_actions` in `chores.jsonl`. |
| E4 | The gauge reads the plan's windows: the status line for Claude Code, the rollout for Codex, with a timestamp that decides freshness (0.10.2). | `agy -p /usage --output-format json` answers without a model call, with `groups` and `buckets` of `remaining_fraction` and `reset_time`. Only an empty reading was seen. The status line also carries `quota`, but only while the TUI is open. | Read `/usage` at gate time; used % = (1 − `remaining_fraction`) × 100. Which bucket is the five-hour window and which the week needs one reading from a real account. Until then, no reading means no run, as for Codex. |
| E5 | Codex spends the plan the user chose. | With `useG1Credits` on, calls past the plan quota spend paid credits. | Refuse while `useG1Credits` is on, unless `chores.json` opts in. The gate's thresholds keep runs far from exhaustion anyway. |
| E6 | `codex_model`, `codex_reasoning_effort`. | `--model <slug>` (`agy models`), `--effort low\|medium\|high\|max`. A headless run fails on an unknown model rather than falling back. | `antigravity_model`, `antigravity_effort`, low for chores and medium for passes as for Codex. |
| E7 | API keys: not relevant to Codex. | Under a Gemini API key there is no plan quota, only billing. | Chores stay off under an API key unless the user opts in. |

### F. History and tooling

| # | Codex lesson (where) | Antigravity | Proposed mapping |
|---|---|---|---|
| F1 | `kg-scout` has a Codex recipe: rollout inventory, then selective reads (`skills/kg-scout/SKILL.md:39-106`). | `conversation_summaries.db` is the inventory (`workspace_uris`, `title`, `step_count`, `last_modified_time`, `nesting_depth`); `transcript_full.jsonl` is the record. | An Antigravity recipe: list conversations by workspace and recency, skip `nesting_depth` > 0, read `USER_INPUT` bodies as in B5, skip injected `SYSTEM_MESSAGE` steps. Three app-data directories, one per surface. |
| F2 | The visual editor missed Codex-only projects until the server listed its own graphs (Unreleased). | Already harness-neutral. | Nothing. |
| F3 | Roadmap 10 parses both transcript formats. | Tool calls are `PLANNER_RESPONSE.tool_calls`; `kg_read` ids sit in `call_mcp_tool`'s `Arguments`. | A third parser when roadmap 10 is built. |
| F4 | Codex has its own `memories` feature, off by default; the README says to leave it off. | The CLI's prompt had no memory section. The IDE has knowledge items. | README note for IDE and desktop users only. |
| F5 | Codex behaviour changed between 0.157 and 0.158 (`shell_context.py:3`). | `agy` updates itself in the background, and payloads already differ from the docs. | Keep the [probe](tools/antigravity-probe/README.md) and run it on a new version before trusting an old observation. The adapter tolerates missing fields. |

## 3. The adapter, sketched

*Recommendation*, following the rule that the server makes every decision
and an adapter only carries events and replies:

- **`knowledge-graph/hooks.json`** (Antigravity format), three handlers:
  `SessionStart`, `PreInvocation` and `PostToolUse` (matcher from B9), each
  `hooks/kg-agy.sh <event>`. No `PreToolUse`: answering `{}` there denies
  every tool.
- **`hooks/kg-agy.sh`**, one script. It honours `KG_CHORE`, normalises the
  payload (`session_id` ← `conversationId`, `cwd` ← first workspace path,
  `transcript_path`, `harness`, and for prompts the text from B5), posts to
  the existing endpoints, and prints only JSON. On `SessionStart` it also
  starts a stopped server, as `kg-autostart.sh` does.
- **Server**, all of it in the harness profile or behind it:
  - an `ANTIGRAVITY` profile: preload 10,000, `shell_reads_count` off, shell
    directory known, its own hint (D3), the `injectSteps` envelope (B1);
  - detection from `_meta` (A2) and binding on the first MCP call (A3);
  - the tool table (B9, B13), `Cwd` as `workdir` (B10), `/.gemini/` noise
    (B12);
  - the held tool recall per conversation (B8);
  - the read path (section 4);
  - an `AntigravityRunner` and `/usage` gauge (E1-E7), and `ANTIGRAVITY_*`
    stripping (D7).
- **Plugin files**: root `plugin.json`, `mcp_config.json`, `rules/kg-core.md`
  (D1, D4).
- **Docs**: README support table, `kg-ops` runbook, `kg-scout` recipe.

The Codex work suggests the size: the profile, runner and gauge were a few
hundred lines; the hand-off in B8 and the read path in section 4 are the
parts with no Codex precedent.

## 4. The MCP read path

The server renders a read and marks every rendered node as seen by the
session (`mark_seen` with `via` `full_read`, `read` or `search`). Later recall
treats a seen node as already in context. In Antigravity a render over 4,096
characters reaches the model only if the model opens the saved file. If it
does not, the session's seen set claims nodes the model never saw, and
recall withholds them for the rest of the session. Nothing would report it.

| Option | How | For | Against |
|---|---|---|---|
| A. Small pages | An Antigravity read budget under 4,096; longer reads return the first page and say how to ask for the next. | Works without hooks; what is marked seen was shown. | A full read takes many calls; changes `kg_read` and `kg_search` semantics for one harness. |
| B. Accept the spill | Return the full render; mark it seen only once a `PostToolUse` shows `view_file` of that conversation's spilled output. | No change to the render. | Ties seen-marking to the CLI's storage layout and to the model choosing to open the file; heuristic matching of the spill path. |
| C. Deliver through the hook | Return a receipt under 4,096 ("the full graph follows as a system message") and hold the render; the next `PreInvocation`, which always follows a tool result within a turn, injects it as a `systemMessage`, persistent, up to about 48,900 characters. Mark seen when the adapter collects it. | The model sees everything once, in context, as with the preload. Reuses the B8 hand-off. | Needs the hooks active; a render arrives framed as a system message, not as the tool's result. |

*Recommendation:* **C**, with **A** for a conversation whose Antigravity hooks
the server has never seen. Before relying on it, check with a real model that
a render delivered this way is used as if it were the tool's result.

## 5. Open questions only the maintainer can decide

1. **Scope.** CLI only, or the desktop app and IDE too? They share
   `~/.gemini/config/` hooks and plugins but keep conversations in other
   directories. Only the CLI was examined.
2. **One plugin directory or two?** D1 adds three root files to
   `knowledge-graph/`. That they do not disturb Claude Code or Codex needs a
   check in each.
3. **Subagent memory.** Full preload, a pointer, or nothing (A6)?
4. **The read path.** C with A as fallback, or one of the others (section 4)?
5. **Multi-root workspaces.** Is the first workspace path the project, or
   should a session span several (A4)?
6. **Interactive grants.** Pre-approve every `kg_*` tool at install, or leave
   `kg_delete_node` on Ask (C2)?
7. **Rule strength.** An `always_on` rule is framed as a must-follow user
   rule. Ship the call contract that way, or as `model_decision` with a
   description (D4)?
8. **MCP `instructions`.** Sending them helps Antigravity's lazy tools, and
   also changes what Claude Code and Codex put in context (C3).
9. **Maintenance through Antigravity at all,** spending the Google plan's
   quota, with the gauge unverified until an account reading (E4)?

## 6. What still needs a signed-in account or a real model

- `/usage` buckets and their names, to map the five-hour and weekly windows.
- Whether a real model treats an injected `systemMessage` as context, and a
  delivered read (section 4, C) as the read's result.
- Whether compaction keeps the session-start preload, and what `/fork` and
  `/clear` fire.
- Hook trust in the interactive TUI, and how the TUI shows injected steps.
- `search_web` and `read_url_content` reaching `PostToolUse`.
- The marketplace route for a third-party plugin.

## 7. What this proposal does not rest on

- A real model. Every run used a mock; the proposal relies on placement.
- A Google account. Quota, marketplace and desktop sync are documentation.
- Versions after `agy 1.2.14`. The CLI updates itself, and the hook payloads
  already differ from its documentation.
