# Knowledge Graph for Claude Code

Extract and remember patterns, insights, and relationships worth preserving across sessions. Unlike flat-file memory systems, knowledge here is a graph — nodes gain meaning through edges. Unlike verbatim-storage systems, only distilled insights are kept, not raw transcripts.

## Features

- 🧠 **Persistent Memory** — Knowledge survives across sessions
- ⚡ **Write-Through Persistence** — Every mutation saved immediately to disk
- 🔄 **Multi-Session** — Share knowledge across parallel sessions and agents
- 🎯 **Two Levels** — User (cross-project) and Project (codebase-specific)
- 🗜️ **Auto-Compaction** — Automatically manages context window size
- ♻️ **Memory Traces** — Archived knowledge remains discoverable
- 📊 **Progress Tracking** — Persistent state for long-running tasks (scout, extract)
- 🔍 **Full-Text Search** — `kg_search` across active and archived nodes
- 🔍 **Scout Skill** — Mine conversation history for patterns (`/skill kg-scout`)
- 🗺️ **Extract Skill** — Map codebase architecture into the graph (`/skill kg-extract`)

## Important notes

Most of the time system works out of the box. But one useful pattern is this:
1. Do not go for context compaction, it is wasteful, I think. Instead try to finish session with some tangible result, like completing task altogether or writing transient results into. Then clear the session and start anew, now reading this file as input.
2. Before the end of each session, explicitly say that you are wrapping up the session. That would often trigger some additional memory writes. But also take a few minutes for reflection what was working and what's not. This helps to create some overarching learning.

## Installation

### Via Marketplace

```bash
# 1. Add the marketplace
/plugin marketplace add mironmax/claudecode-plugins

# 2. Install the plugin
/plugin install knowledge-graph@maxim-plugins

# 3. Disable Claude Code's built-in auto-memory (see below)

# 4. Install global command (optional but recommended)
bash ~/.claude/plugins/knowledge-graph/install_command.sh

# 5. Restart Claude Code
```

### Disable Built-in Auto-Memory

Claude Code has a built-in auto-memory system that writes to `~/.claude/projects/*/memory/`. It runs in parallel with the knowledge graph, causing duplicate, contradicting memory and wasted context. Disable it:

1. Open Claude Code settings: **⚙ Settings → Memory**
2. Toggle **Auto-memory** off

With auto-memory off, the knowledge graph is the single source of persistent memory.

### Enable Auto-Approval (Optional)

To skip permission prompts, add these permissions to your `~/.claude/settings.json`:

**If you don't have a settings.json yet**, create it with:

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

**If you already have a settings.json**, add the permissions to your existing `permissions.allow` array:

```json
{
  "permissions": {
    "allow": [
      // ... your existing permissions ...
      "mcp__plugin_knowledge-graph_kg__kg_read",
      "mcp__plugin_knowledge-graph_kg__kg_put_node",
      "mcp__plugin_knowledge-graph_kg__kg_put_edge",
      "mcp__plugin_knowledge-graph_kg__kg_sync",
      "mcp__plugin_knowledge-graph_kg__kg_delete_node",
      "mcp__plugin_knowledge-graph_kg__kg_delete_edge",
      "mcp__plugin_knowledge-graph_kg__kg_search",
      "mcp__plugin_knowledge-graph_kg__kg_progress"
    ],
    "deny": [/* ... your existing denies ... */]
  }
}
```

⚠️ **Important**: Don't append the entire JSON object if you already have settings — merge the permissions into your existing `allow` array to avoid JSON syntax errors.

## Usage

### Server Management

The plugin uses a **shared MCP server** that runs in the background. The server starts automatically on first use, but you have full control over it.

**Global command (recommended):**

After running `install_command.sh`, you can manage the server from anywhere:

```bash
kg-memory status    # Check if server is running
kg-memory start     # Start server
kg-memory stop      # Stop server
kg-memory restart   # Restart server
kg-memory logs      # View logs (tail -f)
```

**Direct script (alternative):**
```bash
cd ~/.claude/plugins/knowledge-graph/server
./manage_server.sh status
```

**If server is not running:**
- The plugin will show an error when you try to use memory tools
- Simply run `kg-memory start` (or `./manage_server.sh start` from the server directory)
- Or restart Claude Code to auto-start the server

**Server details:**
- Endpoint: `http://127.0.0.1:8765/`
- Health check: `http://127.0.0.1:8765/health`
- Logs: `/tmp/mcp_server.log`
- PID file: `/tmp/.mcp_server.pid`

**Advanced: systemd service (optional)**

For auto-start on boot and auto-restart on crashes (Linux only):

```bash
# Link service file
mkdir -p ~/.config/systemd/user
ln -s ~/.claude/plugins/knowledge-graph/server/memory-mcp.service ~/.config/systemd/user/memory-mcp.service

# Enable and start
systemctl --user enable memory-mcp.service
systemctl --user start memory-mcp.service

# Check status
systemctl --user status memory-mcp.service
```

Note: Using systemd service is optional. The default `manage_server.sh` approach gives you more direct control.

### Working with Knowledge Graph

Once the server is running:

- Claude captures insights as you work
- Knowledge persists across sessions
- The plugin's hidden skills automatically guide Claude's memory behavior
- No CLAUDE.md setup required — behavioral rules are loaded via skill descriptions

### Available Skills

| Skill | Type | Purpose |
|-------|------|---------|
| `kg-core` | Hidden (auto-loaded) | Session protocol, self-awareness, API reference |
| `kg-capture` | Hidden (auto-loaded) | Capture rules, compression, search-before-put |
| `kg-recall` | Hidden (auto-loaded) | Proactive recall, memory traces, sync timing |
| `kg-maintain` | Hidden (auto-loaded) | Self-reflection triggers, graph health, lifecycle |
| `/skill kg-scout` | User-invocable | Mine conversation history for patterns and insights |
| `/skill kg-extract` | User-invocable | Map codebase architecture into the knowledge graph |

## Configuration

Edit `~/.claude/plugins/knowledge-graph/.mcp.json` to customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `KG_SAVE_INTERVAL` | `30` | Auto-save interval (seconds) |
| `KG_MAX_TOKENS` | `4000` | Token limit before compaction, per graph level. Two graphs ≈ 32K chars, safe under Claude Code's 50K tool output limit. Not recommended to raise — nodes in the grace period can temporarily exceed the limit. |
| `KG_ORPHAN_GRACE_DAYS` | see `constants.py` | Days before orphaned archived nodes are permanently deleted |
| `KG_GRACE_PERIOD_DAYS` | see `constants.py` | Days a node is protected from archival after last update |
| `KG_STORAGE_ROOT` | `~/.knowledge-graph` | Root directory for all graph data |

**Note:** Storage is centralized at `~/.knowledge-graph/` by default. Override with `KG_STORAGE_ROOT`.

## Data Locations

All data is centralized under `~/.knowledge-graph/` (git-tracked):

- **User level:** `~/.knowledge-graph/user.json` — Cross-project knowledge
- **Project level:** `~/.knowledge-graph/projects/<slug>/graph.json` — Codebase-specific
- **Sessions:** `~/.knowledge-graph/sessions.json` — Session registry

### Git and Sharing

The `~/.knowledge-graph/` directory is designed to be git-tracked for backup and portability. Backup files (`.bak.*`) are excluded via `.gitignore`.

## Backup and Recovery

The plugin automatically creates tiered backups to protect against data corruption or accidental changes:

### Backup Tiers

1. **Recent backups** (3 copies) — `.json.bak.1`, `.bak.2`, `.bak.3`
   - Created hourly (minimum 1 hour between backups)
   - Most recent snapshots

2. **Daily backups** (7 copies) — `.json.bak.daily.1` through `.bak.daily.7`
   - One backup per day, kept for 7 days
   - Provides coverage for recent changes

3. **Weekly backups** (4 copies) — `.json.bak.weekly.1` through `.bak.weekly.4`
   - One backup per week, kept for 4 weeks
   - Long-term recovery option

### Recovery

If you need to restore from a backup:

```bash
# For user-level graph:
cp ~/.knowledge-graph/user.json.bak.1 ~/.knowledge-graph/user.json

# For project-level graph:
cp ~/.knowledge-graph/projects/<slug>/graph.json.bak.daily.3 ~/.knowledge-graph/projects/<slug>/graph.json
```

Choose the appropriate backup tier based on when the corruption occurred. The plugin will automatically reload on next session.

### Atomic Writes

All saves use atomic writes (write-to-temp, then rename) to prevent corruption from interrupted writes.

## Uninstallation

```bash
/plugin uninstall knowledge-graph@maxim-plugins
```

Your knowledge data is preserved in the locations above.

### Migration Tools

- **`server/tools/migrate_storage.py`** — Migrate from old per-project storage to centralized `~/.knowledge-graph/`
- **`server/tools/replay_sessions.py`** — Replay session history for debugging or recovery

## License

MIT License — see [LICENSE](LICENSE)

## Version

0.9.1

### Changelog

**0.9.1**
- Compaction tuning: `COMPACTION_TARGET_RATIO` 0.9→0.8 (wider buffer, less re-compaction churn), `GRACE_PERIOD_DAYS` 3→5 (longer protection window), `ORPHAN_GRACE_DAYS` 30→365 (archived orphans kept 1 year)
- `constants.py` is now single source of truth — `mcp_streamable_server.py` env var fallbacks import from constants; service file no longer hardcodes overriding values
- Removed `server/tools/migrate_storage.py` and `server/tools/replay_sessions.py` (superseded by current storage layout and skills)
- Storage safety: atomic writes + `.prev` rolling backup on every save
- Docs: day/ratio values in skills and docs now reference env vars and `constants.py` instead of hardcoded numbers
- Added comparison with MemPalace and Claude Code Auto-Memory in `Design-Decisions.md` and wiki `Home.md`

**0.9.0**
- Consolidated MCP tools from 13 to 8: removed `kg_ping`, `kg_session_stats`, `kg_register_session`, `kg_recall`, `kg_progress_get`, `kg_progress_set`
- `kg_read(cwd)` now initializes session and returns `session_id` (replaces `kg_register_session`)
- `kg_read(cwd, id)` reads a single node's full content and promotes archived nodes (replaces `kg_recall`)
- `kg_progress` merges get/set — omit `state` to read, include to write
- `kg_delete_node` and `kg_delete_edge` auto-resolve graph level (no `level` param needed)
- Removed repetitive "connect with edge" tip from `kg_put_node` response (guidance lives in skill descriptions)
- Updated all skill files to reference new tool API
- Updated permission names to use `knowledge-graph` plugin prefix

**0.8.0**
- Zero-setup behavioral guidance: 4 hidden skills (kg-core, kg-capture, kg-recall, kg-maintain) auto-load rules into every session via skill descriptions (~8K chars, within 16K budget)
- Eliminated CLAUDE.md template requirement — plugin works out of the box after install
- Enriched MCP tool descriptions with point-of-use behavioral nudges (search-before-put, recall-bias, session-awareness)
- Restructured from single kg-memory skill into 6 focused skills (4 hidden + 2 user-invocable)
- Self-awareness mechanism: Claude checks if graph is loaded before any task, not just at session start

**0.7.2**
- Recommend disabling Claude Code built-in auto-memory to avoid duplicate/conflicting memory
- User profile added as top-priority capture target
- Notes explicitly framed as home for rationale/"why" — recalled on demand, keeps active context lean
- devdocs/ moved to gitignore (internal scratch only)

**0.7.1**
- Renamed skills to `kg-` prefix to avoid name collisions
- Improved archival scoring and recall strategy

**0.7.0**
- Renamed plugin from "memory" to "knowledge-graph"
- Centralized storage at `~/.knowledge-graph/` (user, projects, sessions)
- Write-through persistence: every mutation saved immediately to disk
- Added `kg_search` tool for full-text search across active and archived nodes
- Added migration tool (`server/tools/migrate_storage.py`) for old storage layout
- Added session replay tool (`server/tools/replay_sessions.py`)
- Safe server restart with `setsid`, PID validation, `stop-port` command
- Added `manage_visual.sh` for visual editor management (`kg-visual` symlink)
- `KG_MAX_TOKENS` default raised to 4000 (safe within Claude Code's 10K warning / 25K cap per tool output)
- Updated `KG_ORPHAN_GRACE_DAYS` default to 30, `KG_GRACE_PERIOD_DAYS` to 3
- Added `KG_STORAGE_ROOT` env var for storage location override

**0.6.1**
- Fixed session startup order and sync deduplication
- Project discovery improvements

**0.6.0**
- Added `kg_progress_get` / `kg_progress_set` tools for persistent task progress tracking (consolidated to `kg_progress` in v0.9.0)
- Added `kg_session_stats` tool for session duration, operation counts, and graph sizes (removed in v0.9.0)
- Added operation counting per session (tracked via `session_manager.increment_ops()`)
- Added `/skill kg-scout` — mine conversation history for patterns and insights
- Added `/skill kg-extract` — map codebase architecture into the knowledge graph
- Restructured skill documentation: SKILL.md (overview) + CAPTURE.md, RECALL.md, MAINTAIN.md reference files
- Updated CLAUDE.md template with session lifecycle guidance and skill routing
- REST API endpoints for progress and session stats (visual editor)
- Progress data persists in `_meta.progress` within graph JSON files

**0.5.14**
- Consolidated project graph path to `.claude/knowledge/graph.json` (hardcoded, mirrors user-level structure)
- Added global `kg-memory` command for server management from anywhere (`install_command.sh`)
- Auto-generated `.gitignore` for project knowledge folders (private by default, instructions for team sharing)
- Removed legacy path support (`.knowledge/`, `.claude/graph.json`)

**0.5.13**
- Fixed MCP Streamable HTTP transport: changed json_response=False to json_response=True for Claude Code compatibility
- Added orphaned edge cleanup on graph load
- Added project_path parameter to read_graphs() REST API endpoint
- Removed dead code: mcp_http/app.py (unused FastAPI app)

**0.4.2**
- Fixed backup tier promotion logic (oldest backups now properly promote to next tier)
- Added session_id tracking to recall() for sync consistency
- Removed unused helper methods and TypeVar
- Moved static utility methods to module level for cleaner code
- Added level validation to all public methods
- Standardized type hints to use Python 3.10+ `X | None` syntax

**0.4.1**
- Code refactoring and tiered backup strategy improvements

**0.4.0**
- Auto-compaction with 7-day grace period
- Percentile-based scoring for archiving decisions
- Memory traces: edges to archived nodes remain visible
- `kg_recall` to retrieve archived knowledge
- Node deletion now removes connected edges
- New dict-based file format (breaking change — delete old files before upgrading)

**0.3.x**
- Initial release with multi-session sync
- User and project level separation
