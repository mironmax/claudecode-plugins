# Changelog

All notable changes to this project are documented here.

## [0.9.11] - 2026-05-22

### Fixed
- Visual editor "Recall" action: switched broken `POST /api/nodes/{level}/{id}/recall` proxy to the existing `GET /api/nodes/{level}/{id}` REST read (which auto-promotes archived/orphaned nodes). Previously the action silently 500'd.
- Visual editor WebSocket URL: derived from `window.location` instead of hardcoded `:3000`, so the page works on any `EDITOR_PORT`.
- Systemd unit (`server/memory-mcp.service`) rewritten to invoke `~/.local/bin/kg-memory` (oneshot + RemainAfterExit). Previous unit pointed at `~/.claude/plugins/cache/maxim-plugins/memory/latest/server` — the wrong plugin name and a path that does not exist.
- `server/version.py` synced to plugin.json (was lagging at 0.9.9).
- `manage_server.sh`: removed `migrate` subcommand and `auto_migrate()` — they referenced `tools/migrate_storage.py` which was deleted in 0.9.1.

### Changed
- Docs: `ARCHITECTURE.md` scoring formula updated to `0.33×recency + 0.66×connectedness` (richness was dropped in 0.9.9); compaction budget shown as ~4000 tokens; stale version header removed.
- Docs: `wiki/Skills-Reference.md` skill table refreshed (six skills, hidden vs user-invocable split); `kg-extract` section rewritten to match the current Tier 1 / Tier 2 model and subsystem/component vocab; stale char-count table replaced with a one-line note.
- Docs: `wiki/Knowledge-Graph-API.md` `kg_search` entry now documents RRF ranking and actual return shape (gist + notes + score, not full node body).
- Docs: `wiki/Installation.md` corrected — six skills, not four; `kg-maintain` is auto-loaded *and* user-invocable, not "hidden."
- Docs: `wiki/Design-Decisions.md`, `Data-and-Backup.md` token references updated 3000 → 4000.
- Docs: `knowledge-graph/README.md` inline mini-changelog removed; replaced with a pointer to this file.
- Docs: `visual-editor/README.md` rewritten — it was stuck at the Read-Only MVP era. Now a dev-oriented overview pointing at `VISUAL_EDITOR_GUIDE.md` and the wiki for user-facing content.
- Skills: `kg-scout` and `kg-extract` frontmatter explicitly marked `user-invocable: true` for consistency with `kg-maintain`.
- Settings: `.claude/settings.local.json` cleaned of legacy MCP tool names (`kg_ping`, `kg_register_session`, `kg_progress_get`/`_set`, `kg_recall`) and shell-parsing artifacts (`Bash(rtk *)`, `Bash(done)`, `__NEW_LINE__` entries).

### Removed
- `knowledge-graph/mcp` — orphan thin wrapper around `manage_server.sh`, not referenced anywhere.
- `knowledge-graph/visual-editor/start.sh` — redundant with `manage_visual.sh` and had a port-3001 default that contradicted everything else.

## [0.9.10] - 2026-05-20

### Added
- Docs guidance on enabling Claude Code plugin auto-updates for the `maxim-plugins` marketplace (off by default for third-party sources). Covers `/plugin` UI flow, manual `/plugin marketplace update maxim-plugins`, and the `/reload-plugins` prompt that follows an automatic version bump.

### Changed
- Install flow simplified to three user-visible steps: marketplace add → plugin install → restart Claude Code.
- UserPromptSubmit memory hook moved into bundled `hooks/hooks.json` — auto-registers on plugin enable; no `~/.claude/settings.json` edits required.
- `install_command.sh` demoted to optional (only needed for the `kg-memory` / `kg-visual` shell command symlinks). Also performs idempotent cleanup of the legacy hook entry in `settings.json` for users upgrading from earlier installs.

### Fixed
- Docs no longer reference the non-existent `~/.claude/plugins/knowledge-graph/` flat path. Bundled assets are addressed via `${CLAUDE_PLUGIN_ROOT}` inside the plugin and `find ... | sort -V | tail -1` in the single user-facing shell command that still needs it.
- Server Management docs corrected: the HTTP MCP server requires manual start; it is not auto-started by Claude Code (previously implied otherwise).
- Configuration docs corrected: tunable env vars are read from the shell where the server is started, not from the plugin's bundled `.mcp.json` (which is overwritten on update).
- Systemd auto-start instructions use `cp` (not `ln -s`) so the unit file survives plugin cache churn on updates.
- Knowledge-graph plugin README version field synced to plugin.json (was lagging at 0.9.8).

## [0.9.9] - 2026-05-19

### Security
- Add `safe_project_path()` validator — user-supplied project paths are now constrained
  to within the user's home directory, preventing path traversal (CWE-022)
- Remove exception details (`str(e)`) from all HTTP 500 responses in the visual editor
  backend; errors are logged server-side only (CWE-209)
- Add `SECURITY.md` with responsible disclosure instructions and GitHub Advisory reporting
- Add `.github/dependabot.yml` for automated weekly pip dependency updates

### Changed
- Scorer redesign: drop `richness` dimension, refine `connectedness` to count only edges
  to/from active nodes (in×0.66 + out×0.33), add `resurrection` pass after archiving
- Grace period now based on `_created_ts` only — updates and reads no longer reset it,
  preventing active nodes from becoming permanently immune to compaction
- After archiving pass, a resurrection pass promotes any archived node that outscores a
  freshly-archived one by ≥ 0.05 margin
- `score_all()` accepts `include_archived` flag to support resurrection scoring
- Add `_created_ts` and `_last_read_ts` fields to `Node` TypedDict
- Update SKILL.md scoring formula description to match implementation
- Export `safe_project_path` from `core.__init__`

## [0.9.8] - 2026-05-14

### Changed
- `kg-maintain` hygiene passes (water/prune/fertilize) always run regardless of graph health score

## [0.9.7] - 2026-05-13

### Added
- Visual editor three-panel layout: projects list, graph canvas, details/connections panel
- Inline field editing in details panel
- Connections panel showing node edges

### Fixed
- WebSocket handshake: route `/ws` through ASGI dispatcher so visual editor stays Online

## [0.9.6]

### Fixed
- Removed tiered backup table (hourly/daily/weekly) and git auto-commit section from README and wiki — neither was ever implemented.
- Documented actual built-in protection: atomic writes + single `.prev` rolling copy per save.
- Added user-managed external backup guide: git (simple snapshots) and Borg (dedup-friendly, better for high-frequency data).
- Same corrections applied to wiki (`Data-and-Backup.md`, `Configuration.md`, `Home.md`).

## [0.9.5]

### Added
- `kg_search` upgraded to Reciprocal Rank Fusion: multi-term queries tokenize, rank per term by occurrence, then merge into a single unified ranking — user and project results sorted together by score.
- Without `session_id`, `kg_search` falls back to searching all loaded project graphs (best-effort); response includes a note explaining the limitation.
- `kg-maintain` made user-invocable (`/kg-maintain`): focused pass — health check, prune if large, fertilize, water — and reports what changed.
- `kg-visual` shell command added to `install_command.sh` (was previously a manual symlink).

### Changed
- Edge notes removed from full-graph `kg_read` output — edges show as `from --rel--> to` only; notes appear in single-node reads (same pattern as node notes).
- Size notification threshold raised 40K → 45K chars; tone shifted from warning to informational note suggesting `/kg-maintain`.
- All skill language rewritten for calm, professional tone — imperative/enforcement framing replaced with collaborative guidance throughout `kg-core`, `kg-recall`, `kg-capture`, `kg-maintain`.
- `kg-core` skill body: new Server Operations section documenting both `kg-memory` and `kg-visual` with subcommands, ports, install path, troubleshooting.

## [0.9.4]

### Added
- README Prerequisites section: Python 3 + pip install instructions for macOS, Linux, Windows.

### Changed
- Skill guidance rewritten across all four hidden skills for sharper, more actionable capture / recall / maintain / extract rules. `kg-extract` introduces the two-tier index (subsystem + component) with skip-decision gist patterns.
- Quick Install: marketplace URL changed to `https://github.com/mironmax/claudecode-plugins`; setup script path uses `find` to auto-locate the version-stamped cache dir.
- `kg-remind` hook rotates through 18 targeted prompts (was a single generic reminder).

### Removed
- Scheduler plugin — superseded by Claude Code's native `/schedule` skill.

## [0.9.3]

### Added
- Three-tier compaction: active → archived → orphaned. Pass 1 archives lowest-scored active nodes; pass 2 orphans lowest-connectivity archived nodes when archived section exceeds 30% of token budget. Orphans are invisible in `kg_read`/`kg_sync`, searchable via `kg_search`, chain-rescued when adjacent archived nodes are read, permanently deleted after 365 days without recall.
- Four hidden skills: `kg-core`, `kg-capture`, `kg-recall`, `kg-maintain`. Descriptions rewritten to fit the 1,536-char per-skill hard limit (previously silently truncated at 38–65%).
- `UserPromptSubmit` hook for ambient memory reminders — injects a short prompt via `additionalContext`. `install_command.sh` wires it into `~/.claude/settings.json` idempotently.

### Changed
- Encoding doctrine added to `kg-capture` (telegraphic gist style, gist vs notes boundary, edge-first principle).
- Garden rhythm added to `kg-maintain` (water/prune/fertilize as proactive tending alongside reactive triggers).

## [0.9.1]

### Changed
- Compaction tuning: `COMPACTION_TARGET_RATIO` 0.9 → 0.8 (wider buffer), `GRACE_PERIOD_DAYS` 3 → 5, `ORPHAN_GRACE_DAYS` 30 → 365.
- `constants.py` is now the single source of truth — env var fallbacks import from constants; service file no longer overrides.
- Storage safety: atomic writes + `.prev` rolling backup on every save.
- Docs: values in skills and docs reference env vars and `constants.py` instead of hardcoded numbers.
- Added comparison with MemPalace and Claude Code Auto-Memory in wiki.

### Removed
- `migrate_storage.py` and `replay_sessions.py` (superseded by centralized storage).

## [0.9.0]

### Changed
- Consolidated MCP tools from 13 → 8. Removed `kg_ping`, `kg_session_stats`, `kg_register_session`, `kg_recall`, `kg_progress_get`, `kg_progress_set`.
- `kg_read(cwd)` initializes session and returns `session_id`.
- `kg_read(cwd, id)` reads a single node and promotes archived nodes.
- `kg_progress` merges get/set — omit `state` to read, include to write.
- `kg_delete_node` and `kg_delete_edge` auto-resolve graph level (no `level` param needed).
- Default `KG_MAX_TOKENS` raised to 4000.

## [0.8.0]

### Added
- Zero-setup behavioral guidance via four hidden skills — no CLAUDE.md required.
- Self-awareness mechanism: Claude checks graph is loaded before any task.

### Changed
- Restructured into six focused skills (four hidden + two user-invocable).

## [0.7.2]

### Added
- User profile as top-priority capture target — calibrate explanations to the user's domain knowledge.
- `CAPTURE.md` "Preserving the Why" section: notes as the home for rationale, recalled on demand.
- `RECALL.md`: recall active nodes for their notes when rationale matters.

### Changed
- Recommend disabling Claude Code's built-in auto-memory (conflicts with KG).
- Recommend a single global `CLAUDE.md` only; project-level files cause instruction conflicts.

## [0.7.1]

### Changed
- Renamed skills to `kg-` prefix to avoid name collisions across plugins.

## [0.7.0]

### Added
- Server tools (`migrate_storage`, `replay_sessions`) and `manage_visual.sh`.
- Scheduler plugin (new, second plugin in the marketplace): MCP stdio server for task scheduling, usage-monitor hook, launcher, installer, skills, templates.
- `kg_search` for full-text search across active and archived nodes.

### Changed
- Plugin renamed from `memory-plugin` to `knowledge-graph` to better reflect the underlying model.
- Centralized storage moved to `~/.knowledge-graph/`.
- Write-through persistence: every mutation saved to disk immediately.
- Multi-session-safe server restart with `setsid` + PID validation.
- Visual editor UI/CSS overhaul; streamable server, store, session manager enhancements.
- Architecture docs rewritten.

## [0.6.1]

### Fixed
- Reversed `kg_read` / `kg_register_session` order so the project graph loads on startup.
- `kg_register_session` accepts a `cwd` parameter for automatic `graph.json` resolution.
- Sync timestamp tracking (`mark_synced`) prevents duplicate sync diffs; `kg_sync` handler advances the watermark after each call.
- `project_discovery` hardened: scans multiple session files / lines for `cwd`.
- Tighter visual editor graph simulation forces.

## [0.6.0]

### Added
- `kg_progress_get` / `kg_progress_set` tools for persistent task progress (stored in `_meta.progress` in graph JSON).
- `kg_session_stats` tool (duration, op count, graph sizes); per-session operation counting.
- Session persistence — `~/.claude/knowledge/sessions.json` survives server restarts; auto-recover unknown sessions gracefully.
- `/skill scout` — tension-driven mining of conversation history for pattern extraction.
- `/skill extract` — map codebase architecture into the knowledge graph.
- Visual editor write support: create / edit / delete nodes and edges via REST proxy.
- WebSocket transport for visual editor real-time updates (replaces polling); context menu, modals, toast notifications.
- `VISUAL_EDITOR_GUIDE.md`.

### Changed
- Memory skill restructured into `SKILL.md` (100-line overview) + reference files (`CAPTURE.md`, `RECALL.md`, `MAINTAIN.md`).
- `persistence.py` returns a 3-tuple (graph, versions, progress); REST endpoints for progress and session stats.
- `CLAUDE.md` template adds session-lifecycle guidance and available-skills routing.

## [0.5.14]

### Changed
- Project graph path consolidated to `.claude/knowledge/graph.json` (hardcoded).
- Added global `kg-memory` command for server management from anywhere.
- Auto-generated `.gitignore` for project knowledge folders.

### Removed
- Legacy path support (`.knowledge/`, `.claude/graph.json`).

## [0.5.13]

### Fixed
- Streamable HTTP transport for Claude Code: `json_response=False → True` in `StreamableHTTPSessionManager` (was using SSE format instead of JSON-RPC over HTTP). Resolves "Failed to reconnect to plugin:memory:kg" errors.
- Orphaned-edge cleanup on graph load.

### Added
- `project_path` parameter to `read_graphs()` REST API.

### Removed
- Dead code: unused `mcp_http/app.py`.

## Earlier versions

Versions before 0.5.13 predate this changelog. The earliest commit in the current history is the initial Streamable HTTP transport work; older code is no longer in git history (an early force-push removed sensitive data that had leaked into commits). See [`ARCHITECTURE.md`](knowledge-graph/ARCHITECTURE.md) "Origin & Evolution" for the pre-history of the design (ByteRover Cipher → TypeScript MCP with Steiner trees → current compression-first architecture).
