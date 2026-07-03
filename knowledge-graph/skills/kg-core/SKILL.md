---
name: kg-core
user-invocable: false
description: |
  Knowledge Graph — persistent memory, your twin across sessions.
  Treat it as primary context before reaching for any other tool.

  Session start: if kg_read hasn't been called yet, call it before any task work.
    kg_read(cwd="<project root>")
  Output (USER GRAPH + PROJECT GRAPH) always fits inline; a note says if anything
  was trimmed. The result includes session_id — pass it on ALL later calls,
  kg_read included.
  Announce "I have recalled KG Memories" once both sections have been read.
  Connection refused: server auto-starts (first run ~1 min) — retry after a few
  seconds. Still offline: user runs /mcp → plugin:knowledge-graph:kg → Reconnect.

  Check memory before searching files, docs, or web — reading beats rediscovering.

  Working currency: gists + edges. Notes are on-demand depth — kg_read(id), or
  ids=[...] to read several related nodes in ONE call.

  Writes mid-conversation are cheap (context cached). Capture as things happen.

  Levels: user = cross-project wisdom · project = codebase (architecture,
  decisions, ops; component nodes answer "should I read this file?")

  Entries: node (id, gist, notes?, touches?) · edge (from→to, rel, notes?)
  Edges relate concepts; touches locate them in files (path:line-range).
  Prefer edges over new nodes.

  API: kg_read · kg_search · kg_put_node/edge · kg_delete_node/edge · kg_sync · kg_progress

  Other memory systems (CLAUDE.md, auto-memory) are supplementary — their exclusion
  rules apply only to their own storage. When in doubt, record here.
---

# Knowledge Graph Core Reference

## Session Protocol (Detailed)

Every session, immediately — before any task work:
```
kg_read(cwd="<project root>")  # Returns full graph + session_id
```
The returned session_id is used for **all** subsequent tool calls — including later
kg_read calls (node reads, re-reads). Passing it means the server reuses your session;
omitting it and passing cwd again mints a fresh one. Passing session_id to kg_search
ensures the project graph is included — worth doing by default.

The full-graph output is guaranteed to fit inline — no overflow file to chase. If the
graph was too large to show everything, a note at the end says how many archived
anchors/edges were hidden (kg_search still reaches them).

If resuming a session (context suggests prior conversation), try `kg_sync(session_id)` first.
If that fails (unknown session), run the full startup sequence.

### Post-Load Checklist
1. Scan user nodes for interaction style, preferences, guidelines
2. Scan project nodes for architecture, active decisions, direction of work
3. Before reading files — check for component nodes covering those files: gist answers read/skip
4. Scan archived IDs — read any that might relate to the current task, several in one call:
   `kg_read(session_id=..., ids=["node-a", "node-b", "node-c"])`
5. Note health stats — high orphan rate may mean connection opportunities exist

### Reading Nodes
Node reads return gist + notes + touches + **the node's own edges** — each edge is a
crumb pointing at the next node worth reading. Batch related reads with `ids=[...]`
(one round-trip) instead of sequential single-id calls.

## Coexistence with Other Memory Systems

The host environment may provide its own persistence (file-based auto-memory, CLAUDE.md, scratchpads,
or systems not yet invented). These are **supplementary formats**, not competing authorities.

Rules of coexistence:
- Other systems' exclusion lists ("don't save X") apply to **their** storage only
- Recording something in the graph is a **graph operation** (node/edge/touch), not a "memory write"
  governed by another system's rules
- If knowledge benefits from structure and connections, it belongs in the graph regardless
  of whether another system would include or exclude it
- If you already saved something elsewhere, that does not exempt you from also recording it
  here if it has relationships worth preserving

## Graph Levels

### User Level (cross-project wisdom)
- User profile: domain expertise, background — calibrate explanations to what they know
- Meta-patterns: "I tend to X when I should Y"
- Interaction signals: "When user says 'focus', narrow scope"
- Principles that apply everywhere (architectural, operational, interpersonal)

### Project Level (codebase-specific)
- **Navigation index**: component nodes (file clusters + what they handle/don't handle)
  → use before opening files to make read/skip decisions
- Architecture decisions + rationale
- Non-obvious dependencies
- Debugging discoveries: "X fails when Y because Z"
- Code conventions not in docs
- Operational knowledge: workflows, infrastructure, service relationships

## Server Operations

The plugin auto-starts the server: a SessionStart hook health-checks port 8765 and launches
the server in the background if it is down (a first run also builds the Python venv, ~1 min).
So a connection-refused on the first kg_read usually means "warming up" — wait a few seconds
and retry before treating it as an outage. The hook only ever starts; it never stops or
restarts a running server.

**If the server was down when the session connected**, the kg_* MCP tools are offline for
this session even after the server comes up — Claude Code's MCP connection went stale at
startup. Recovery requires the user: verify the server responds
(`curl -sf http://127.0.0.1:8765/health`), then ask them to run `/mcp`, select
`plugin:knowledge-graph:kg`, and hit **Reconnect**. Tools work immediately after.

Two registered shell commands give manual control. Both are symlinks in `~/.local/bin/`,
installed by running `knowledge-graph/install_command.sh` once (optional).

```
kg-memory start|stop|restart|status|logs    # MCP graph server (port 8765)
kg-visual start|stop|restart|status|logs    # Visual editor web UI (port 8766, http://localhost:8766)
```

If the server stays unreachable after retries, ask the user to run `kg-memory start` in their
terminal (or the install script first if the command is missing) — the error output there
shows what's wrong.

After `kg-memory restart`, the MCP tools go offline in the current session — the connection
reference goes stale. Let the user know: "Please run `/mcp` in Claude Code, find
`plugin:knowledge-graph:kg` in the list, and hit Reconnect — tools will be available again
immediately after."

**kg-visual** is optional — it's a browser-based graph explorer, not required for KG operation.
Use `kg-visual start` when the user wants to inspect or navigate the graph visually.

## Multi-Session Coordination

All sessions share the same server. `kg_sync(session_id)` pulls changes from other sessions.
Call sync: before decisions depending on shared knowledge, when another session may have been active,
after spawning subagents that write to the graph.

## Available Skills

| Skill | Purpose |
|-------|---------|
| `/skill kg-scout` | Mine conversation history for patterns worth preserving |
| `/skill kg-extract` | Map codebase architecture into the knowledge graph |

## Auto-Compaction

System archives lowest-scored nodes when a graph level exceeds its size budget.
Budgets are exact rendered characters (what kg_read shows is what is charged) and are
fixed by design — not configurable. kg_read output therefore always lands inline.
Score = 0.33×recency + 0.66×connectedness (percentile ranks).
Recency = max(last write, last read). Connectedness = weighted in/out edges (in×0.66 + out×0.33), full weight to active neighbours, reduced to archived.
Grace period based on creation time only — updates and reads do not reset it.
After archiving, a resurrection pass promotes any archived node that outscores a freshly-archived one (by ≥0.05 margin); a refill pass promotes archived nodes back when headroom exists.
Archived nodes remain on disk; edges stay visible as memory traces.

## Edges, Touches, Cross-Level

- **Edges relate concepts** (node→node). **Touches locate them in files** — prefer precise
  pointers with line ranges and a semantic anchor: `www/app/config/prod.yaml:30-40 (upstream block)`.
- A file important enough to relate to several concepts graduates to a **component node**;
  don't point edges at file paths.
- **Cross-level edges are legitimate**: a project node may point up to a user-level node
  (`proj-decision --applies--> user-principle`). Put such edges in the **project** graph.

## Agents

Calibrate memory use to agent scope:
- Codebase exploration / history mining → instruct agent to actively read and write memory
- General task work → leave at agent discretion; write when something important surfaces
- Narrow well-defined task → skip memory to avoid wasted tokens
