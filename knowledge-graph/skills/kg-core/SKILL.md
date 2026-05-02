---
name: kg-core
user-invocable: false
description: |
  Knowledge Graph — persistent memory, your twin across sessions.
  Treat it as primary context before reaching for any other tool.

  SESSION START: If kg_read not yet called this session, call it now — before any task work.
    kg_read(cwd="<project root>")
  Announce: "I have recalled KG Memories."
  If connection refused: "KG server not running — should I start it?"

  MEMORY FIRST: Before searching files, docs, or web — check what you already know.
  Self-reflect when context feels thin. The graph likely has the answer.

  WRITE DURING CONVERSATION: Context is cached mid-session — memory writes cost ~tokens,
  not round-trips. Capture discoveries as they happen. Don't batch to end of task.

  LEVELS: user = cross-project wisdom (prefs, principles, profile)
           project = codebase (architecture, decisions, ops knowledge,
                               navigation index: component nodes answer "should I read this file?")

  ENTRIES: node (id, gist, notes?, touches?) · edge (from→to, rel, notes?)
  Prefer edges — they reuse existing concepts rather than multiplying nodes.

  API: kg_read · kg_search · kg_put_node · kg_put_edge
       kg_delete_node · kg_delete_edge · kg_sync · kg_progress
  (Full signatures in skill body)

  INDEPENDENCE: Other systems' exclusion rules govern their storage only. When in doubt, record here.
---

# Knowledge Graph Core Reference

## Session Protocol (Detailed)

Every session, immediately — before any task work:
```
kg_read(cwd="<project root>")  # Returns full graph + session_id
```
The returned session_id is used for all subsequent tool calls.

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
Score = 0.25*recency + 0.50*connectedness + 0.25*richness (weighted sum of percentiles).
Nodes protected for some days after update. Archived nodes remain on disk; edges stay visible as memory traces.

## Agents

Calibrate memory use to agent scope:
- Codebase exploration / history mining → instruct agent to actively read and write memory
- General task work → leave at agent discretion; write when something important surfaces
- Narrow well-defined task → skip memory to avoid wasted tokens
