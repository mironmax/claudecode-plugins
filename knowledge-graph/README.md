# kg-memory: knowledge-graph memory for Claude Code, Codex and Antigravity

kg-memory gives a coding agent a memory that survives across sessions. Each memory is a short lesson (a node: a one-line *gist*, notes, file pointers), linked to related lessons by named relationships. The agent writes a lesson at the moment it learns it, and later sessions get it back without asking: at session start, when a prompt matches it, and when the agent reads or edits a file it concerns. Claude Code and Codex CLI share one memory server, so what one learns the other recalls. Antigravity CLI support is [experimental](#antigravity-cli-experimental).

It is free and open source (MIT), with no account, no API key and no telemetry. The memory is plain JSON on your machine. For why it's worth trying, what a day with it looks like and an honest account of how mature it is, see the [project page](https://github.com/mironmax/kg-memory#readme).

The design puts the intelligence at **capture time**. The model compresses knowledge while the insight is fresh, stores it as a headline plus relationships, and reads it back as structured text. Lexical search and file matching bring back what sits below the preloaded surface. No embedding service or database is needed. [ARCHITECTURE.md](https://github.com/mironmax/kg-memory/blob/main/knowledge-graph/ARCHITECTURE.md) explains the design in full.

## Install

```bash
uv tool install kg-memory     # needs uv: https://docs.astral.sh/uv/ (it brings its own Python)
kg setup                      # asks before each change, backs up what it edits
```

`kg setup` checks each piece, shows what it would change, and applies what you accept:

- **kg**: one local memory server for every harness. On Linux with a systemd user session, a user service starts it at login; elsewhere the session hook starts it on demand. If the `kg` command isn't on your PATH, setup tells you how to fix that (`uv tool update-shell`).
- **Claude Code**: the knowledge-graph plugin with marketplace auto-update, the `kg_*` tools pre-approved, the built-in auto-memory turned off (two memory systems write conflicting entries), and a quota gauge in your status line (`kg gauge`; an existing status line keeps working through it).
- **Codex CLI**, **Antigravity CLI** (experimental, with its tool grants) and **Claude Desktop**, when they are installed.
- **Background upkeep**, opt-in: the server tends the graph in small headless runs while your quota is spare. Because it spends quota, its question defaults to No, and `--yes` applies it only when named (`kg setup --yes --only upkeep`).

`kg setup --plan` lists the items with their keys and changes nothing; `kg setup --yes --only KEY,KEY` applies just those. Every file setup edits is backed up under `~/.local/state/knowledge-graph/backups/`. `kg doctor` checks everything later. `kg update` upgrades kg, then restarts the server on the new version and updates every installed plugin. `kg uninstall` reverses setup and leaves your memory in place.

Two steps stay with you: in Codex, run `/hooks` and trust the knowledge-graph hooks; fully quit and reopen Claude Desktop. Then start a new session.

The hooks run `bash`, `curl` and `python3` from your system. Windows is not supported.

---

## What the memory does on its own

With the harness hooks enabled, none of this needs to be asked for:

- **Preloaded at session start.** The top-scored nodes of both graphs (user and project) are in context before the first word. One `kg_read`, followed through its parts, renders the rest, and the agent then says *"I have recalled KG Memories"*.
- **Recall when a file is touched.** When the agent reads or edits a file, the memory that names that file arrives with the tool result. A node already in the session's context is not repeated.
- **Recall at the moment of relevance.** Each prompt you type is matched against the graph on the server. When unseen nodes fit, their gists arrive with the prompt. Precision is deliberate: nothing is injected twice, weak matches stay silent, and machine records (notifications, pasted images and paths) never trigger it. The channel only speaks when a person asked something.
- **Capture when re-derivation is proven.** Reading the same uncovered file in a second, separate session (or fetching the same URL or query twice) earns a one-time nudge to write the bottom line down. First-time reads never nudge, and throttles keep it rare.
- **Sessions hear each other.** When another session writes a node that could hold this session's lesson, its gist is appended to this session's next hook or write reply, so the agent updates the existing lesson instead of writing a duplicate.
- **Maintenance signal.** Every read carries a `DEBT:` line per graph. It counts oversized gists, unconnected nodes, file pointers that no longer resolve and episodes waiting to be lifted into a principle, and weighs them by time since the graph was last tended and how actively it is used. When it reads HIGH, `/kg-maintain` runs a bounded pass, or the agent hands it to a maintenance subagent.
- **Budget notices.** The server reads the session's own quota gauge (Claude Code's status-line file, the Codex rollout, Antigravity's `/usage`) and says once per window when to plan the wrap-up (80% of five hours) and when to wrap up now (90%), and 90/95% for the week.
- **Background upkeep, if you switch it on.** The server can also pay debt down while you work. Each run is one kind of fix on one or two nodes the server picks itself, in a handful of tool calls. It runs as a detached headless agent whose MCP access is limited to its maintenance tools, so your session spends no context on it. Under Codex, shell and hosted web are also disabled and the filesystem is read-only. Every run and every refusal is logged. Runs can go through Claude Code, Codex or Antigravity, each gated on its own subscription's limits. `/kg-ops` has the switch and the gates.

## Everyday use

The agent does the remembering. A few habits make it work better:

- **Say when you're wrapping up.** That's the natural moment for the agent to write down what the session learned and to endorse (`kg_useful`) the memories that helped. Endorsed memories stay on top.
- **Correct it, and say when memory missed.** If you have to tell the agent something the graph already held, that miss is worth reporting. A miss is the only signal that brings an archived memory back into view.
- **Prefer fresh sessions to long compactions.** Finishing a task cleanly and starting a new session works better than letting context compact. The graph keeps what matters.
- **Seed and mine now and then.** `/kg-extract` maps a codebase into the graph; `/kg-scout` mines past sessions for lessons worth keeping.
- **Look at it.** `kg editor` opens a browser view of the graph, with search, score explanations and editing ([guide](https://github.com/mironmax/kg-memory/blob/main/knowledge-graph/VISUAL_EDITOR_GUIDE.md)).

### Skills

| Skill | Type | Purpose |
|-------|------|---------|
| `kg-core` | Hidden (auto-loaded) | The memory doctrine: session protocol, recall, capture, search below the surface |
| `/kg-maintain` | User-invocable | Bounded maintenance pass that pays down the graph's DEBT line; includes the subagent dispatch prompt |
| `/kg-scout` | User-invocable | Mine Claude Code, Codex and Antigravity history for patterns and insights |
| `/kg-extract` | User-invocable | Map codebase architecture into the knowledge graph |
| `/kg-ops` | User-invocable | Operations runbook: install, updates, server, Desktop/Cowork, Codex, Antigravity, chores, backup, troubleshooting, uninstall |

In Codex the same five skills are listed to the agent; ask for one by name.

### Recommended setup (optional)

- **[Claude Code](https://github.com/mironmax/kg-memory/tree/main/recommended-setup)**: an output style carrying a benchmarked working agreement, and a quota-aware status line. Your remaining 5h/7d quota reaches `~/.claude/last-limits.json` only through a status line. Either this one or the `kg gauge` that `kg setup` installs will do. That file is what lets a long session end on a clean checkpoint instead of stopping mid-edit, and it feeds the budget notices.
- **[Codex CLI](https://github.com/mironmax/kg-memory/blob/main/recommended-setup/codex.md)**: the same working style as developer instructions, and a native footer preset for limits and context.
- **[Antigravity CLI](https://github.com/mironmax/kg-memory/blob/main/recommended-setup/antigravity.md)**: the same working style as a global rule, and a quota status line.

---

## Server management

One shared memory server on port 8765 serves every session: Claude Code, Codex, Antigravity and Claude Desktop alike. Each harness connects through `kg mcp`, a small stdio bridge that starts the server when it is down and waits out restarts, so the tools stay connected.

> Anything operational (updates, autostart, Desktop connection, backups, troubleshooting) is written up as agent-followable recipes in `/kg-ops`. Telling the agent "run /kg-ops and fix the memory server" is a complete instruction.

```bash
kg status            # running? which version, from where?
kg start | stop | restart
kg logs [-f]
kg editor [stop]     # browser-based graph explorer at http://localhost:8766
kg doctor            # check every piece the memory depends on
kg version
```

**Server details:** endpoint `http://127.0.0.1:8765/` (health: `/health`). The PID file and the last start error live in `~/.local/state/knowledge-graph/`; a server on another `KG_HTTP_PORT` uses `port-<N>/` inside it. When `kg` started the server, its log is `mcp_server.log` there; under the systemd service, `kg logs` reads the journal (`journalctl --user -u kg-memory.service`).

---

## Claude Desktop

Claude Desktop uses the same memory as another client of the shared server. Its "Add custom connector" dialog won't take a local URL, because those connectors are contacted from Anthropic's cloud. So `kg setup` writes Desktop's config file instead, pointing it at `kg mcp` (macOS and Linux). Fully quit and reopen Desktop afterwards. Cowork sessions receive the server through Desktop's own sandbox bridge.

Desktop's **Code tab** runs the Claude Code hooks, including preload and file recall. **Desktop chat** uses the MCP tools without those hooks, so memory arrives on the first `kg_read`. Without a working directory that read opens user memory only; name the project's absolute path to attach its graph.

---

## Codex CLI

`kg setup` installs this plugin from the same marketplace (`knowledge-graph@maxim-plugins`). Leave Codex's own `memories` feature off, for the same reason as Claude Code's auto-memory.

Codex keeps a plugin's hooks off until you approve them. Run `/hooks` in Codex, trust the knowledge-graph hooks, and start a new session. Until then the `kg_*` tools work but nothing arrives on its own. A first `kg_read` offers a diagnostic hint when no live Codex session in this project has reported hooks; another session can suppress that hint, so check `/hooks` directly when recall is missing. Codex records trust against each hook's content, so an update that changes the hooks asks for it again.

Both harnesses talk to one local server, so a lesson captured in Codex is recalled in Claude Code and the other way round. What Codex CLI supports:

| Capability | Codex CLI support |
|---|---|
| MCP tools and shared memory | Supported, including when hooks are off |
| Preload and prompt recall | Supported with trusted hooks; preload fits 9,000 bytes |
| File recall and read counters | `apply_patch` and explicit file operands of `cat`, `head`, `tail`, `less`, `sed -n`, `grep`, `jq`, `nl`, `rg`; relative shell paths need a verified execution directory |
| Hosted web search | No hook event, so no web-research capture nudges |
| `/kg-extract` | Codebase mapping works in either harness |
| `/kg-scout` | Mines Claude Code history and Codex rollouts; resumed rollouts use per-file cursors |
| Visual editor | Discovers stored project graphs through the server, including Codex-only projects |
| Background upkeep | Opt-in; the selected runner determines which subscription it spends |
| Codex desktop | Checked on Linux; this is not a verification of every desktop platform |
| macOS | The test suite runs in CI (report-only); the Codex integration is checked on Linux |
| Windows | Not supported |

**Shell directories.** Codex reports the session directory in its hook and can omit `exec_command.workdir`. For relative paths, the server first honors an explicit absolute `workdir`. Otherwise it matches the hook's invocation id to a completed command record in a bounded tail of that session's rollout and uses the recorded execution directory. This was measured live with nested and parallel calls on CLI 0.158.0. Missing, incomplete or ambiguous records leave relative paths unresolved; absolute operands still work. `nl` and `rg` support explicit file operands with recognized options; implicit directory searches and `rg --files` are not tracked. Rollouts must be local `.jsonl` files under the user's home directory, as for session recovery.

**Update:** `kg update` upgrades kg, restarts the shared server on the new version and upgrades the Codex plugin. Then check changed hooks in `/hooks` and start a new session. A new session alone does not replace a running server.

Maintenance can run through Codex too, spending your ChatGPT plan's limits instead of Claude's. See `/kg-ops` (Maintenance chores, `"runner"`).

---

## Antigravity CLI (experimental)

The plugin also carries a native Antigravity CLI package: eager KG tools, session preload, prompt recall and file recall, on the same memory server. The CLI truncates large MCP results, so larger replies arrive through the next `PreInvocation` hook in chunks, and nothing they imply is recorded until the last chunk is delivered. A checkpoint (compaction) re-queues the preload. `kg setup` installs it with `agy plugin install` from the kg-memory package, and `kg update` refreshes it; there is no marketplace or git-URL install.

Budget notices, background upkeep (including an Antigravity runner) and `/kg-scout` history mining work here too. Signed-in resume, fork, clear and compaction checks are still open. See the [Antigravity guide](https://github.com/mironmax/kg-memory/blob/main/knowledge-graph/ANTIGRAVITY.md).

---

## Configuration

The server reads a few tunables from environment variables. Where to set them depends on what starts the server:

- **systemd service (Linux):** when `kg setup` installs the unit, it copies `PATH`, `CODEX_HOME` and every `KG_*` variable from its own environment into it, and later shell changes don't reach it. To change one, edit the `Environment=` lines in `~/.config/systemd/user/kg-memory.service`, then `systemctl --user daemon-reload && kg restart`.
- **Otherwise:** a server started by `kg start`, `kg mcp` or a session hook inherits that process's environment. Set the variables in your shell rc file and `kg restart`.

| Variable | Default | Description |
|----------|---------|-------------|
| `KG_STORAGE_ROOT` | `~/.knowledge-graph` | Root directory for all graph data |
| `KG_HTTP_PORT` | `8765` | Server port. Harnesses, hooks and `kg` must all see the same value |
| `KG_HTTP_HOST` | `127.0.0.1` | Bind address. Keep it local; see [SECURITY.md](https://github.com/mironmax/kg-memory/blob/main/SECURITY.md) |
| `KG_SAVE_INTERVAL` | `30` | Background save interval (seconds) |
| `KG_AUTOCOMMIT_INTERVAL` | `900` | Git auto-commit interval for the storage root (seconds); `0` disables. Only acts when the storage root is a git repository |
| `KG_ORPHAN_GRACE_DAYS` | `365` | Days an orphaned node may go unrecalled before it is permanently deleted |
| `KG_CHORES` | unset | `1` or `0` overrides `"enabled"` in `chores.json` (background upkeep) |
| `KG_BUDGET_NOTICES` | `1` | `0` turns budget notices off (or `"budget_notices": false` in `chores.json`) |
| `KG_LOG_LEVEL` | `INFO` | Server log level |
| `EDITOR_PORT` | `8766` | Visual editor port |

> Don't edit the plugin's bundled `.mcp.json`. It only launches `kg mcp`, and it is overwritten on every plugin update.

> **The size budget is fixed by design.** Budgets are exact rendered characters: 22,000 per level and 50,000 for the combined full-graph render. The preload fits each client's hook limit as that client counts (9,500 UTF-16 units in Claude Code, 9,000 bytes in Codex). A reply longer than the client keeps whole arrives in parts, and only what a part showed counts as seen. Oversized full graphs hide the lowest-scored archived anchors and edges, with counts and a search pointer.

> The full-graph figure is a target: active gists are preserved even if they alone exceed it. The newest nodes (the fresh tier, up to 30% of a level's budget) are never archived. So a level holding unusually long gists can render past its target until a maintenance pass tightens them.

---

## Data locations

Graph data lives under `~/.knowledge-graph/` by default (`KG_STORAGE_ROOT` can change it), as JSON plus JSONL decision logs. Any file backup tool works.

- **User level:** `user.json`: cross-project knowledge
- **Project level:** `projects/<slug>/graph.json`: codebase-specific
- **Sessions:** `sessions.json`: session registry
- **Tool-event counters:** `projects/<slug>/tool_events.json`: per-target read/fetch counts feeding the capture nudges and the activity part of the DEBT score
- **Maintenance memory:** `maintain.json`: the maintenance agent's own lessons, never shown in sessions
- **Logs:** `recall.jsonl` records what recall decided per prompt and tool event. `useful.jsonl` records endorsements, plus the credits a repeat, an added case or a maintenance pass gives (marked `via: "recurrence"`, `"note"` or `"maintenance"`); the evaluator counts only real use. `chores.jsonl` records every upkeep decision. All are size-capped.
- **Upkeep:** `chores.json` (your switch and settings, if any) and `chore_state.json` (spacing and daily counts)

Project graphs are keyed by the project root's final directory name, without a path hash, so two project roots with the same final name share one graph; use distinct names within a storage root. Project roots must be under your home directory. See [Data and Backup](https://github.com/mironmax/kg-memory/wiki/Data-and-Backup#file-locations).

### Built-in crash protection

Every save is atomic (write to a temp file, fsync, rename) and keeps one rolling copy of the previous good state beside it: `user.prev` for `user.json`, `graph.prev` for a project's `graph.json`. This protects against corruption from interrupted writes, not against accidental deletion or longer-term history.

To restore the previous state, stop the server first so a background save cannot overwrite the restored file:

```bash
kg stop
cp ~/.knowledge-graph/user.prev ~/.knowledge-graph/user.json
cp ~/.knowledge-graph/projects/<slug>/graph.prev \
   ~/.knowledge-graph/projects/<slug>/graph.json
kg start
```

### Self-healing on load

Occasionally a client glitch lands a node with its `gist`, `notes` and tool-call markup mashed into one oversized string. The server repairs this automatically: it sanitizes on write, heals existing damage when a graph is loaded, and writes the fix back. The repair is idempotent and never overwrites data you supplied. A `Healed N corrupt node(s) on load` log line means it did its job. See [Data and Backup](https://github.com/mironmax/kg-memory/wiki/Data-and-Backup#self-healing-on-load) for details.

### Versioned history and external backups

For versioned history, the server has git support built in. For off-machine copies, set up an external tool. Two options:

**Git**: one-time setup, then automatic:
```bash
cd ~/.knowledge-graph
git init
echo "*.prev" >> .gitignore
echo "*.tmp" >> .gitignore
git add -A && git commit -m "initial"
```
That's it. The server detects the repository and commits changes itself every 15 minutes (`Auto-save YYYY-MM-DD HH:MM` commits, only when something actually changed), plus a final commit on graceful shutdown and on `kg stop`. Tune or disable with `KG_AUTOCOMMIT_INTERVAL` (seconds; `0` disables). `kg commit` forces an immediate commit.

**Borg**, a better fit for frequently changing data:
```bash
borg init --encryption=none ~/.knowledge-graph-borg
```
Add to crontab (`crontab -e`):
```
0 * * * * borg create --stats ~/.knowledge-graph-borg::'{now}' ~/.knowledge-graph
0 2 * * * borg prune ~/.knowledge-graph-borg --keep-hourly=24 --keep-daily=7 --keep-weekly=4
```
Borg deduplicates across archives, so hourly snapshots of mostly unchanged JSON files cost almost nothing. Point-in-time restore:
```bash
borg extract ~/.knowledge-graph-borg::2026-05-17T03:00 --strip-components 3
```

---

## Uninstallation

```bash
kg uninstall --plan          # what it would reverse
kg uninstall                 # plugins, service, permissions, settings; asks before each
uv tool uninstall kg-memory  # the kg command itself
```

Your knowledge data stays in `~/.knowledge-graph/`.

---

## License

MIT. See [LICENSE](https://github.com/mironmax/kg-memory/blob/main/knowledge-graph/LICENSE).

Current version: `kg version`, or `.claude-plugin/plugin.json` in the plugin. Release history: [CHANGELOG.md](https://github.com/mironmax/kg-memory/blob/main/CHANGELOG.md).
