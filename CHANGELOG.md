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
- Backup documentation corrections
- Remove never-implemented tiered backup system references

## [0.9.5]

### Added
- RRF (Reciprocal Rank Fusion) multi-term search
- Edge note trimming
- `kg-maintain` user-invocable as a skill

## [0.9.4]

### Changed
- Sharper skill guidance and descriptions
- Rotating hook prompts for KG reminders
- Removed unused scaffolding

## [0.9.3]

### Added
- Three-tier compaction: active → archived → orphaned
- Hidden skills architecture (kg-core, kg-capture, kg-recall, kg-maintain)
- UserPromptSubmit hook for ambient memory reminders

## [0.9.1]

### Changed
- Compaction tuning
- Storage safety: atomic writes with `.prev` rolling backup

## [0.9.0]

### Changed
- Consolidated MCP tools from 13 → 8
- Raised token limit to 4K
- `kg_read` absorbs session registration and single-node recall
