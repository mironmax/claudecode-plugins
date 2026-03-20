---
name: kg-recall
user-invocable: false
description: |
  Knowledge recall rules. ALWAYS ACTIVE during every session:

  PROACTIVE RECALL AT TASK START: After kg_read, scan all node IDs and gists.
  If ANY node feels related to the current task — read it in full with kg_read(cwd, id).
  Bias toward false positives. Wrong recall = 1 tool call wasted. Missing context = failed task.

  MEMORY TRACES: Edges pointing to archived nodes are hints. When you see
  "active-node --rel--> archived-node-id", the archived node likely has relevant context.
  Follow these traces — read the archived node to promote it and see its full content.

  WHEN TO READ A NODE IN FULL:
  - Starting a task near a known topic → read related archived nodes
  - Active node gist signals action/context you should act on → read for full notes
  - Making architectural decisions → read decision history nodes
  - Debugging something familiar-feeling → kg_search first, then read matches
  - User asks "why did we do X?" → read nodes with notes explaining rationale
  - Encountering a problem class you've seen before → STOP, search before guessing

  GIST vs FULL READ: kg_read() returns gists only (compressed headlines — WHAT, not WHY).
  kg_read(cwd, id) returns the full node: gist + notes + touches. Notes contain rationale,
  constraints, and "why" — the context that matters for decisions. When a node looks relevant,
  read it in full. This is especially important for action-item nodes (test plans, pending work,
  checklists) where the gist summarizes intent but notes contain the steps.

  BATCH RECALL: When exploring a topic, read several related nodes at once rather than
  one at a time. This is more efficient and gives you complete context.

  WHEN TO SYNC: Call kg_sync(session_id) when:
  - Before decisions depending on shared knowledge
  - When you suspect another session has been active
  - Every 30+ min in long sessions
  - After spawning subagents that write to the graph
---

# Recall Reference (Detailed)

## Automatic Loading

Every session starts with:
```
kg_read(cwd="<project root>")
```
Returns: full graph (gists only) + session_id for subsequent calls.

Output format:
- **Active nodes**: `id: gist` — compressed headline, no notes
- **Archived nodes**: `id` only — read with kg_read(cwd, id) to see full content and promote
- **Edges**: `from --rel--> to`
- **Health stats**: node count, edge count, orphan %, avg edges/node
- **Session ID**: use for writes, deletes, and sync

## Reading a Node in Full

```
kg_read(cwd="<project root>", id="node-id")
```
Returns: gist + notes + touches. If the node is archived, promotes it to active automatically.

## Reading Strategies

### Scan for relevance
After kg_read, scan node IDs and gists for anything relevant to current task.

### Follow edges
Start from a known-relevant node and traverse its edges. What depends on it?
What does it depend on? This is where graph structure pays off.

### Level-appropriate search
- User level: preferences, meta-learnings, cross-project patterns
- Project level: architecture, decisions, codebase knowledge

## Subagent Coordination

When spawning subagents that need domain context:
1. Include instruction: "First call kg_read(cwd=...) to load knowledge graph"
2. After completion, call kg_sync(session_id) to see their discoveries
3. Skip graph loading for straightforward tasks that don't need domain context

## How to recall

By ID (returns full node content, promotes if archived):
```
kg_read(cwd="<project root>", id="node-id")
```

By content (when you don't know the ID):
```
kg_search(query="chown permissions docker", session_id="...")
```
