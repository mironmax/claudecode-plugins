# Coupling map

Every place the repository depends on Claude Code, sorted into three layers.
Paths are relative to the repository root, and line numbers refer to commit
`f17349a` (v0.9.42).

- **Layer 0, harness-neutral:** the graph, scoring, search, archival, chore
  selection. Nothing in it knows which harness is calling.
- **Layer 1, protocol:** the MCP tool surface and the REST API. Any client
  that speaks MCP over streamable HTTP, or plain HTTP, can use it.
- **Layer 2, harness-specific:** code, configuration and prompt text that
  assume Claude Code's hooks, payloads, binaries, files or UI.

The last column of each layer 2 table records what happened when the same
artefact ran under Codex CLI 0.157.1 (see [the Codex card](cards/codex-cli.md)).
"Ran unchanged" means observed by running it; "not run" means the repository
alone decides the answer.

## The jobs table, verified

The brief's table was written from memory. Checked against the code, it
needs these corrections.

| Job | Correction |
|---|---|
| Ambient recall on each prompt | The path is `kg-remind.sh` → `POST /api/prompt_context` (`knowledge-graph/server/mcp_http/rest.py:234-275`) → `build_prompt_recall` (`knowledge-graph/server/mcp_http/ambient.py:229`). When the server has nothing deterministic to say, the hook falls back to staged random nudges that live in the hook itself (`knowledge-graph/hooks/kg-remind.sh:53-110`). |
| Recall and nudges on tool use | The path is `kg-tool-event.sh` → `POST /api/tool_event` (`rest.py:277-295`) → `handle_tool_event` (`ambient.py:569`), which calls `file_targets` (`knowledge-graph/server/mcp_http/file_recall.py:263`). Claude Code tool names are hard-coded at `file_recall.py:107-108` and `ambient.py:492-503`. |
| Doctrine delivery | `kg-core` declares `user-invocable: false` (`knowledge-graph/skills/kg-core/SKILL.md:3`). The repository does not show whether Claude Code auto-loads its body. Doctrine also rides the preload header (`knowledge-graph/server/mcp_http/read_format.py:244-265`), the hook nudges, and `recommended-setup/CLAUDE.md`. |
| Maintenance dispatch | Maintenance does not run on a clock. It is triggered by the per-prompt hook: `/api/prompt_context` starts `maybe_dispatch` on a thread (`rest.py:245-257`), so this job depends on the prompt hook too. The chore runs with `KG_CHORE=1`, which silences the plugin's own hooks (`knowledge-graph/hooks/kg-autostart.sh:26`, `kg-remind.sh:18`, `kg-tool-event.sh:13`), and from the storage root as its working directory (`chore_dispatch.py:381-388`). A systemd "tick" dispatcher is referenced (`chore_dispatch.py:3`, `knowledge-graph/server/core/constants.py:280`) but its script is not in this repository. |
| Budget gate for maintenance | Correct as stated. The gauge path can be overridden with `"limits"` in `chores.json` (`chore_dispatch.py:253`). |
| Session and project identity | Besides the hook's stdin fields, identity also depends on the SessionStart `source` value (`rest.py:124-149`), on KG session ids recovered from markers in the transcript file (`knowledge-graph/server/mcp_http/session_manager.py:14-51`), and on `CLAUDE_PROJECT_DIR` as a fallback (`kg-autostart.sh:40`). |
| Packaging, install, update | The marketplace manifest is at the repository root (`.claude-plugin/marketplace.json`). `install_command.sh` installs harness-neutral shell shims and also removes a legacy hook from `~/.claude/settings.json`. `knowledge-graph/server/sync_version.py:12` writes the version into `.claude-plugin/plugin.json`. |
| Project discovery for the visual editor | Correct as stated (`knowledge-graph/visual-editor/backend/project_discovery.py:200-291`). |

These jobs were missing from the table:

| Job | Instrument | Where |
|---|---|---|
| Claude Desktop connection | stdio bridge to the HTTP server, and a config writer | `knowledge-graph/desktop_bridge.sh`, `knowledge-graph/setup_desktop.py` |
| Mining past sessions | user-invoked skill that reads Claude Code history files | `knowledge-graph/skills/kg-scout/SKILL.md:27-33,63,73` |
| Output budgets | size ceilings calibrated against Claude Code's limits for inline hook output and tool results | `constants.py:17-18`, `constants.py:33-40` |

## Layer 0: harness-neutral

| Area | Files |
|---|---|
| Graph model, persistence, archival, compaction, healing | `knowledge-graph/server/core/{persistence,compactor,healer,autocommit,types,utils,exceptions}.py`, `knowledge-graph/server/mcp_http/store.py` |
| Scoring, rendering, budgets | `knowledge-graph/server/core/{scorer,render,estimator}.py`, `knowledge-graph/server/mcp_http/read_format.py` |
| Anchors, debt, lift, chore selection | `knowledge-graph/server/core/{anchors,debt,lift,chores}.py`; in `chore_dispatch.py`, target selection (`306-562`), the dispatcher's state (`183-213`) and the gate arithmetic (`220-241`, `271-299`) |
| Prompt and file recall decisions | `ambient.py` apart from the lines in layer 2; `file_recall.py` apart from the lines in layer 2 |
| Session seen-state | `session_manager.py` apart from the lines in layer 2 |
| Retrieval evaluation | `knowledge-graph/server/eval/` |

Three things in layer 0 carry Claude Code's shape without depending on it:

- `constants.py:33-40`: `BOOTSTRAP_CHAR_BUDGET = 10000` was measured against
  Claude Code 2.1.199's inline limit for hook output. Codex truncates hook
  output by tokens, at about 2,500 tokens by default (observed, see the Codex
  card), so the same value lands near Codex's limit rather than safely under it.
- `constants.py:17-18`: `READ_CHAR_BUDGET` is sized under Claude Code's
  roughly 50K-character persistence threshold for tool results.
- Log fields are named for Claude: `claude_session` (`ambient.py:204`,
  `store.py:520`), and the `claude_sid` argument throughout `ambient.py` and
  `file_recall.py`. They are names only; the values are whatever id the hook
  sends.

## Layer 1: protocol

| Surface | Where | Notes |
|---|---|---|
| MCP tools over streamable HTTP | `knowledge-graph/server/mcp_streamable_server.py` | Ten `kg_*` tools. Codex (run) and the Cursor Agent CLI (`mcp list-tools`, run) both listed all ten against this server with no changes. The one Claude Code reference is a comment (`:744`). |
| REST, reads and writes | `rest.py:68-90`, `rest.py:187-232`, `rest.py:301-462` | Used by the visual editor and the hooks. |
| REST, hook endpoints | `rest.py:92-185` (`/api/session_bootstrap`), `rest.py:234-275` (`/api/prompt_context`), `rest.py:277-295` (`/api/tool_event`) | Protocol in form, but they accept Claude Code's hook payload as-is and return Claude Code's hook output envelope. Those parts are counted in layer 2 below. |
| WebSocket and request security | `knowledge-graph/server/mcp_http/{websocket,security}.py` | |
| Server lifecycle | `knowledge-graph/server/manage_server.sh`, `knowledge-graph/server/memory-mcp.service` | Harness-neutral; comments mention Claude Code (`manage_server.sh:206,366`). |

## Layer 2: harness-specific

### Whole files

| File | Lines | What is Claude Code-specific | Under Codex 0.157.1 |
|---|---:|---|---|
| `knowledge-graph/hooks/hooks.json` | 35 | Claude Code hook config: event names, nesting, `${CLAUDE_PLUGIN_ROOT}`, a matcher of Claude tool names | Ran unchanged. Codex reads the same format and sets `CLAUDE_PLUGIN_ROOT`. The matcher's `Bash` matches Codex shell calls and `Edit\|Write` match `apply_patch` by alias. |
| `knowledge-graph/hooks/kg-autostart.sh` | 120 | Emits `hookSpecificOutput`/`systemMessage`; tells the model to have the user run `/mcp` → Reconnect (`:112`, `:119`) | Ran unchanged; the preload reached the model as a developer message. The `/mcp` Reconnect advice names a Claude Code UI step. |
| `knowledge-graph/hooks/kg-remind.sh` | 110 | Emits the `UserPromptSubmit` envelope; sizes session depth from the transcript file (`:53-66`) | Ran unchanged; the nudge reached the model. |
| `knowledge-graph/hooks/kg-tool-event.sh` | 28 | Posts the raw `PostToolUse` payload | Ran unchanged. |
| `knowledge-graph/.claude-plugin/plugin.json` | 21 | Claude Code plugin manifest | Installed unchanged via `codex plugin add`. |
| `knowledge-graph/.mcp.json` | 8 | Claude Code MCP config (`"type": "http"`) | Loaded unchanged; tools appeared as `mcp__kg`. |
| `.claude-plugin/marketplace.json` | 15 | Claude Code marketplace | Added unchanged via `codex plugin marketplace add`. |
| `knowledge-graph/chores/settings.json` | 24 | Claude Code `--settings` permission file; tool names such as `mcp__plugin_knowledge-graph_kg__kg_read`, `Bash`, `Task` | Not applicable: Codex restricts tools with CLI flags and config keys instead. |
| `knowledge-graph/chores/pass-settings.json` | 25 | Same, for the pass tier | Not applicable. |
| `recommended-setup/statusline.sh` | 262 | Reads Claude Code's status line stdin (`rate_limits.five_hour`, `seven_day`) and writes `~/.claude/last-limits.json` (`:120-123`, `:158-182`) | Not applicable: Codex's status line is a set of built-in items, not a command. |
| **Subtotal** | **648** | | |

### Lines inside shared files

| Location | Lines | What is Claude Code-specific | Under Codex 0.157.1 |
|---|---:|---|---|
| `knowledge-graph/server/mcp_http/chore_dispatch.py:134-176` | 43 | Finds the `claude` binary; picks the `--settings` file; strips `CLAUDE_CODE_*`, `CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_EFFORT`; builds `claude -p --model … --setting-sources user --settings …` | Needs a Codex equivalent. |
| `chore_dispatch.py:244-268` | 25 | Reads the gauge from `~/.claude/last-limits.json` in the status line's field names | Needs a Codex source. |
| `chore_dispatch.py:631-636` | 6 | Refuses dispatch when the `claude` binary or settings are missing | Same. |
| `knowledge-graph/server/core/constants.py:321` | 1 | `CHORE_MODEL = "claude-sonnet-5"` | Same. |
| `constants.py:457-459` | 3 | Legacy graph paths under `~/.claude/knowledge/` (migration only) | Harmless. |
| `rest.py:93-95`, `rest.py:119-142` | 27 | Bootstrap takes `claude_session_id`, `source`, `transcript_path` and resolves identity from them | Worked unchanged: Codex sends the same fields, and its `source` values are the same (`startup`, `resume`, `clear`, `compact`). |
| `rest.py:270-275`, `rest.py:290-295` | 12 | Builds Claude Code's `hookSpecificOutput` envelope | Worked unchanged: Codex accepts the same envelope. |
| `session_manager.py:14-51` | 38 | Recovers the KG session from markers in a transcript `.jsonl` under home | The regex found the KG session id in a Codex rollout file (run). A real Codex rollout lives under `~/.codex/sessions/`, which passes the under-home check. |
| `session_manager.py:97-120` | 24 | Binds a Claude Code session id to a KG session | Worked unchanged with Codex session ids. |
| `ambient.py:492-503` | 12 | Capture nudges only for `Read`, `WebFetch`, `WebSearch` | Codex has no `Read` tool, and its web search does not pass through hooks, so these nudges never fire. |
| `ambient.py:573-576` | 4 | Reads `cwd`, `tool_name`, `session_id`, `tool_input` from the payload | Worked unchanged. |
| `file_recall.py:107-108` | 2 | File tools `Read`, `Edit`, `Write`, `MultiEdit`, `NotebookEdit` | Codex edits arrive as `tool_name: "apply_patch"` with the patch text in `tool_input.command` (run). They match no entry, so edits produce no file recall. |
| `file_recall.py:263-276` | 14 | `Bash` → parse `tool_input.command` | Worked unchanged: Codex reports shell calls as `Bash` with `command` (run). `cat notes.txt` recalled the node anchored to `notes.txt`. |
| `knowledge-graph/install_command.sh:19-20,33-39,41-99,107-111` | 72 | Legacy cleanup of `~/.claude/settings.json`; refreshes the Claude Desktop bridge; prints `/reload-plugins` instructions | Not run. |
| `knowledge-graph/server/sync_version.py:12`, `knowledge-graph/visual-editor/backend/server.py:37` | 2 | Read or write the version in `.claude-plugin/plugin.json` | Harmless. |
| `knowledge-graph/visual-editor/backend/project_discovery.py:53-108,200-291` | 148 | Discovers projects by decoding `~/.claude/projects/<encoded>/` | Codex keeps sessions in `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (run), so this discovers nothing. |
| **Subtotal** | **433** | | |

### Prompt text

These are Markdown the model reads. They port as files, since Codex,
Cursor and Antigravity all read `SKILL.md`, but the lines below name Claude
Code specifics.

| File | Lines | Claude Code-specific content |
|---|---:|---|
| `knowledge-graph/skills/kg-scout/SKILL.md` | 148 | The whole method reads `~/.claude/history.jsonl` and `~/.claude/projects/…` (`:27-33`, `:63`, `:73`). |
| `knowledge-graph/skills/kg-ops/SKILL.md` | about 40 of 314 | Install via `/plugin`, `/mcp` Reconnect, `~/.claude/plugins/cache`, the status line gauge, `${CLAUDE_PLUGIN_ROOT}` (`:28-58`, `:93-140`). |
| `knowledge-graph/skills/kg-core/SKILL.md` | 3 | Mentions `CLAUDE.md` and subagents (`:126`, `:179-181`). |
| `knowledge-graph/skills/kg-maintain/SKILL.md` | about 6 | Subagent dispatch (`:7`, `:134-137`). |
| `recommended-setup/output-styles/concise-quality-v2.md`, `recommended-setup/CLAUDE.md` | 53 | Claude Code output style and memory file. |

### Claude Desktop adapter

`knowledge-graph/desktop_bridge.sh` (40 lines) and
`knowledge-graph/setup_desktop.py` (135 lines) connect a different Anthropic
client. They are specific to Claude Desktop, not Claude Code, and are left
out of the count below.

## The size of the port

| Layer 2 part | Lines |
|---|---:|
| Whole files | 648 |
| Lines inside shared files | 433 |
| **Code and configuration specific to Claude Code** | **1,081** |
| Prompt text naming Claude Code specifics | about 250 |

The raw count overstates the Codex port. Of the 1,081 lines, these ran
under Codex 0.157.1 without a change: the hook config and three hook scripts,
the plugin, MCP and marketplace manifests, and the payload and envelope code
in `rest.py`, `session_manager.py`, `ambient.py:573-576` and
`file_recall.py:263-276`. That is 456 lines. The remainder that has no
Codex equivalent yet is:

| Part | Lines |
|---|---:|
| Chore runner, permission files and model name (`chore_dispatch.py:134-176,631-636`, both `chores/*settings.json`, `constants.py:321`) | 99 |
| Budget gauge source (`chore_dispatch.py:244-268`, `recommended-setup/statusline.sh`) | 287 |
| File recall for edits (`file_recall.py:107-108`) and capture nudges (`ambient.py:492-503`) | 14 |
| Visual editor project discovery | 148 |
| Install script specifics | 72 |
| Legacy paths and version file references | 5 |
| **Total without a Codex equivalent** | **625** |

Most of that is the status line script, which Codex would not need in that
form (its quota figures land in its session files; see the card), and the
visual editor's discovery. The code that must be written new for Codex is
small in lines. The work is in the design choices the
[proposal](proposal.md) puts to the maintainer.
