# Knowledge Graph for Claude Code

Gives Claude a persistent memory that survives across sessions — not flat notes, but a graph of distilled insights connected by typed relationships. Claude captures patterns and decisions as you work; next session it recalls them automatically.

## Quick Install

```bash
# 1. Add the marketplace
/plugin marketplace add mironmax/claudecode-plugins

# 2. Install the plugin
/plugin install knowledge-graph@maxim-plugins

# 3. Run the setup script (installs kg-memory command + memory hook)
bash ~/.claude/plugins/knowledge-graph/install_command.sh

# 4. Restart Claude Code
```

**Done.** The setup script wires a lightweight hook that keeps memory in focus across all sessions.

**One more thing:**
- **Disable built-in auto-memory** — ⚙ Settings → Memory → toggle **Auto-memory off**. Without this, two memory systems run in parallel and write conflicting entries.

**Optional:**
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

The plugin runs a shared background server. It starts automatically on first use. You can manage it with the `kg-memory` command (after running `install_command.sh`):

```bash
kg-memory status    # Check if server is running
kg-memory start     # Start server
kg-memory stop      # Stop server
kg-memory restart   # Restart server
kg-memory logs      # View logs (tail -f)
```

If you skipped `install_command.sh`, use the script directly:
```bash
cd ~/.claude/plugins/knowledge-graph/server
./manage_server.sh status
```

**Server details:**
- Endpoint: `http://127.0.0.1:8765/`
- Health check: `http://127.0.0.1:8765/health`
- Logs: `/tmp/mcp_server.log`
- PID file: `/tmp/.mcp_server.pid`

**Auto-start on boot (Linux, optional):**
```bash
mkdir -p ~/.config/systemd/user
ln -s ~/.claude/plugins/knowledge-graph/server/memory-mcp.service ~/.config/systemd/user/memory-mcp.service
systemctl --user enable memory-mcp.service
systemctl --user start memory-mcp.service
```

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
| `kg-maintain` | Hidden (auto-loaded) | Self-reflection triggers, graph health, lifecycle |
| `/skill kg-scout` | User-invocable | Mine conversation history for patterns and insights |
| `/skill kg-extract` | User-invocable | Map codebase architecture into the knowledge graph |

---

## Configuration

Edit `~/.claude/plugins/knowledge-graph/.mcp.json` to customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `KG_MAX_TOKENS` | `4000` | Token limit before compaction triggers, per graph level |
| `KG_GRACE_PERIOD_DAYS` | see `constants.py` | Days a node is protected from archival after last update |
| `KG_ORPHAN_GRACE_DAYS` | see `constants.py` | Days before orphaned archived nodes are permanently deleted |
| `KG_STORAGE_ROOT` | `~/.knowledge-graph` | Root directory for all graph data |
| `KG_SAVE_INTERVAL` | `30` | Auto-save interval (seconds) |

---

## Data Locations

All data lives under `~/.knowledge-graph/` (git-trackable for backup and portability):

- **User level:** `~/.knowledge-graph/user.json` — cross-project knowledge
- **Project level:** `~/.knowledge-graph/projects/<slug>/graph.json` — codebase-specific
- **Sessions:** `~/.knowledge-graph/sessions.json` — session registry

Backup files (`.bak.*`) are excluded via `.gitignore`.

### Automatic Backups

The plugin creates tiered backups automatically:

| Tier | Count | Frequency |
|------|-------|-----------|
| Recent | 3 copies | Hourly |
| Daily | 7 copies | One per day |
| Weekly | 4 copies | One per week |

To restore from a backup:
```bash
# User-level graph:
cp ~/.knowledge-graph/user.json.bak.1 ~/.knowledge-graph/user.json

# Project-level graph:
cp ~/.knowledge-graph/projects/<slug>/graph.json.bak.daily.3 \
   ~/.knowledge-graph/projects/<slug>/graph.json
```

All saves use atomic writes (write-to-temp → fsync → rename) to prevent corruption from interrupted writes.

---

## Uninstallation

```bash
/plugin uninstall knowledge-graph@maxim-plugins
```

Your knowledge data is preserved at `~/.knowledge-graph/`.

---

## License

MIT — see [LICENSE](LICENSE)

## Version

**0.9.1**

---

## Changelog

**0.9.1**
- Compaction tuning: `COMPACTION_TARGET_RATIO` 0.9→0.8 (wider buffer), `GRACE_PERIOD_DAYS` 3→5, `ORPHAN_GRACE_DAYS` 30→365
- `constants.py` is now single source of truth — env var fallbacks import from constants; service file no longer overrides
- Storage safety: atomic writes + `.prev` rolling backup on every save
- Removed `migrate_storage.py` and `replay_sessions.py` (superseded)
- Docs: values in skills and docs now reference env vars and `constants.py` instead of hardcoded numbers
- Added comparison with MemPalace and Claude Code Auto-Memory

**0.9.0**
- Consolidated MCP tools from 13 to 8: removed `kg_ping`, `kg_session_stats`, `kg_register_session`, `kg_recall`, `kg_progress_get`, `kg_progress_set`
- `kg_read(cwd)` now initializes session and returns `session_id`
- `kg_read(cwd, id)` reads a single node and promotes archived nodes
- `kg_progress` merges get/set — omit `state` to read, include to write
- `kg_delete_node` and `kg_delete_edge` auto-resolve graph level (no `level` param needed)

**0.8.0**
- Zero-setup behavioral guidance via 4 hidden skills — no CLAUDE.md required
- Restructured into 6 focused skills (4 hidden + 2 user-invocable)
- Self-awareness mechanism: Claude checks graph is loaded before any task

**0.7.2**
- Recommend disabling built-in auto-memory
- User profile added as top-priority capture target
- Notes framed as home for rationale/"why"

**0.7.1**
- Renamed skills to `kg-` prefix to avoid name collisions

**0.7.0**
- Renamed plugin from "memory" to "knowledge-graph"
- Centralized storage at `~/.knowledge-graph/`
- Write-through persistence: every mutation saved immediately to disk
- Added `kg_search` for full-text search across active and archived nodes
- Multi-session safe server restart with `setsid`, PID validation

**0.6.x and earlier**
- Added `kg_progress` tools for persistent task state tracking
- Added `/skill kg-scout` and `/skill kg-extract`
- Initial multi-session sync, user/project level separation
