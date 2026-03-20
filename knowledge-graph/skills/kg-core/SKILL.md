---
name: kg-core
user-invocable: false
description: |
  Knowledge graph persistent memory system. CRITICAL behavioral rules:

  SESSION AWARENESS: Before starting ANY task, check if kg_read has been called this session.
  If graph is not loaded (no node/edge data visible in recent context), IMMEDIATELY run:
    kg_read(cwd="<project root>")
  This applies to new sessions, resumed sessions, and sessions continuing after context compaction.
  If kg_read fails (connection refused), tell user: "Memory server not running. Start with kg-memory start?"

  AFTER LOADING: Review ALL user-level nodes — they contain working style rules, pitfall patterns,
  confirmed preferences. Treat as defaults. Review project-level nodes for architecture and decisions.
  Scan archived node IDs — if ANY feel related to upcoming work, read them in full with
  kg_read(cwd, id). Err on reading too many. A wasted read = 1 tool call. Missing context = failed task.

  SELF-AWARENESS CHECK: If you encounter a problem class that "feels familiar" (permissions, caching,
  deployment, file ownership, etc.) — STOP and kg_search before attempting a solution. Your memory
  likely has the answer. Never guess when you can check.

  TWO STORAGE LEVELS:
  - user: cross-project wisdom (preferences, principles, meta-learnings, user profile)
  - project: codebase-specific (architecture, decisions, dependencies, patterns)

  TWO ENTRY TYPES:
  - node: named concept/pattern/insight (id + gist + optional notes/touches)
  - edge: relationship between nodes/files/concepts (from + to + rel + optional notes)

  CORE PRINCIPLE: Compress meaning. Maximum insight per symbol. Prefer edges over new nodes.
  If something can be expressed as a relationship between existing things, use an edge.

  API (8 tools):
  kg_read(cwd, id?, level?) → init session + full graph, or single node with full content
  kg_search(query, session_id?) → full-text search across both levels
  kg_put_node(session_id, level, id, gist, notes?, touches?) → create/update node
  kg_put_edge(session_id, level, from, to, rel, notes?) → create/update edge
  kg_delete_node(session_id, id) → delete node (auto-resolves level)
  kg_delete_edge(session_id, from, to, rel) → delete edge (auto-resolves level)
  kg_sync(session_id) → changes from other sessions
  kg_progress(session_id, task_id, state?, level?) → get/set task progress
---

# Knowledge Graph Core Reference

## Session Protocol (Detailed)

Every session, immediately — before any task work:
```
kg_read(cwd="<project root>")  # Returns full graph + session_id
```
The returned session_id is used for all subsequent tool calls (writes, deletes, sync).

If resuming a session (context suggests prior conversation), try `kg_sync(session_id)` first.
If that fails (unknown session), run the full startup sequence.

### Post-Load Checklist
1. Scan user nodes for interaction style, preferences, known pitfalls
2. Scan project nodes for architecture, active decisions, known gotchas
3. Scan archived IDs — read any that might relate to current task: kg_read(cwd, id="node-id")
4. Note health stats — high orphan rate means connection opportunities exist

## Graph Levels

### User Level (cross-project wisdom)
- User profile: domain expertise, background — calibrate explanations to what they know
- Meta-patterns: "I tend to X when I should Y"
- Interaction signals: "When user says 'focus', narrow scope"
- Architectural principles that apply everywhere
- Tool insights and quality bars

### Project Level (codebase-specific)
- Architecture decisions + rationale
- Non-obvious dependencies
- Debugging discoveries: "X fails when Y because Z"
- Code conventions not in docs

## Multi-Session Coordination

All sessions share the same server. `kg_sync(session_id)` pulls changes from other sessions.
Call sync: before decisions depending on shared knowledge, when another session may have been active,
every 30+ min in long sessions, after spawning subagents that write to the graph.

## Available Skills

| Skill | Purpose |
|-------|---------|
| `/skill kg-scout` | Mine conversation history for patterns worth preserving |
| `/skill kg-extract` | Map codebase architecture into the knowledge graph |

## Auto-Compaction

System archives lowest-scored nodes when graph exceeds token limit.
Score = 0.25*recency + 0.50*connectedness + 0.25*richness (weighted sum of percentiles).
Nodes protected for 3 days after update. Archived nodes remain on disk; edges stay visible as memory traces.
