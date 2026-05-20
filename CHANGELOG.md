# Changelog

All notable changes to this project are documented here.

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
