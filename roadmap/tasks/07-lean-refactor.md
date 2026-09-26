# 07 — Lean refactor: findings

## Goal

Make `knowledge-graph/` smaller and easier to change without changing what
it does. Each finding names a duplication, an unneeded special case, or
wasted work, and gives a simpler form that behaves the same. This is a
findings list, not a patch: no code has been changed yet.

## How the list was made

The whole `knowledge-graph/` tree was reviewed, not a diff. Two review
skills drove it, and each reviewer read the cited code:

- `/code-review` with its reuse, simplification and efficiency checks.
- `/simplify`, split into four parallel angles: **reuse** (code that
  re-implements an existing helper), **simplification** (derivable state,
  copy-paste, dead code), **efficiency** (repeated I/O and recomputation),
  and **altitude** (special cases that a general change further down
  would remove, and module boundaries).

Duplicates across reviewers are merged below. Spot checks confirmed the
central claims: the helper names exist, the repeated `except` ladders,
the six `os.replace`/`.replace` temp-file writes, the two
`_DATED_ID_RE` copies, the dead `LEGACY_*` names, and the `tier ==
"pass"` branches. The line numbers are from commit `f17349a`.

Bug hunting was out of scope. Where a finding also exposes drift between
copies (a fix that only reached one of them), it says so, because that
drift is the real cost of the duplication.

## Suggested order

Start with the mechanical, low-risk items (1–4). They shrink the code
before the structural moves (5–8), so there is less to carry around
during those. Run the full test suite after each item. Task 06
(continuous integration) makes that cheap, and item 9 makes the suite
itself easier to extend.

---

## 1. Use the helpers that already exist

**Node-state predicates.** `core/utils.py:83-93` defines `is_archived`,
`is_orphaned` and `is_active`. The inline form
`not n.get("_archived") and "_orphaned_ts" not in n` is still written out
about ten times, in `core/scorer.py:20`, `core/chores.py:130`,
`core/compactor.py:58`, `core/lift.py:93`, `core/anchors.py:92`,
`mcp_streamable_server.py:466` and `mcp_http/store.py:527,1210`.
`NodeScorer._is_active` and `chores._active` are private copies of the
helper. The "archived but not orphaned" test appears five more times, in
`compactor.py:104,185,203,268` and `scorer.py:106`.
→ Import the helpers and add one `is_dormant(node)`. `store.py:527,1210`
write out `is_archived(n) or "_orphaned_ts" in n`, which is simply
`not is_active(n)`.

**Hub and slug detection.** `store._hub_mention` (`store.py:791-822`)
re-implements `core/debt.smeared_terms` (`debt.py:49,81-113`): the
slug-token skip, the hub test, and the same `_DATED_ID_RE` regex, which
is defined in both files. `utils.node_id_has_date` already does the date
check. The slug itself is derived three ways: by parsing the graph key
(`store.py:803-806`), from `project_graph_path(...).parent.name`
(`store.py:1697-1701`), and by the canonical `constants.project_slug`.
→ Move `hub_candidates(term, ids)` into `debt.py` and call it from the
store, using `node_id_has_date` and `project_slug`.

**Tool-event reads.** The `tool_events.json` path and its `last_ts`
extraction are re-implemented in `store.py:1690-1696` and
`debt.py:289-297`, next to the existing `ambient._events_path` and
`_load_events`. → Add one `tool_event_timestamps(project_dir)` beside
them. There are also two touch parsers: `file_recall.normalize_touch`
(`file_recall.py:42`) and `anchors.parse_touch` (`anchors.py:42`).
→ Build the first on the second.

## 2. One atomic JSON write

The "write to .tmp, then replace" sequence is hand-rolled six times:
`core/persistence.py:126-150` and `:198-209`, `session_manager.py:366-383`,
`chore_dispatch.py:194-202`, `ambient.py:486-489`, and
`constants.py:520-526`. The copies have drifted. Only some of them fsync,
only some clean up the `.tmp` file on failure, and
`rewrite_edge_refs_on_disk` also repeats the edge re-keying from
`_rewrite_edge_refs`.
→ Add `write_json_atomic(path, data, *, indent=2, backup=False)` in
`persistence.py`, next to `append_jsonl`, and call it from all six sites.

## 3. Remove dead and redundant code

These were checked with a grep over the whole repo, including tests,
hooks, skills and the JS frontend:

- `LEGACY_USER_PATH`, `LEGACY_PROJECT_KNOWLEDGE_PATH` and
  `LEGACY_SESSIONS_PATH` (`constants.py:457-459`), and
  `NodeNotArchivedError` (`exceptions.py:24`). Nothing uses them except
  the definitions and re-exports.
- `MultiProjectGraphStore.reload_project_graph` (`store.py:402`) and
  `HTTPSessionManager.get_start_ts` (`session_manager.py:155`) are never
  called.
- `core/__init__.py` keeps an `__all__` by hand. It is stale: it lists
  the dead `LEGACY_*` names and leaves out names that `store.py:12`
  imports from `core`. Those imports work only because of
  `from .constants import *`. `store.py` is the only
  `from core import (...)` caller. → Import from the submodules and empty
  the `__init__`.
- `self.dirty[gk] = True` is set just before `_write_through(gk)` at
  about ten sites (`store.py:557, 693, 868, 922, 1001, 1129, 1649,
  1735, …`). `_write_through` saves and then resets `dirty` to `False`,
  so those assignments do nothing. Confirm this site by site before
  deleting.
- `find_node_level` (`store.py:1146`) and `find_edge_level`
  (`store.py:1083`) are the same function with a different membership
  test. → Merge them into one `_find_level(contains)`.
- `chores.render_lessons` (`chores.py:455`) checks `if not lessons` and
  then `if not lines`. The second check covers both.

## 4. Replace repeated `try`/`except` with exception handlers and a dispatch table

**MCP tools.** `mcp_streamable_server.py:385-702` is one if/elif chain,
about 320 lines long, over eleven tools.
- Eight branches repeat
  `sid = arguments["session_id"]; session_manager.increment_ops(sid)`
  and the `TextContent` wrapping.
- The chain ends with separate `except` arms for `NodeNotFoundError`,
  `SessionNotFoundError` and `KGError` (`:688-692`) that return the same
  text. The first two are subclasses of `KGError`.
- The `kg_read` branch alone runs about 95 lines, and
  `format_sync_compact` is nested inside the handler (`:648`).

→ Use a `TOOLS = {name: handler}` table of small functions that each
return a `str`. One wrapper calls `increment_ops`, wraps the result, and
catches `KGError` once. The inline formatters move to `read_format.py`.

**REST.** `rest.py:301-460` repeats the same ladder in six handlers:
`NodeNotFoundError` → 404, `KGError`/`ValueError` → 400, anything else →
logged 500. → Register one `exception_handler` per exception class on
the app. Each handler body then becomes `return store.x(...)`.

**Visual-editor proxy.** `visual-editor/backend/server.py:123-327` has
six proxy routes. Each one repeats the `httpx.AsyncClient` setup, the
`session_id`/`project_path` params, `_raise_upstream`, and a catch-all
block. Its `NodeCreate`/`EdgeCreate` models copy rest.py's request
models, and the WebSocket proxy hardcodes `ws://127.0.0.1:8765` instead
of deriving it from `MCP_SERVER_URL` (`:345`). → Use one
`_proxy(method, path, *, params=None, json=None)` with a single
module-level `AsyncClient`, which also gives connection reuse.

## 5. Give the store an API so callers stop reaching into it

`chore_dispatch.py:306-497`, `file_recall.py:308-360` and
`ambient.py:523` take `store.lock` themselves. They then read
`store.graphs`, `store._progress`, `store._versions` and
`store.write_gen`, and call `store._ensure_project_loaded` and even
`store.scorer._recency`. So the lock rule and the "load the project graph
first" rule are copied into every caller.
- `file_recall._rank` re-does `scores_for_read` (`store.py:571`) by hand.
- `chore_dispatch._graph_debt` recomputes `store.maintenance_debt` but
  leaves out tool-event activity, so the dispatcher and `kg_read` can
  disagree about a graph's debt.
- `ambient._decide_nudge` (`ambient.py:655`) calls `store.read_graphs()`
  and discards the result, only to get the project graph loaded.

→ Add one `store.graph_view(graph_key, project_root=None)` that loads the
graph and returns a snapshot of nodes, edges, versions, progress and
scores, and move the touches index from `file_recall._INDEX` into the
store as `nodes_touching(paths)`. After that, callers hold no lock and
touch no underscore attributes, and `_decide_nudge` can use
`_ensure_project_loaded`.

The "find session by Claude session id, else by project path" lookup is
copy-pasted three times in `ambient.py` (around `:237`), and belongs in
`session_manager`.

The three near-identical graph loaders (`store.py:~230-301`, one each for
user, project and maintain) have also drifted: the maintain copy never
bumps `write_gen` and never repairs malformed gists on load. → Use one
`_load_graph(key, path)`.

## 6. Split `store.py` along its real seams

`store.py` is 1,909 lines. About 330 of them are pure logic over plain
dicts that can only be tested through a store. That covers `_stem`,
`_term_stream` and `search_terms` (`:60-115`), `search` with its nested
`search_graph_rrf`, `build_record` and `find_gist` (`:1313-1543`), and
the BFS in `_connection_paths` (`:1545-1600`).
- `_near_duplicate` (`:717`) is a second copy of the search scoring
  pipeline, and has already started to differ from it.
- The bigram loop in search (`:1427`) recounts occurrences that the
  unigram pass just computed.

→ Create `core/search.py` with `search_terms`, `rrf_scores(nodes,
unigrams, bigrams)` and `connection_paths(graphs, top_ids)`. Both
`search` and `_near_duplicate` call it, and `store.search` shrinks to
about 30 lines: resolve keys, take a snapshot under the lock, call core,
build records.

In the same spirit, `core/constants.py:438-636` holds storage layout and
migration logic with file I/O (`_load_aliases`, `_save_aliases`,
`project_graph_path` with its slug migration, `_migrate_slug`). → Move it
to `core/paths.py` so `constants.py` holds only tunables. The
sessions.json fallback inside `project_graph_path` (`:580-603`) compares
`Path(sp).resolve().name` with `Path(sp).name`. Since `register()` stores
real paths, the two are almost always equal. Check whether the branch
still fires, and drop it if not.

## 7. Resolve graph scope once, at the transport edge

Every store operation takes both `session_id` and `project_path`
(`store.py:408, 637, 841, 888, 938, 1107, 1170`). `_resolve_graph_key`
(`:327`) documents a precedence rule that exists only because the visual
editor's WebSocket session has no project registered.
- `_get_graph_key`, the two `find_*_level` functions, `_broadcast`
  (`:357`) and `scores_for_read` each repeat the session-to-project
  lookup.
- `_broadcast` never receives `project_path`, so editor writes to a
  project graph are broadcast with no project attached.

→ Have the editor call the existing `/api/sessions/register?project_path=`
(`rest.py:88`) when it picks a project. Store methods then take a
resolved scope and never call `session_manager`. That removes one
parameter from about seven signatures and removes the precedence special
case. It also cuts the store's dependency on `HTTPSessionManager`.

The two read paths share one sequence: the full read in `call_tool`
(`mcp_streamable_server.py:451-481`) and `rest_session_bootstrap`
(`rest.py:161-178`) both do `read_graphs` → `scores_for_read` →
`maintenance_debt` → render → `mark_seen`/`mark_full_read`. The bootstrap
route also holds about 60 lines of session-resume logic, none of which
is HTTP. → Move both into a small service module with `full_read(sid)`
and `bootstrap(...)`, so each transport makes a one-line call.

## 8. Replace per-tier and per-kind branches with tables

**Tiers.** `chore_dispatch.py` branches on `tier == "pass"` at `:145,
610, 638-640, 651, 685`, because a pass payload is a dict and a chore
payload is a `Chore` object. For example, `:638-640` reads
`payload["level"] if tier == "pass" else payload.level` three times.
→ Give both tiers one `Job` shape, plus a
`TIERS = {"chore": TierSpec(...), "pass": TierSpec(...)}` table covering
the cap key, gauge gate, timeout, settings file, prompt builder and state
key.

**Kinds.** Special cases for the `lift` and `anchor` chore kinds are
spread over six places in two files: `chores.py:313-326`, `:418-440`,
`:508`, `:522` and `chore_dispatch.py:427`, `:666-677`. Meanwhile
`REASONS`, `_KIND_INSTRUCTIONS` and `CHORE_TARGETS` are already per-kind
dicts. → Use one `KINDS = {kind: KindSpec(context, lines, rules,
record_extras)}` table with defaults, so each branch becomes a lookup.

## 9. Tests and scripts

**Test harness.** 24 of the 25 `test_*.py` files in `server/tests/` define their own
`check()`/pass/fail counters. Most set `KG_STORAGE_ROOT` to a temp dir,
insert into `sys.path`, and build a store the same way
(`GraphConfig(save_interval=9999)`, `HTTPSessionManager()`,
`MultiProjectGraphStore(...)`). Nineteen files are named by release
(`test_v09NN.py`), so finding the tests for a feature means reading the
changelog. → Add a `tests/_harness.py` with `check`, `summary_exit`,
`tmp_storage()` and `fresh_store()`, and rename or merge files by
feature. This fits task 06.

**Shell.** `visual-editor/manage_visual.sh` is an older fork of
`server/manage_server.sh`. It copies `ensure_venv`, the PID-file
start/stop/status logic and the `case` dispatch, but lacks the
requirements-hash marker. In `manage_server.sh`, `wait_healthy` (`:138`)
and `wait_port_free` (`:152`) are the same polling loop with the
condition negated. `hooks/kg-remind.sh:27-34` and
`hooks/kg-tool-event.sh:18-27` share the same POST-and-relay step, and
all three hooks repeat the `KG_CHORE` guard, the stdin read and the
host/port setup. → Add a shared `lib.sh` (`ensure_venv`, pidfile
start/stop, `wait_until <cmd>`) and a `hooks/_kg_common.sh`
(guard, input, `kg_post_relay <endpoint>`).

**Frontend.** In `visual-editor/frontend/static/js/app.js`:
- `transformGraphData` (`:364-407`) has one copy-pasted block per level
  for nodes and again for edges. → Loop over `['user','project']`.
- The `POST /api/nodes` save is written twice (`:502`, `:715`). → Use
  one `saveNode(...)`.
- `deleteNode` and `recallNode` bypass the existing `errDetail` helper.

## 10. Wasted work whose fix is also less code

Only fixes that do not add complexity are listed here.

**Store and REST:**
- **Sync handlers declared `async`.** `rest.py` `/api/tool_event`,
  `/api/prompt_context`, `/api/session_bootstrap`,
  `/api/maintenance_debt` and `/api/graph/read` are `async def` but only
  do blocking work (`store.lock`, file reads, `survey_debt`). So while
  maintenance holds the lock, one hook call stalls the event loop for
  every client. → Delete the `async` so FastAPI runs them in its
  threadpool. Keep `async` on routes that broadcast, because
  `_broadcast` needs the running loop.
- **One save per id.** `mark_useful` (`store.py:558`) saves the whole
  graph once per liked id, and batch `kg_read(ids=[...])` saves once per
  promoted archived id through `read_node` (`:1239`). → Mark dirty
  inside the loop and save once after it.
- **Idle maintenance and session saves.** The 30-second maintenance pass
  (`store.py:1880`) recompacts graphs nothing has written to, and
  `save_sessions` (`session_manager.py:366`) rewrites `sessions.json`
  every 30s even when nothing changed. → Skip graphs whose `write_gen`
  hasn't moved, and skip a sessions write whose content equals the last
  one.
- **Repeated edge scans.** `read_node` (`:1226`, `:1253`),
  `get_sync_diff` (`:1278`) and `_prune_orphans` (`:1846`) each rescan
  all edges once per item. → Build the per-node edge list once.
  `_prune_orphans` can call `delete_node`'s edge removal instead of
  repeating it.

**Other paths:**
- **Budget fitting.** `read_format._fit_to_budget` (`:168`) re-renders
  the whole output after each item it drops, which makes trimming
  quadratic, and `compactor.py:85,115` re-estimates the whole graph for
  each node it archives. → Use the delta accounting that
  `refill_if_room` (`compactor.py:201-221`) already does.
- **Tool events.** Every tool-event hook reloads and rewrites the whole
  `tool_events.json` under one lock shared by all projects
  (`ambient.py:612`).
- **Visual editor.** It always sends `reload=true` (`server.py:143`), so
  every read re-parses the graph files from disk. Each WebSocket event
  triggers a full `loadGraph()` (`app.js:155`), and
  `project_discovery.py` hard-codes the storage layout (ignoring
  `KG_STORAGE_ROOT` and slug aliases) and globs `*.jsonl` twice per
  project. → Drop the forced reload, coalesce reloads behind one timer,
  and take layout and counts from `core.constants` or the server.

## Out of scope

Behavior changes, new features, and bug fixes other than the drift that
item 5 notes. Any refactor that changes what a tool returns, or when a
file is written, belongs in its own brief.
