---
name: kg-core
user-invocable: false
description: |
  Knowledge Graph — persistent memory, your twin across sessions.
  Treat it as primary context before reaching for any other tool.

  Session start: if kg_read hasn't been called yet, call it before any task work.
    kg_read(cwd="<project root>")
  Output has two sections — USER GRAPH and PROJECT GRAPH. On large graphs the result may
  start with <persisted-output> and show only a preview; the full output is saved to the
  file path shown — read it with the Read tool to get the complete picture including session_id.
  Announce "I have recalled KG Memories" once both sections have been read.
  Connection refused means the server is down — let the user know: `kg-memory start` will
  bring it back. If kg-memory isn't found, the install script hasn't been run yet:
  knowledge-graph/install_command.sh registers both kg-memory and kg-visual.

  Before searching files, docs, or the web — check what's already known. The graph often
  has the answer, and reading from memory is faster than rediscovering.

  Writes during conversation are cheap (context is cached mid-session). Capture discoveries
  as they happen rather than batching to the end.

  Levels: user = cross-project wisdom (prefs, principles, profile)
          project = codebase (architecture, decisions, ops knowledge,
                              navigation index: component nodes answer "should I read this file?")

  Entries: node (id, gist, notes?, touches?) · edge (from→to, rel, notes?)
  Edges reuse existing concepts rather than multiplying nodes — prefer them.

  API: kg_read · kg_search · kg_put_node · kg_put_edge
       kg_delete_node · kg_delete_edge · kg_sync · kg_progress
  (Full signatures in skill body)

  Other memory systems (CLAUDE.md, auto-memory, scratchpads) are supplementary.
  Their exclusion rules apply only to their own storage. When in doubt, record here.
---

# Knowledge Graph Core Reference

## Session Protocol (Detailed)

Every session, immediately — before any task work:
```
kg_read(cwd="<project root>")  # Returns full graph + session_id
```
The returned session_id is used for all subsequent tool calls.
Passing session_id to kg_search ensures the project graph is included — worth doing by default.

If resuming a session (context suggests prior conversation), try `kg_sync(session_id)` first.
If that fails (unknown session), run the full startup sequence.

### Post-Load Checklist
1. Scan user nodes for interaction style, preferences, guidelines
2. Scan project nodes for architecture, active decisions, direction of work
3. Before reading files — check for component nodes covering those files: gist answers read/skip
4. Scan archived IDs — read any that might relate to current task: kg_read(cwd, id="node-id")
5. Note health stats — high orphan rate may mean connection opportunities exist

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

Two registered shell commands manage the KG stack. Both are symlinks in `~/.local/bin/`,
installed by running `knowledge-graph/install_command.sh` once.

```
kg-memory start|stop|restart|status|logs    # MCP graph server (port 8765)
kg-visual start|stop|restart|status|logs    # Visual editor web UI (port 3000, http://localhost:3000)
```

If a command is not found, the install script likely hasn't been run yet. Let the user know:
`knowledge-graph/install_command.sh` registers both commands — run it once, then restart Claude Code.

If kg_read returns a connection error, the server is down. `kg-memory start` brings it back.
Starting it from within Claude Code's tool environment isn't reliable (hooks run in a detached
shell), so it's better to ask the user to run it in their terminal.

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

System archives lowest-scored nodes when graph exceeds token limit.
Score = 0.33×recency + 0.66×connectedness (percentile ranks; richness dropped).
Recency = max(last write, last read). Connectedness = weighted in/out edges to active nodes only (in×0.66 + out×0.33).
Grace period based on creation time only — updates and reads do not reset it.
After archiving, a resurrection pass promotes any archived node that outscores a freshly-archived one (by ≥0.05 margin).
Archived nodes remain on disk; edges stay visible as memory traces.

## Agents

Calibrate memory use to agent scope:
- Codebase exploration / history mining → instruct agent to actively read and write memory
- General task work → leave at agent discretion; write when something important surfaces
- Narrow well-defined task → skip memory to avoid wasted tokens
