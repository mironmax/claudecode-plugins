# 08 — Visual editor: readable graphs and a server-owned project list

## Goal

Make the visual editor useful on a mature graph, and make its project list
come from the memory server instead of Claude Code's conversation history:

- **Readable by default.** A graph of several hundred nodes should open as a
  picture of what is live, with the rest one toggle away.
- **Honest counts.** What the header counts and what the canvas draws should
  agree, or say why not.
- **One source of projects.** The list shows the memory projects the server
  stores, for every harness, respecting a custom storage root.

## Why

A mature project graph observed in real use held about 550 nodes: 7% active,
25% archived and 68% orphaned (archived and dropped from the default read,
still searchable). The editor draws all of them with every edge label, which
is an unreadable grey mass at any zoom; the header counted 739 edges while the
canvas drew 711, with no explanation. The live cluster and the one hub that
mattered were visible only to someone who already knew where to look.

The project list is built from Claude Code's `~/.claude/projects/` history.
That misses projects used only from Codex, shows folders that have no graph,
lists evaluation runs as projects, can show one graph twice, and ignores
`KG_STORAGE_ROOT`: the editor hardcodes `~/.knowledge-graph` while the server
honours the variable.

## Where things are

- `knowledge-graph/visual-editor/backend/project_discovery.py` —
  `discover_projects()` walks `~/.claude/projects/`, decodes folder names or
  reads `cwd` from session files, then looks up
  `STORAGE_ROOT / "projects" / slug / "graph.json"` (hardcoded root).
- `knowledge-graph/visual-editor/backend/server.py` — `GET /api/projects`
  returns that list; other routes proxy the memory server's REST API.
- `knowledge-graph/visual-editor/frontend/static/js/app.js` — d3 force
  layout (`d3.forceSimulation`, around line 827), node and label classes for
  archived/orphaned (around lines 890–925), node detail panel.
- `knowledge-graph/server/mcp_http/rest.py` — the memory server's REST API
  (`create_rest_api`); `GET /api/maintenance_debt` already surveys every
  graph on disk without loading it (`core/debt.py:survey_debt`) and is the
  model for a read-only listing.
- `knowledge-graph/server/core/persistence.py` — project graphs are stamped
  with `_meta.project_path` on save. Older graphs may lack it, and the folder
  it names may no longer exist.
- `knowledge-graph/server/core/constants.py` — `get_storage_root()`,
  `project_slug()`, `project_graph_path()`. Note: `project_graph_path` may
  migrate a graph when it detects a rename, so a listing must not call it.

## What to build

1. **Server endpoint** `GET /api/projects` on the memory server, read-only:
   one row per stored project graph under `get_storage_root()/projects/*/`:
   slug, `project_path` from `_meta` (or null), whether that folder exists,
   node counts per tier (active / archived / orphaned), edge count, and the
   graph file's modification time as last-used. Read metadata without
   creating, loading into the store, migrating or rewriting any graph.
   Directories without a `graph.json` (counter files only) are not projects.
2. **Editor list from the server.** `discover_projects()` calls the endpoint
   instead of scanning Claude history; conversation counts go (they depended
   on Claude transcripts). Deduplicate by slug. Keep graphs whose folder is
   gone or whose `_meta` lacks a path, shown as such; never guess a path
   from a slug. Evaluation-run graphs (by their storage location or a marker
   the eval harness writes, whichever exists; check `server/eval/`) group
   under one collapsed entry.
3. **Default view.** Active nodes plus their one-hop neighbours; orphaned
   nodes hidden behind a toggle, archived shown dimmed. Edge labels on hover
   only. Node radius by degree (all non-orphaned edges), so hubs read as
   hubs. Keep the existing full view reachable.
4. **Edge-count honesty.** When edges are not drawn because an endpoint is
   missing or lives in the other graph, the header says so (e.g. "711 drawn,
   28 dangling").

## Constraints

- Showing is read-only: rendering, toggles and listing must never promote,
  archive or otherwise write a node. Existing explicit actions (recall,
  edit) keep their behaviour.
- The editor must still work against a server that lacks the new endpoint
  (older server): fall back to the current discovery, once, with a log line.
- No new frontend framework; d3 stays.
- Self-contained tests for the endpoint (tier counts, missing `_meta`, gone
  folder, no-graph directory skipped, custom storage root, nothing written);
  suite green; CHANGELOG entry.

## Done when

On a storage root holding a large mostly-orphaned graph, a graph without
`_meta.project_path`, a graph whose folder was removed and a counters-only
directory: the endpoint lists exactly the three graphs with correct tier
counts and writes nothing; the editor opens the large graph showing active
nodes and neighbours with hubs visibly larger; the edge header accounts for
every stored edge.
