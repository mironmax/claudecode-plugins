# kg-memory: knowledge-graph memory for Claude Code, Codex and Antigravity

Gives a coding agent a persistent memory that survives across sessions — not flat notes, but a graph of distilled insights connected by typed relationships. The agent captures patterns and decisions as you work; next session it recalls them automatically. Claude Code and Codex CLI share one memory server, so what is learned in one is recalled in the other. Antigravity CLI support is [experimental](#antigravity-cli-experimental).

The design puts the intelligence at **capture time**: knowledge is compressed by the model in the moment of insight, stored as headline + relationships, and read back as structured text. Lexical search and file matching bring back memories below the preload; no embedding service or database is required. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design thesis.

## Install

```bash
uv tool install kg-memory     # needs uv: https://docs.astral.sh/uv/ (it brings its own Python)
kg setup                      # asks before each change, backs up what it edits
```

`kg setup` checks each piece and fixes what you accept:

- the `kg` command on your PATH and one local memory server for every harness (a systemd user service on Linux);
- **Claude Code**: the knowledge-graph plugin with marketplace auto-update, the `kg_*` tools pre-approved, and the built-in auto-memory turned off (two memories write conflicting entries);
- **Codex CLI**, **Antigravity CLI** (experimental) and **Claude Desktop**, when installed.

`kg setup --plan` shows the list without changing anything; `kg doctor` checks everything later; `kg update` upgrades kg, the server and every plugin together; `kg uninstall` reverses setup and leaves your memory in place.

Two steps stay with you: in Codex, run `/hooks` and trust the knowledge-graph hooks; fully quit and reopen Claude Desktop. Then start a new session.

The plugin's hooks carry the ambient behaviour: SessionStart preloads your memory into context (starting the server if it is down); UserPromptSubmit surfaces memory relevant to each prompt; PostToolUse brings up the memory about a file when the agent reads or edits it, and nudges a capture when knowledge is being re-derived.

**Optional:**
- **[Recommended user-level setup](../recommended-setup/)** — an output style carrying a benchmarked working agreement, and a quota-aware status line. The status line matters here beyond taste: it persists your rolling 5h/7d usage to `~/.claude/last-limits.json`, the only channel through which Claude can read its own remaining budget — which is what lets a long session end on a clean checkpoint (handover letter + memory writes) instead of stopping mid-edit.
- **[Codex CLI setup](../recommended-setup/codex.md)** — the same working style as developer instructions, and a native footer preset for limits and context.
- **[Antigravity CLI setup](../recommended-setup/antigravity.md)** — the same working style as a global rule, and a quota status line.

---

## Server Management

One shared HTTP MCP server on port 8765 serves every session — Claude Code, Codex, Antigravity and Claude Desktop alike. Each harness connects through `kg mcp`, which starts the server when it is down and waits out restarts, so the tools stay connected.

> Anything operational — updates, autostart, Desktop connection, backups, troubleshooting — is written up as agent-followable recipes in `/kg-ops`. Telling the agent "run /kg-ops and fix the memory server" is a complete instruction.

```bash
kg status            # running? which version?
kg start | stop | restart
kg logs [-f]
kg editor [stop]     # browser-based graph explorer at http://localhost:8766
kg doctor            # check every piece the memory depends on
```

**Server details:** endpoint `http://127.0.0.1:8765/` (health: `/health`); logs, PID and the last start error in `~/.local/state/knowledge-graph/`.

---

## Claude Desktop

Claude Desktop uses the same memory as another client of the shared server. Its "Add custom connector" dialog won't take a local URL (those connectors are contacted from Anthropic's cloud), so `kg setup` writes Desktop's config file instead, pointing it at `kg mcp`. Fully quit and reopen Desktop afterwards; Cowork sessions receive the server through Desktop's own sandbox bridge.

Desktop's **Code tab** runs the Claude Code hooks, including preload and file recall. **Desktop chat** uses the MCP tools without those hooks: memory arrives on the first `kg_read`. Without a working directory that read opens user memory only; name the project's absolute path to attach its graph.

---

## Codex CLI

`kg setup` installs this plugin from the same marketplace (`knowledge-graph@maxim-plugins`).

Codex keeps a plugin's hooks off until you approve them. Run `/hooks` in Codex, trust the knowledge-graph hooks, and start a new session. Until then the `kg_*` tools work but nothing arrives on its own. A first `kg_read` offers a diagnostic hint when no live Codex session in this project has reported hooks; another session can suppress that hint, so check `/hooks` directly when recall is missing. Codex records trust against each hook's content, so an update that changes the hooks asks for it again.

Both harnesses talk to one local server, so a lesson captured in Codex is recalled in Claude Code and the other way round. What Codex CLI supports:

| Capability | Codex CLI support |
|---|---|
| MCP tools and shared memory | Supported, including when hooks are off |
| Preload and prompt recall | Supported with trusted hooks; preload fits 8,000 characters |
| File recall and read counters | `apply_patch` and explicit file operands of `cat`, `head`, `tail`, `less`, `sed -n`, `grep`, `jq`, `nl`, `rg`; relative shell paths need a verified execution directory |
| Hosted web search | No hook event, so no web-research capture nudges |
| `/kg-extract` | Codebase mapping works in either harness |
| `/kg-scout` | Mines Claude Code history and Codex rollouts; resumed rollouts use per-file cursors |
| Visual editor | Discovers stored project graphs through the server, including Codex-only projects |
| Background maintenance | Opt-in; the selected runner determines which subscription it spends |
| Codex desktop | Checked on Linux; this is not a verification of every desktop platform |
| macOS and Windows | Not verified by the Linux integration tests |

**Shell directories.** Codex reports the session directory in its hook and can omit `exec_command.workdir`. For relative paths, the server first honors an explicit absolute `workdir`; otherwise it matches the hook's invocation id to a completed command record in a bounded tail of that session's rollout and uses the recorded execution directory. This was measured live with nested and parallel calls on CLI 0.158.0. Missing, incomplete or ambiguous records leave relative paths unresolved; absolute operands still work. `nl` and `rg` support explicit file operands with recognized options; implicit directory searches and `rg --files` are not tracked. Rollouts must be local `.jsonl` files under the user's home directory, as for session recovery.

**Update:** run `codex plugin marketplace upgrade maxim-plugins`, then `codex plugin add knowledge-graph@maxim-plugins`; check the installed version with `codex plugin list`. Repoint optional shell helpers from the Codex cache, arrange a shared-server restart, and verify its version at `/health` (see `/kg-ops`). Check changed hooks in `/hooks`, then start a new session. A new session alone does not replace a healthy running server.

Maintenance chores can run through Codex too, spending your ChatGPT plan's limits instead of Claude's — see `/kg-ops` (Maintenance chores, `"runner"`).

---

## Antigravity CLI (Experimental)

The plugin also carries a native Antigravity CLI package: eager KG tools,
session preload, prompt recall and file recall, on the same memory server.
The CLI truncates large MCP results, so larger replies arrive through the
next `PreInvocation` hook in chunks, and nothing they imply is recorded until
the last chunk is delivered. A checkpoint (compaction) re-queues the preload.
Install with `agy plugin install <checkout>/knowledge-graph`; there is no
marketplace or git-URL install.

Budget notices, maintenance chores (including an Antigravity runner) and
`/kg-scout` history mining work here too. Signed-in resume, fork, clear and
compaction checks are still open. See the [Antigravity guide](ANTIGRAVITY.md).

---

## What the Memory Does on Its Own

The system is designed to work without being asked. With the harness hooks enabled:

- **Preloaded at session start** — the top-scored nodes of both graphs are in context before the first word, and one `kg_read` renders the rest.
- **Recall when a file is touched** — when the agent reads or edits a file, the memory that names that file arrives with the tool result; a node already in the session's context is not repeated.
- **Recall at the moment of relevance** — each prompt you type is matched against the graph server-side; when unseen nodes fit, their gists arrive with the prompt. Precision is deliberate: nothing injects twice, weak matches stay silent, and machine records (notifications, pasted images and paths) never trigger it — the channel only speaks when a human asked something.
- **Capture when re-derivation is proven** — reading a file a second session in a row (or fetching the same URL twice) with no node covering it earns a one-time nudge to write the bottom line down. First-time reads never nudge; hard throttles keep it rare.
- **Self-aware maintenance** — every read carries a `DEBT:` line per graph (oversized gists, unconnected nodes, touches that no longer resolve, episodes waiting to be lifted into a principle, time since last tended, weighted by how actively the graph is used). When it reads HIGH, `/kg-maintain` runs a bounded pass — or the agent spawns a maintenance subagent with the dispatch prompt the skill provides.
- **Budget notices** — the server reads the session's own quota gauge (Claude Code's status-line file, the Codex rollout, Antigravity's `/usage`) and says once per window when to plan the wrap-up (80% of five hours) and when to wrap up now (90%); 90/95% for the week. Codex sessions were found running to 90–97% without once reading their quota.
- **Chores, if you switch them on** — the server can also pay debt down while you work: one category, one or two targets it names itself, a handful of tool calls, run as a detached headless agent with MCP access limited to its maintenance tools, so your session spends no context on it. Codex also disables shell and hosted web and blocks filesystem writes with a read-only sandbox. Every dispatch and every refusal is logged. Chores run through Claude Code, Codex or Antigravity, each gated on its own subscription's limits. Off by default because they spend quota — `/kg-ops` has the switch and the gates.

## Usage Tips

Once the server is running, the agent captures insights automatically. A few habits that improve the experience:

- **Wrap up sessions explicitly** — say "wrapping up" before ending. This triggers reflection and writes the session's learnings to the graph.
- **Start fresh sessions over compacting** — finishing a task cleanly and starting a new session is more effective than context compaction. The graph preserves what matters.
- **Run `/kg-scout`** now and then to mine past Claude Code sessions and Codex rollouts for patterns worth keeping.

---

## Available Skills

| Skill | Type | Purpose |
|-------|------|---------|
| `kg-core` | Hidden (auto-loaded) | The memory doctrine: session protocol, recall, capture, search below the surface |
| `/kg-maintain` | User-invocable | Bounded maintenance pass that pays down the graph's DEBT line; includes the subagent dispatch prompt |
| `/kg-scout` | User-invocable | Mine Claude Code and Codex conversation history for patterns and insights |
| `/kg-extract` | User-invocable | Map codebase architecture into the knowledge graph |
| `/kg-ops` | User-invocable | Operations runbook: install, updates, server, Desktop/Cowork, Codex, chores, backup, troubleshooting |

In Codex the same five skills are listed to the agent; ask for one by name.

---

## Configuration

The server reads tunables from environment variables. Set them in your shell rc file (`~/.zshrc`, `~/.bashrc`) or in the systemd unit if you auto-start the server — then `kg restart` to pick up changes.

| Variable | Default | Description |
|----------|---------|-------------|
| `KG_GRACE_PERIOD_DAYS` | see `constants.py` | Days a newly created node is protected from archival; reads and updates do not restart this grace period |
| `KG_ORPHAN_GRACE_DAYS` | see `constants.py` | Days before orphaned archived nodes are permanently deleted |
| `KG_STORAGE_ROOT` | `~/.knowledge-graph` | Root directory for all graph data |
| `KG_SAVE_INTERVAL` | `30` | Auto-save interval (seconds) |
| `KG_BUDGET_NOTICES` | `1` | `0` turns budget notices off (or `"budget_notices": false` in `~/.knowledge-graph/chores.json`) |
| `KG_AUTOCOMMIT_INTERVAL` | `900` | Git auto-commit interval for the storage root (seconds); `0` disables. Only acts when `~/.knowledge-graph` is a git repository |

> Don't edit the plugin's bundled `.mcp.json` — that file just declares the HTTP endpoint the harness connects to (`http://127.0.0.1:8765/`), and it gets overwritten on every plugin update.

> **The size budget is fixed by design.** Budgets are exact rendered characters: 17,500 per level, 40,000 for the combined full-graph render, and 10,000 for preload (8,000 in Codex). They were sized for the measured Claude Code/Codex clients; arbitrary batches of full-node notes and other clients have separate delivery limits. Oversized full graphs hide the lowest-scored archived anchors and edges with counts and a search pointer.

> The full-graph figure is a target: active gists are preserved even if they
> alone exceed it, including while creation grace prevents archival. Such a
> render can exceed a client's inline limit; a maintenance pass or expiry of
> grace is needed to restore headroom.

---

## Data Locations

Graph data lives under `~/.knowledge-graph/` by default (`KG_STORAGE_ROOT` can change it), as JSON plus JSONL decision logs. Any file backup tool works.

- **User level:** `~/.knowledge-graph/user.json` — cross-project knowledge
- **Project level:** `~/.knowledge-graph/projects/<slug>/graph.json` — codebase-specific
- **Sessions:** `~/.knowledge-graph/sessions.json` — session registry
- **Tool-event counters:** `~/.knowledge-graph/projects/<slug>/tool_events.json` — per-target read/fetch counts feeding the capture nudges and the activity part of the DEBT score
- **Maintenance memory:** `~/.knowledge-graph/maintain.json` — the maintenance agent's own lessons, never shown in sessions
- **Logs:** `recall.jsonl` (what recall decided per prompt and tool event), `useful.jsonl` (endorsements), `chores.jsonl` (every chore decision) — all in `~/.knowledge-graph/`, size-capped
- **Chores:** `chores.json` (your switch and settings, if any) and `chore_state.json` (spacing and daily counts)

Project slugs use the final directory name, without a path hash. Project roots
with the same final name map to the same disk location; use distinct names
within a storage root. See [Data and Backup](https://github.com/mironmax/kg-memory/wiki/Data-and-Backup#file-locations).

### Built-in crash protection

Every save is atomic (write-to-temp → fsync → rename) and keeps one rolling copy of the previous good state beside it: `user.prev` for `user.json`, `graph.prev` for a project's `graph.json`. This protects against corruption from interrupted writes, not against accidental deletion or longer-term history.

To restore the previous state:
```bash
cp ~/.knowledge-graph/user.prev ~/.knowledge-graph/user.json
cp ~/.knowledge-graph/projects/<slug>/graph.prev \
   ~/.knowledge-graph/projects/<slug>/graph.json
```
If the server was running during the copy, make it re-read the disk: `curl -s 'http://127.0.0.1:8765/api/graph/read?reload=true'` (add `&project_path=<root>` for a project graph).

### Self-healing on load

If a node ever lands with its `gist`, `notes`, and tool-call markup mashed into one oversized string (an occasional client glitch), the server repairs it automatically — sanitizing on write and healing any existing damage when a graph is loaded, then writing the fix back. It's idempotent and never overwrites data you supplied. A `Healed N corrupt node(s) on load` log line means it did its job. See [Data and Backup](https://github.com/mironmax/kg-memory/wiki/Data-and-Backup#self-healing-on-load) for details.

### Versioned history and external backups

For versioned history, the plugin has git support built in. For off-machine copies, set up an external tool. Two options:

**Git** — one-time setup, then automatic:
```bash
cd ~/.knowledge-graph
git init
echo "*.prev" >> .gitignore
echo "*.tmp" >> .gitignore
git add -A && git commit -m "initial"
```
That's it: the server detects the repository and commits changes itself every 15 minutes (`Auto-save YYYY-MM-DD HH:MM` commits, only when something actually changed), plus a final commit on graceful shutdown. Tune or disable with `KG_AUTOCOMMIT_INTERVAL` (seconds; `0` disables). `kg commit` forces an immediate commit by hand.

**Borg** — better fit for frequently-changing data:
```bash
borg init --encryption=none ~/.knowledge-graph-borg
```
Add to crontab (`crontab -e`):
```
0 * * * * borg create --stats ~/.knowledge-graph-borg::'{now}' ~/.knowledge-graph
0 2 * * * borg prune ~/.knowledge-graph-borg --keep-hourly=24 --keep-daily=7 --keep-weekly=4
```
Borg deduplicates across archives, so hourly snapshots of mostly-unchanged JSON files cost almost nothing. Point-in-time restore:
```bash
borg extract ~/.knowledge-graph-borg::2026-05-17T03:00 --strip-components 3
```

---

## Uninstallation

```bash
/plugin uninstall knowledge-graph@maxim-plugins      # Claude Code
codex plugin remove knowledge-graph@maxim-plugins    # Codex CLI
```

Your knowledge data is preserved at `~/.knowledge-graph/`.

---

## License

MIT — see [LICENSE](LICENSE)

> Current version: see [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json), `/plugin list` in Claude Code, or `codex plugin list`.

---

## Changelog

See [`../CHANGELOG.md`](../CHANGELOG.md) for the full release history.
