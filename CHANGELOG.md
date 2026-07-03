# Changelog

All notable changes to this project are documented here.

## [0.9.20] - 2026-07-03

### Changed
Two readability tweaks from first-person reading experience of the node-centric format:
- **The archived list renders alphabetically.** It is id-only, so score ordering was invisible to the reader anyway — alphabetical clusters related name prefixes (`kg-*`, `night-ops-*`) and makes a long list scannable. Score still governs what the degradation ladder *drops*; only the display order changed.
- **Write-time nudge for oversized gists.** Gists past 300 chars read as walls in the full-graph render and break the scan rhythm. `kg_put_node` now appends a note to its response when a gist exceeds the limit — the write is never rejected (long gists are sometimes right), but the writer is nudged at the moment the fix is cheapest. The maintain skill's oversized-gist pass handles existing stock; this stems the inflow.

## [0.9.19] - 2026-07-03

### Changed
- **The session-start preload is a compact core (≤10K chars) — and the loud `kg_read` never repeats it.** Hook `additionalContext` rides a much smaller inline window than tool results: measured on Claude Code 2.1.199, hook output stays inline up to ~10,100 chars and spills to a persisted file (2KB preview) at ~10,150 — so v0.9.17's full-render preload silently landed in a file on any real graph. The bootstrap now renders under a hard `BOOTSTRAP_CHAR_BUDGET` (10,000, instruction header included — render == charge covers every character the hook emits) with an extended degradation ladder: archived anchors first, then edge citations, then whole active gists, lowest-scored first — the hubs stay. The two channels then split the work: the silent preload gives the model its top-scored orientation before the first tool call; `kg_read(session_id)` renders the full graph with preloaded gists collapsed to id-only `(preloaded)` anchors, spending its 40K budget on everything the compact core had to drop. Explicit `ids=[...]` reads are never deduped.
- **Memory loading is no longer invisible.** The SessionStart hook emits a `systemMessage` one-liner (active node counts, gists inline, session id) so the user sees memory load instead of inferring it from a missing tool call.
- **The prompt-time reminder is stage-aware.** `kg-remind.sh` now weights its nudge pool by session depth (transcript size): early prompts point at recall, mid-session at capture, deep sessions at maintenance and wrap-up (`kg_useful`). New reminder: subagents never receive the preload — the dispatching session puts the relevant gists or `kg_*` instructions in their prompts (measured: SessionStart does not fire for Agent-tool subagents).

### Added
- Tests: `tests/test_v0919.py` (30 assertions: bootstrap budget cap and hub-first selection, read dedup and freed budget, preloaded-set session tracking with save/load round-trip).

## [0.9.18] - 2026-07-03

### Added
- **`kg_useful` — explicit usefulness endorsement.** At session wrap-up, the agent marks up to 5 nodes that *actually helped*, judged against real results rather than mid-flight promise. One vote per node per session; the ledger lives on the session, decaying timestamps (90-day half-life) on the node. Reads deliberately do **not** feed this signal: a well-formed gist is self-sufficient, so counting reads would reward the weakest gists. A like is not a content write — versions, recency, and sync state are untouched.
- **Usefulness in archival scoring.** The score blend is now 0.25 recency / 0.40 connectedness / 0.35 usefulness (percentile ranks). Percentile assignment became tie-aware (equal raw values share the average rank), which the usefulness column requires — with most nodes at zero likes, index-order percentiles would have spread identical values across the whole range arbitrarily; an all-zero column now collapses to a uniform 0.5 and distorts nothing.
- Tests: `tests/test_v0918.py` (22 assertions: like budget/ledger/decay, scorer blend and tie-awareness, namespace helpers, namespace meta).

### Changed
- **Namespace seam (internal, no behavior change).** Graph keys are constructed and inspected only through `core.constants` helpers (`project_namespace`, `is_project_namespace`, `namespace_kind`) instead of scattered string literals, and every graph file now carries `_meta.namespace = {kind, owner}`. This is the storage-level seam for future namespace kinds (role/org graphs, multi-user owners) — they slot in without a migration.

## [0.9.17] - 2026-07-03

### Added
- **Memory preloaded at turn 1.** The SessionStart hook now injects the rendered knowledge graph as `additionalContext` when the server is healthy (`/api/session_bootstrap`: registers the session, returns the same text kg_read would produce — one renderer, two delivery channels). The session starts with memory already in context: zero tool calls, a full model round-trip saved. Silent miss while the server bootstraps; classic kg_read remains the fallback.
- **Search v2.** `kg_search` returns a focused, capped answer instead of an unbounded JSON dump: top-5 hits with full treatment, connections *between* the hits (union of pairwise shortest paths — connector nodes as id+gist plus the path edges), and remaining matches as one-liners, all under a 10K-char ceiling with a value-ordered trim ladder. **Session-aware dedup:** the server tracks which gists each session has already been shown (preload, reads, prior searches); a seen hit renders as a one-line gist reminder — notes are never re-dumped, they stay one explicit node read away. Gists + edges are the working currency; notes are on-demand depth.

### Changed
- **kg_read full-graph format is node-centric.** Nodes render in cluster order (connected communities contiguous, highest-degree hub first) with their relationships indented beneath them — a cluster reads as one coherent knowledge paragraph instead of three flat sections joined by id. Each edge is cited exactly once, under its first-rendered endpoint (`→`/`←` show direction); doubling citations would tax the character budget that gists need. A node's complete neighbourhood is always visible in single-node reads. The estimator measures the actual planned render (headers, node lines, citations, anchors), so render == charge stays exact by construction.

## [0.9.16] - 2026-07-03

### Changed
- **Budgets are now exact rendered characters — kg_read is guaranteed to land inline.** The old budget was estimated tokens with flat per-item charges (`BASE_NODE_TOKENS`, `TOKENS_PER_EDGE`, `ARCHIVED_ID_TOKENS`, …) that drifted from real rendered sizes — long kebab ids and long relationship names rendered far past their charge, so on some projects the combined kg_read output overflowed the MCP client's inline tool-result limit and landed in a persisted file the model only sees a 2KB preview of (the root cause behind "read the overflow file before working"). The estimator now measures the *exact strings* kg_read renders (`core/render.py` is the single source of truth for line rendering; the estimator charges `len(line)+1`), the per-level budget is `MAX_CHARS_PER_LEVEL` (17,500), and a render-time **degradation ladder** enforces a hard `READ_CHAR_BUDGET` (40,000) on the combined output for graphs the compactor hasn't maintained yet: lowest-scored archived anchors are hidden first (with a count and a kg_search pointer), then lowest-value live edges — active gists are never dropped. The budget is deliberately **not configurable**: the `KG_MAX_TOKENS` env override is gone; the inline guarantee is an invariant, not a tuning exercise.
- **Node reads are compact text, not raw JSON.** `kg_read(id)` used to dump the node as indented JSON including internal fields (`_last_read_ts`, …). It now renders gist / notes / touches plus — new — the node's own edges, giving crumb-following its next hops for free.

### Added
- **Batch node reads:** `kg_read` accepts `ids: [...]` to read several nodes in one call — sequential crumb-following round-trips collapse into one.
- **Session reuse:** `kg_read` accepts `session_id`; a valid one is reused instead of registering a fresh session. Previously *every* read carrying `cwd` (which the schema required) minted a new session and fsynced `sessions.json` — four crumb reads = four sessions. `cwd` is now only required on the true first call.
- Tests: `tests/test_v0916.py` (33 assertions: exact-char render==charge, ladder ordering/floor/guarantee, compact node format, session reuse, edge-cleanup preservation).

### Fixed
- **Cross-level and artifact edges were silently deleted on every server restart.** `_clean_orphaned_edges` removed any edge whose endpoint wasn't a node in the same graph — which is exactly what a project→user cross-level edge or a file-path (artifact) edge looks like locally. Cleanup now keeps endpoints that are artifact paths (`/` or `~`) or resolvable in another loaded level; only true dangling references (deleted nodes) are removed. Doctrine settled alongside: cross-level edges belong in the *project* graph pointing up to user-level nodes.

## [0.9.15] - 2026-07-02

### Fixed
- **Storage git auto-commit never fired in normal operation.** Commits of `~/.knowledge-graph` only happened in `manage_server.sh` on managed `stop`/`restart` — but in real life the server is launched by the SessionStart hook and dies with machine shutdown, so a managed stop (and thus a commit) never ran; on one machine the last `Auto-save` commit was three weeks stale despite daily use, and a crash during that window would have lost the entire uncommitted history. The server now commits **periodically from within the Python process** (`core/autocommit.py`, daemon thread on the same Event-wait pattern as the store's saver thread), so history accumulates no matter how the server is started or killed. Every `KG_AUTOCOMMIT_INTERVAL` seconds (default 900 = 15 min; `0` disables) it commits pending changes with the existing `Auto-save YYYY-MM-DD HH:MM` message convention — only when the tree is actually dirty (no empty commits), silent no-op when the storage root has no `.git` (a later `git init` is picked up without a restart), and git failures are logged but never fatal. A final best-effort commit also runs on graceful shutdown (SIGTERM/SIGINT), ordered after the store's disk flush so it captures the final state. `manage_server.sh commit_storage()` stays as-is for `kg-memory commit` and the managed stop paths.

### Added
- `KG_AUTOCOMMIT_INTERVAL` environment variable (documented in README configuration table and as a commented example in `server/memory-mcp.service`).
- Tests: `tests/test_autocommit.py` (8 tests on throwaway git repos — dirty/untracked commit, clean-tree no-op, no-`.git` no-op, interval parsing, disabled mode, idempotent shutdown commit, periodic loop firing). Runs standalone like the other suites or via pytest.

### Changed
- README "External backups" git section rewritten: `git init` in `~/.knowledge-graph` is now a one-time setup — the server handles the periodic commits itself. ARCHITECTURE.md storage layer documents the design.

## [0.9.14] - 2026-06-11

### Fixed
- **First run actually works now.** There was no venv bootstrap anywhere: a fresh install had no Python environment and no documented step to create one, so `kg-memory start` failed with `venv/bin/python: No such file` and the plugin could not complete first run on any machine where the venv hadn't been built by hand. Worse, plugin updates install into a fresh version-stamped directory, so even a hand-built venv vanished on every update. `manage_server.sh` and `manage_visual.sh` now build the venv automatically on `start` when it's missing or incomplete (one-time ~1 min, with a marker file so a half-finished `pip install` is retried rather than trusted).

### Added
- **Server auto-start.** A bundled SessionStart hook health-checks the memory server on every session and launches it in the background when down — combined with the venv bootstrap, "install → restart → done" is now literally true. The hook only ever *starts* the server; it never stops or restarts a running one. When a session connected while the server was down, the hook tells Claude to verify health and ask the user for the one step only they can do: `/mcp` → `plugin:knowledge-graph:kg` → **Reconnect**.

### Changed
- `kg-core` skill guidance updated: connection-refused is now "warming up — retry, then /mcp Reconnect", not "ask the user to start the server".
- README rewritten around the real first-run experience: honest "Done." claim, Python 3.10+ requirement stated up front, and a "Your first five minutes" section (what Claude says, how to seed the graph with `/kg-extract` and `/kg-scout`, what to ask next session). Wiki Installation/Server-Management/Home updated to match.

## [0.9.13] - 2026-06-11

### Fixed
- **Visual editor writes to project graphs.** Creating a node, editing gist/notes/touches inline, and creating an edge on a *project* graph all 500'd: the editor's session has no project path registered, and unlike the read/recall/delete paths (fixed in 0.9.12), the write paths never accepted `project_path`. Now `POST /api/nodes` and `POST /api/edges` take `project_path`, the editor sends it, and all node/edge operations share one graph-addressing helper in the store (`_resolve_graph_key`).
- **REST `DELETE /api/edges/...` always failed** — the endpoint passed its arguments to the store positionally in the wrong order (`level` landed in `from_ref`, `rel` in `level`), so every call errored. Latent because the editor UI has no delete-edge action yet; fixed and covered by tests.
- **Refill dead band: graphs settled permanently with most knowledge stranded archived.** Refill only triggered below 0.6×budget but filled to 0.8×, so any graph sitting between 0.6 and 0.8 (where compacted graphs naturally land) never refilled — observed live: a user graph at 33 active / 128 archived with ~560 tokens of unused headroom and refill never firing. Refill now acts whenever the graph is below the 0.8 fill ceiling (single threshold; no-thrash is preserved by the ceiling sitting under the 1.0 archive threshold, plus skipping refill on any tick that just archived).
- **Refill blocker: one oversized gist stranded everything behind it.** A top-scored candidate too large for the remaining headroom used to stop the whole pass ("reconsidered next time" — but the estimate only grows, so it never fit later either). Non-fitting candidates are now skipped and smaller ones behind them promote. The fit check uses an exact O(degree) promotion delta from the adjacency index, so skipping is cheap even on dense graphs.
- **Compaction token bookkeeping.** Archiving re-measures the graph instead of subtracting the node cost (which ignored the remaining anchor cost and edges going dead); the resurrection pass now re-measures too and reverts a swap that would push the graph back over budget.
- Server shutdown ran the store flush twice, each waiting up to 5s for the sleeping maintenance thread (~10s exit latency). `shutdown()` is now idempotent and wakes the thread via an event — exit is immediate.
- `kg_search` iterated graph dicts without the store lock — racing the background maintenance thread (archival, pruning) could blow up mid-scan. Search moved into the store behind the lock; `read_graphs` now returns snapshot copies instead of live dict references for the same reason.

### Security
- **Healer ReDoS, complete fix (CodeQL alert 12).** The 0.9.12 boundary-lookahead fix killed one witness family (`<notes` glued to junk) but a gist full of *viable* opener starts (`<notes <notes …`, no `>` anywhere) still made every start scan the unbounded `[^>]*` tail to end-of-string — measured quadratic (7.7s at 140KB; healing runs on every write and load). The attribute tail is now bounded (`[^>]{0,256}`), making the scan linear (100ms on the same witness). Regression-tested with both witness families.
- **WebSocket Origin validation.** Browsers do not apply CORS to WebSocket upgrades, so any web page could previously open `ws://127.0.0.1:8765/ws` (or the editor's `:3000/ws` proxy) and silently receive every graph broadcast — node contents included. Upgrades with a non-local `Origin` are now rejected; absent `Origin` (non-browser clients) still works.
- **Host-header validation (anti DNS-rebinding)** on every HTTP/WebSocket request to both servers: requests not addressed to `localhost`/`127.0.0.1`/`::1` (or the explicitly configured bind host) are rejected with `421`, closing the rebinding route around CORS for the REST *and* MCP endpoints.
- **Server-side identifier validation.** Node IDs, edge endpoints, and rel types are validated to a safe character set at the write boundary (REST and MCP alike) — markup can no longer enter the store and reach surfaces that render it. Existing graphs are unaffected (scanned: all existing IDs already conform).
- **Visual editor XSS hardening:** `escapeHtml` now escapes quotes (attribute contexts); the edit-modal title escapes the node ID; node IDs are no longer interpolated into inline `onclick` JS (entity-escaping cannot make that context safe — replaced with data attributes + listeners).
- SECURITY.md now states the trust boundary explicitly (local processes; session IDs are namespacing, not auth) and documents the new guards.

### Changed
- REST API construction extracted from the server entrypoint into `mcp_http/rest.py`; RRF search logic moved from the MCP tool handler into `MultiProjectGraphStore.search()`. Both moves make the wiring layer testable in-process.
- REST write/read endpoints return `400` with a real message on validation errors (was a generic `500`); the editor proxy forwards upstream status + detail, and editor toasts show it.
- `GraphPersistence` takes `project_path` as a constructor parameter (was injected post-hoc as a private attribute).
- Tests: `tests/test_v0912.py` renamed to `tests/test_core.py` (57 assertions, including new refill skip-not-break and validation coverage); new `tests/test_http.py` exercises every REST endpoint in-process with the editor's exact addressing shape, plus the WebSocket Origin policy (28 assertions). Both run with the project venv, no pytest:
  `./venv/bin/python tests/test_core.py && ./venv/bin/python tests/test_http.py`

## [0.9.12] - 2026-06-01

### Added
- **Reverse refill (self-healing active set).** Compaction previously only moved nodes *down* (active → archived) when over budget; nothing moved them back up except a manual `kg_read(id)`, so a graph could sit far below budget with valuable knowledge needlessly collapsed — especially now that the edge-accounting change frees real headroom. A new pass (`Compactor.refill_if_room`) promotes the highest-scored archived nodes back to active to use spare budget. A hysteresis band prevents thrashing: refill only **triggers** below `REFILL_TRIGGER_RATIO` (0.6×max) and only **fills up to** `COMPACTION_TARGET_RATIO` (0.8×max), leaving a stable 0.6–1.0 dead zone where neither refill nor archiving acts. Runs as part of the existing compaction step (on writes and the periodic maintenance tick).
  - Refill **re-scores after each promotion** and connectedness now weights an edge to an archived neighbour at `ARCHIVED_EDGE_WEIGHT` (0.2) instead of 0. Together these fix a ratchet where a well-connected cluster that archived *together* could never be refilled — every member looked disconnected because all its neighbours were archived too. Now a dense archived hub floats up the refill order and, once promoted, makes its neighbours' edges live so the cluster is resurfaced as a unit within the same pass. Scoring builds an edge-adjacency index once per pass, so iterative re-scoring stays fast (sub-second even on large dense graphs).
- **Self-healing for malformed nodes.** A node is meant to arrive as separate fields — a short `gist` headline plus a `notes` list. Occasionally a client serialized the *whole* node (gist + notes + surrounding tool-call markup) into the single `gist` string and left `notes` empty. Because every full-graph `kg_read` renders `id + gist`, those oversized gists (often 20–30× their intended size) dominated the token budget. The server now repairs this automatically in two places, both driven by one function (`core.healer.heal_node_fields`): on **write** (`put_node` sanitizes before storing, so corruption never lands) and on **load** (each graph is healed when first read from disk, and the repair is written back). Healing splits the real headline out and recovers the embedded `notes`/`touches` into their proper fields; it is idempotent and never overwrites caller-supplied data, so already-clean graphs are untouched.
  - **What you'll see on first upgrade:** the server logs a `WARNING` per repaired node plus an `INFO` summary `Healed N corrupt node(s) on load` the first time it opens an affected graph, then rewrites the file. This is expected and one-time — subsequent loads find clean data and do nothing. Affected nodes shrink and their previously-lost notes reappear in `kg_read(id)` and the visual editor.
  - **Token impact:** on graphs that had accumulated these malformed nodes, active-graph cost dropped substantially (observed −30% to −70% per graph). If `kg_*` MCP calls were consuming an outsized share of your context, this is the likely cause and fix.
  - As with any data-touching change, **take a backup before upgrading** — see [Data and Backup](https://github.com/mironmax/claudecode-plugins/wiki/Data-and-Backup). The healed write keeps the usual `.prev` rolling backup, but a point-in-time snapshot is cheap insurance.

### Changed
- **Edges are "resurfacing strings": render == charge.** `kg_read` now shows — and the compaction budget now charges — an edge only when at least one endpoint is active (or is a file/artifact reference, which is always present). An edge between two archived nodes is a dangling thread you can't pull: it is suppressed from `kg_read` output and no longer counted against the active token budget. It reappears automatically the moment either endpoint is promoted, so nothing is lost. A single predicate, `core.utils.edge_is_live`, drives both the renderer (`format_graph_compact`) and the estimator (`TokenEstimator`), so visible output and budget can never drift apart.
  - Impact: on large graphs where most nodes are archived, the archived–archived edges were 85–97% of the edge count and dominated the token budget — starving the active set (e.g. a 117-node project showed only 1 active node) and bloating `kg_read` output toward the tool-result limit. With the fix, those graphs keep far more nodes active and produce much shorter output. Graphs with no archiving are byte-identical — no change.
  - No data migration: existing graph files are untouched; archived nodes and all their edges remain on disk, in the visual editor, and in `kg_search`. Compaction never *re-archives* retroactively — the cheaper edge accounting only means *fewer* nodes archive on future compactions, never more.
- `kg_read` `HEALTH:` line now reports active nodes and **live** edges (matching the visible sections), instead of raw on-disk totals — so `avg edges/node` is no longer skewed by hidden archived–archived edges. "Orphans" in the health line now means active nodes with no live edge (a genuinely useful reachability signal).

### Fixed
- Token estimator charged **all** edges (including orphan-endpoint edges that `kg_read` already suppressed) and counted archived nodes as free — two inconsistencies between what was rendered and what was budgeted. The estimator now charges active nodes (id+gist), archived nodes (a 5-token ID anchor), and live edges only — exactly what `kg_read` renders.
- Reconciled two conflicting active-token-budget defaults: `GraphConfig.max_tokens` was `5000` while the server env fallback was `4000`. Both now derive from a single `MAX_TOKENS` constant in `core/constants.py` (5000), still overridable via `KG_MAX_TOKENS`.
- The orphan-pass `ARCHIVED_ID_TOKENS` was a local literal with no shared source; it is now a single constant in `core/constants.py` shared with the estimator.

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
