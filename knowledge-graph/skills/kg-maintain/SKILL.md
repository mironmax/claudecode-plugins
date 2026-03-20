---
name: kg-maintain
user-invocable: false
description: |
  Knowledge graph maintenance and self-reflection rules. ALWAYS ACTIVE:

  SELF-REFLECTION TRIGGERS — when these patterns occur, STOP and engage memory:

  SPINNING WHEELS: Few attempts at same action without progress.
  → Ask: What am I assuming? Have I seen this before? kg_search or kg_sync.
  → Capture: meta-learning (user level), specific approach (project level).

  USER CORRECTION: "No", "that's wrong", "focus", "step back".
  → STOP. Understand what user wants. Identify the signal you missed.
  → Capture: the pattern at user level so you recognize it next time.

  CONFUSION ABOUT KNOWN STATE: "Where is this data?" about something you should know.
  → Trace data flow explicitly. Don't guess.
  → Capture: organization (project), your pattern (user).

  UNEXPECTED RESULT: Tool output doesn't match expectation.
  → Understand WHY before working around it.
  → Capture: wrong mental model (user) or undocumented behavior (project).

  DEJA VU: "I feel like I've solved this before."
  → Check graph: kg_search. If found: use it. If missing: capture now.

  SESSION LIFECYCLE:
  - Start: kg_read(cwd) + scan for relevance (see kg-core)
  - During: Have you captured anything? If not, why not? Sync periodically.
  - After completing non-trivial task: What relationships are worth recording?
  - End/wrap-up: Flush pending insights. What took longer than expected? What helps next session?

  GRAPH HEALTH AWARENESS:
  - After kg_read, notice health line. High orphan % = connection opportunities.
  - After creating a node, connect it with kg_put_edge — one edge makes a node far more valuable.
  - Nodes without edges risk archival and add cost without compression benefit.

  MEMORY UPDATE DISCIPLINE: When a memorized approach fails or is corrected:
  1. Update the existing node with correct information (don't leave stale data)
  2. Scope appropriately — don't narrow to just the current instance if the pattern is general
  3. Delete or merge duplicate/outdated nodes
---

# Maintenance Reference (Detailed)

## What a Healthy Graph Looks Like

A healthy graph is a mesh of connections, not isolated facts. Most nodes participate
in at least one edge. Health stats show this at a glance:
- Low orphan rate — most nodes connected
- Reasonable edge density — linked but not over-connected
- Mix of levels — user patterns inform project decisions

## Maintenance Operations

When auditing the graph with kg_read:
- **Disconnected nodes** — appear in no edges. Connect or delete if stale.
- **Duplicates** — overlapping gists or IDs. Merge: keep richer one, delete other.
- **Stale knowledge** — about removed code or old decisions. kg_delete_node.
- **Broken edges** — pointing to outdated concepts. kg_delete_edge.

## Operational Safety

### Tool result size limit
Claude Code persists tool results over ~50K chars to disk — model sees only a 2KB preview.
kg_read output is the full graph as text. If kg_read shows a size warning (>40K chars):
1. Review nodes for staleness, duplicates, disconnected entries
2. Delete or merge low-value nodes
3. KG_MAX_TOKENS default is 4000 per graph level — two graphs ≈ 32K chars, safe under 50K limit

### Project renames
When project folder renamed, graph slug changes. Server handles via alias detection.
If project graph is unexpectedly empty, check ~/.knowledge-graph/projects/ for old name.

### Server restart
Server can be safely restarted (kg-memory restart). Validates PIDs, uses setsid,
drains connections, write-through persistence means no data loss.

## First Session in a Project

When project graph is empty:
1. Don't document the whole codebase. Capture what surprises you.
2. 2-5 foundational nodes for major components, connected by edges.
3. Add knowledge organically as you work.
4. Quality over quantity — every node earns its place through reuse.
