# Knowledge Graph for Claude Code

Gives Claude a persistent memory that survives across sessions — not flat notes, but a graph of distilled insights connected by typed relationships. Claude captures patterns and decisions as you work; next session it recalls them automatically.

The design puts the intelligence at **capture time**: knowledge is compressed by the model in the moment of insight, stored as headline + relationships, and read back natively — no embeddings, no retrieval engine, just structured text a language model is built to consume. That makes the memory compound with model capability: sharper models write denser nodes and extract more from the same graph. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design thesis.

## Prerequisites

- **Claude Code** — CLI or desktop app
- **Python 3** — required to run the MCP server

  ```bash
  # Check if you have it:
  python3 --version

  # Install if missing:
  # macOS:   brew install python3
  # Ubuntu:  sudo apt install python3
  # Arch:    sudo pacman -S python
  # Windows: https://python.org/downloads (check "Add to PATH")
  ```

- **pip** — usually bundled with Python 3; if missing: `python3 -m ensurepip`

---

## Quick Install

```bash
# 1. Add the marketplace
/plugin marketplace add mironmax/claudecode-plugins

# 2. Install the plugin
/plugin install knowledge-graph@maxim-plugins

# 3. Restart Claude Code
```

**Done.** The plugin ships a UserPromptSubmit hook in `hooks/hooks.json` that auto-loads on session start — no setup script, no settings.json edits.

> Already in a session? Run `/reload-plugins` instead of restarting.

**One more thing:**
- **Disable built-in auto-memory** — ⚙ Settings → Memory → toggle **Auto-memory off**. Without this, two memory systems run in parallel and write conflicting entries.
- **Enable plugin auto-updates** — `/plugin` → **Marketplaces** → `maxim-plugins` → **Enable auto-update**. Third-party marketplaces are off by default, so this is the only way to stay current without manual refreshes.

**Optional:**
- **`kg-memory` / `kg-visual` shell commands** — for managing the server from your terminal. See [Server Management](#server-management) below.
- **Auto-approval** — skip permission prompts by adding the permissions below to `~/.claude/settings.json`.

---

## Enable Auto-Approval (Optional)

By default, Claude Code asks permission for each MCP tool call. To skip these prompts, add to `~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "mcp__plugin_knowledge-graph_kg__kg_read",
      "mcp__plugin_knowledge-graph_kg__kg_put_node",
      "mcp__plugin_knowledge-graph_kg__kg_put_edge",
      "mcp__plugin_knowledge-graph_kg__kg_sync",
      "mcp__plugin_knowledge-graph_kg__kg_delete_node",
      "mcp__plugin_knowledge-graph_kg__kg_delete_edge",
      "mcp__plugin_knowledge-graph_kg__kg_search",
      "mcp__plugin_knowledge-graph_kg__kg_progress"
    ]
  }
}
```

If you already have a `settings.json`, merge these into your existing `permissions.allow` array — don't paste the whole block or you'll get duplicate keys.

---

## Server Management

The plugin runs a shared HTTP MCP server on port 8765, used by every Claude Code session simultaneously. **It starts automatically** — a SessionStart hook launches it whenever it's down, and the start script builds its Python environment on first run (and again after plugin updates, which install into a fresh directory). The hook only ever starts the server; it never stops or restarts one you're running.

> Anything operational — updates, autostart, Desktop connection, backups, troubleshooting — is written up as agent-followable recipes in `/kg-ops`. Telling Claude "run /kg-ops and fix the memory server" is a complete instruction.

> If a session connected while the server was still down (e.g. the very first run), the `kg_*` tools stay offline for that session — run `/mcp`, select `plugin:knowledge-graph:kg`, and hit **Reconnect** once the server is up.

For manual control from your terminal, **install the helper commands** (one-time, optional):

```bash
bash "$(find ~/.claude/plugins/cache/maxim-plugins/knowledge-graph -name install_command.sh | sort -V | tail -1)"
```

That symlinks `kg-memory` and `kg-visual` into `~/.local/bin/`. Make sure `~/.local/bin` is in your `PATH`.

```bash
# MCP graph server
kg-memory start     # Start server
kg-memory status    # Check if server is running
kg-memory stop      # Stop server
kg-memory restart   # Restart server
kg-memory logs      # View logs (tail -f)

# Visual editor — browser-based graph explorer (optional)
kg-visual start     # Start at http://localhost:8766
kg-visual stop
kg-visual status
kg-visual logs
```

**Server details:**
- Endpoint: `http://127.0.0.1:8765/`
- Health check: `http://127.0.0.1:8765/health`
- Logs: `~/.local/state/knowledge-graph/mcp_server.log`
- PID file: `.mcp_server.pid` (next to `manage_server.sh` in the plugin's `server/` directory)

**Auto-start on boot (Linux, optional):** the bundled systemd unit invokes the `kg-memory` shim, so it survives plugin updates without needing to be refreshed.

Prerequisite: run `install_command.sh` once (see [Server Management](#server-management) above) so `kg-memory` exists in `~/.local/bin/`.

```bash
mkdir -p ~/.config/systemd/user
cp "$(find ~/.claude/plugins/cache/maxim-plugins/knowledge-graph -name memory-mcp.service | sort -V | tail -1)" \
   ~/.config/systemd/user/memory-mcp.service
systemctl --user enable memory-mcp.service
systemctl --user start memory-mcp.service
```

---

## Claude Desktop (Optional)

Claude Desktop can use the same memory — it becomes another client of the shared server. Desktop's "Add custom connector" dialog won't take a local URL (those connectors are contacted from Anthropic's cloud, so they require a public https address); the local route is Desktop's config file, and the plugin automates it:

```
/kg-ops connect Claude Desktop
```

The setup registers a stdio bridge (`mcp-remote`, needs Node.js ≥ 18) in `claude_desktop_config.json`, with paths resolved for your machine. The bridge auto-starts the server if Desktop launches first. Fully quit and reopen Desktop afterwards; Cowork sessions receive the server through Desktop's own sandbox bridge. Remove anytime with `setup_desktop.py --remove`.

Note: Desktop sessions get no session-start preload (that's a Claude Code hook) — memory arrives on the first `kg_read` call instead.

---

## Usage Tips

Once the server is running, Claude captures insights automatically. A few habits that improve the experience:

- **Wrap up sessions explicitly** — tell Claude "wrapping up" before ending. This triggers reflection and writes the session's learnings to the graph.
- **Start fresh sessions over compacting** — finishing a task cleanly and starting a new session is more effective than context compaction. The graph preserves what matters.
- **Use `/skill kg-scout`** after a long session to mine the conversation for patterns worth keeping.

---

## Available Skills

| Skill | Type | Purpose |
|-------|------|---------|
| `kg-core` | Hidden (auto-loaded) | Session protocol, self-awareness, API reference |
| `kg-capture` | Hidden (auto-loaded) | Capture rules, compression, search-before-put |
| `kg-recall` | Hidden (auto-loaded) | Proactive recall, memory traces, sync timing |
| `/skill kg-maintain` | User-invocable | Focused maintenance pass: prune, fertilize, health check |
| `/skill kg-scout` | User-invocable | Mine conversation history for patterns and insights |
| `/skill kg-extract` | User-invocable | Map codebase architecture into the knowledge graph |
| `/skill kg-ops` | User-invocable | Operations runbook: install, updates, server, Desktop/Cowork, backup, troubleshooting |

---

## Configuration

The server reads tunables from environment variables. Set them in your shell rc file (`~/.zshrc`, `~/.bashrc`) or in the systemd unit if you auto-start the server — then `kg-memory restart` to pick up changes.

| Variable | Default | Description |
|----------|---------|-------------|
| `KG_GRACE_PERIOD_DAYS` | see `constants.py` | Days a node is protected from archival after last update |
| `KG_ORPHAN_GRACE_DAYS` | see `constants.py` | Days before orphaned archived nodes are permanently deleted |
| `KG_STORAGE_ROOT` | `~/.knowledge-graph` | Root directory for all graph data |
| `KG_SAVE_INTERVAL` | `30` | Auto-save interval (seconds) |
| `KG_AUTOCOMMIT_INTERVAL` | `900` | Git auto-commit interval for the storage root (seconds); `0` disables. Only acts when `~/.knowledge-graph` is a git repository |

> Don't edit the plugin's bundled `.mcp.json` — that file just declares the HTTP endpoint Claude Code connects to (`http://127.0.0.1:8765/`), and it gets overwritten on every plugin update.

> **The size budget is fixed by design.** Budgets are exact rendered characters — 17,500 per graph level, 40,000 for a whole `kg_read` result — chosen so the output always fits inline in Claude's context instead of spilling to a persisted file. That guarantee is arithmetic over the fixed constants; a knob would break it. If output ever needs trimming (e.g. a graph maintained by an older server), the server hides the lowest-scored archived anchors and edges and says so in the output — never active knowledge.

---

## Data Locations

All data lives under `~/.knowledge-graph/`. The files are plain JSON, so any file backup tool works.

- **User level:** `~/.knowledge-graph/user.json` — cross-project knowledge
- **Project level:** `~/.knowledge-graph/projects/<slug>/graph.json` — codebase-specific
- **Sessions:** `~/.knowledge-graph/sessions.json` — session registry

### Built-in crash protection

Every save is atomic (write-to-temp → fsync → rename) and keeps one rolling copy of the previous good state as `<file>.prev`. This protects against corruption from interrupted writes, not against accidental deletion or longer-term history.

To restore the previous state:
```bash
cp ~/.knowledge-graph/user.json.prev ~/.knowledge-graph/user.json
cp ~/.knowledge-graph/projects/<slug>/graph.json.prev \
   ~/.knowledge-graph/projects/<slug>/graph.json
```

### Self-healing on load

If a node ever lands with its `gist`, `notes`, and tool-call markup mashed into one oversized string (an occasional client glitch), the server repairs it automatically — sanitizing on write and healing any existing damage when a graph is loaded, then writing the fix back. It's idempotent and never overwrites data you supplied. A `Healed N corrupt node(s) on load` log line means it did its job. See [Data and Backup](https://github.com/mironmax/claudecode-plugins/wiki/Data-and-Backup#self-healing-on-load) for details.

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
That's it: the server detects the repository and commits changes itself every 15 minutes (`Auto-save YYYY-MM-DD HH:MM` commits, only when something actually changed), plus a final commit on graceful shutdown. Tune or disable with `KG_AUTOCOMMIT_INTERVAL` (seconds; `0` disables). `kg-memory commit` forces an immediate commit by hand.

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
/plugin uninstall knowledge-graph@maxim-plugins
```

Your knowledge data is preserved at `~/.knowledge-graph/`.

---

## License

MIT — see [LICENSE](LICENSE)

> Current version: see [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) or `/plugin list` inside Claude Code.

---

## Changelog

See [`../CHANGELOG.md`](../CHANGELOG.md) for the full release history.
