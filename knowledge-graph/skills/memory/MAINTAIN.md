# Maintaining the Knowledge Graph

## What a Healthy Graph Looks Like

A healthy graph is a mesh of connections, not a collection of isolated facts. Most nodes participate in at least one edge. Concepts branch and cross-reference naturally. The health stats in `kg_read` output show this at a glance:

- **Low orphan rate** (<20%) — most nodes are connected
- **Reasonable edge density** (1-3 edges/node) — concepts are linked but not over-connected
- **Mix of levels** — user-level patterns inform project-level decisions

When you notice many orphans after loading, look for natural connections. This isn't an obligation — it's an opportunity to strengthen the graph's compression power.

## Self-Reflection Triggers

### Spinning wheels
**Pattern:** 3+ attempts at the same action without progress.
**Response:** STOP. Ask: What am I assuming? Have I seen this before? (`kg_sync()` then `kg_search()`)
**Capture:** Meta-learning at user level, specific issue at project level.

### User correction
**Pattern:** "No," "that's wrong," "focus," "step back."
**Response:** STOP. Understand what the user actually wants. Identify the signal you missed.
**Capture:** The pattern at user level — so you recognize the signal next time.

### Confusion about known state
**Pattern:** "Where is this data?" about something you should know.
**Response:** Trace the data flow explicitly instead of guessing.
**Capture:** Poor organization → project level; your pattern → user level.

### Unexpected result
**Pattern:** Tool output doesn't match expectation.
**Response:** Understand WHY before working around it.
**Capture:** Wrong mental model (user), undocumented behavior (project), or actual bug.

### Deja vu
**Pattern:** "I feel like I've solved this before."
**Response:** Check the graph — `kg_search()`. If found: use it. If missing: capture now.

## Session Lifecycle

### Start
Always, immediately:
```
kg_register_session(cwd="<project root>")
kg_read(session_id="<id>")
```
Scan loaded graph for anything relevant to upcoming work. Notice health stats — if orphan rate is high, keep connection opportunities in mind.

### During
Every ~30 minutes: Have you captured anything? If not, why not?
`kg_sync()` to check for updates from other sessions.
After completing a non-trivial task: What relationships are worth recording?

### End
When user says "wrap up" or conversation winds down:
1. **Flush** — Capture pending insights
2. **Reflect** — What took longer than expected? What would help next session?
3. **Suggest** — If spare capacity: `/skill scout` or `/skill extract`

## Graph Health

### Compaction (automatic)
System archives low-value nodes when graph exceeds token limits.

**What keeps a node alive:** Recent updates (7 days), connections (edges), rich content (gist + notes).
**What gets archived first:** Old, isolated, sparse nodes.
**Your role:** If a node matters, connect it or update it occasionally.

### Orphan awareness
Nodes without edges are at risk of archival and add cost without compression benefit. When creating a node, the server will gently remind you if it has no connections. Take the hint — even one edge makes a node significantly more valuable.

### Maintenance operations
When auditing the graph with `kg_read()`:

- **Disconnected nodes** — appear in no edges. Either connect them or delete if stale.
- **Duplicates** — overlapping gists or IDs. Merge: keep the richer one, delete the other.
- **Stale knowledge** — about removed code or old decisions. `kg_delete_node()`.
- **Broken edges** — pointing to outdated concepts. `kg_delete_edge()`.

## Operational Safety

### Tool result size limit
Claude Code truncates tool results over ~50K characters to a 2000-char preview. The `kg_read` output is the full graph as text — if it exceeds this limit, the agent loses graph context silently. This is why `KG_MAX_TOKENS` defaults to 3000.

**If kg_read output shows a size warning (>40K chars):**
1. Ask the user if they want to run graph maintenance
2. Review nodes for staleness, duplicates, and disconnected entries
3. Delete or merge low-value nodes to reduce graph size
4. Do NOT increase `KG_MAX_TOKENS` — the limit exists to prevent truncation

### Project renames
When a project folder is renamed, the graph slug changes and the server creates a new empty graph. The system handles this automatically via alias detection and migration, but if you notice a project graph is unexpectedly empty:
1. Check `~/.knowledge-graph/projects/` for a directory matching the old name
2. The old data is still there — the server will migrate it on next access
3. If auto-migration didn't trigger, report this as a bug

### Server restart safety
The MCP server can be safely restarted from within Claude Code (`kg-memory restart`). The server:
- Validates PIDs before killing (won't kill non-server processes)
- Launches new process in a separate session (setsid)
- Drains connections gracefully before shutdown
- Write-through persistence means no data loss on restart

## First Session in a Project

When the project graph is empty:
1. Don't document the whole codebase. Capture what surprises you.
2. 2-5 foundational nodes for major components, connected by edges.
3. Add knowledge organically as you work on the user's actual task.
4. Quality over quantity — every node should earn its place through reuse.
