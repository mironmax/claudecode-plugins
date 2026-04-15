---
name: kg-recall
user-invocable: false
description: |
  Knowledge recall rules. Active every session, integrated with all task work.

  PROACTIVE RECALL: After kg_read, scan all node IDs and gists.
  If ANY node feels related to the current task — read it in full: kg_read(cwd, id).
  Bias toward false positives. Wrong recall = 1 tool call wasted. Missing context = failed task.

  GIST vs FULL: kg_read() returns gists only (compressed headline — WHAT, not WHY).
  kg_read(cwd, id) returns gist + notes + touches — the rationale that matters for decisions.

  THREE TIERS — nodes move through states as the graph fills:
    active   → gist visible in kg_read
    archived → ID only visible; edges shown as crumb trails
    orphaned → invisible in kg_read/kg_sync; reachable via kg_search only

  FOLLOWING CRUMBS:
    1. Edge list shows archived IDs: "active --rel--> archived-id" → follow it.
    2. kg_read(cwd, id) promotes the archived node to active AND rescues its orphaned
       neighbors back to archived — they reappear in the edge list. Follow the chain.
    3. No edges left? Scan archived ID list by name alone. Recognize it, read it.
    4. kg_search reaches all tiers including orphaned. Last lifeline for buried nodes.

  BATCH RECALL: Read several related nodes at once rather than one at a time.

  WHEN TO SYNC: Call kg_sync(session_id) before decisions depending on shared knowledge,
  when another session may have been active, or after subagents finish.
---

# Recall Reference (Detailed)

## Node States

| State | Visible in kg_read | Visible in kg_search | How to surface |
|-------|--------------------|----------------------|----------------|
| active | ✓ id + gist | ✓ | Already visible |
| archived | ✓ id only + edges | ✓ | kg_read(cwd, id) → promotes to active |
| orphaned | ✗ invisible | ✓ flagged | kg_search → then kg_read(cwd, id) |

## Automatic Loading

Every session starts with:
```
kg_read(cwd="<project root>")
```
Returns: full graph (gists only) + session_id for subsequent calls.

Output format:
- **Active nodes**: `id: gist` — compressed headline, no notes
- **Archived nodes**: `id` only — read with kg_read(cwd, id) to promote
- **Edges**: `from --rel--> to` — shown for active+archived nodes (crumb trails)
- **Health stats**: node count, edge count, orphan %, avg edges/node
- **Session ID**: use for writes, deletes, and sync

## Reading a Node in Full

```
kg_read(cwd="<project root>", id="node-id")
```
Returns: gist + notes + touches. If archived or orphaned, promotes to active.
**Promotion chain**: if the promoted node has edges to orphaned nodes, those are
automatically rescued back to archived — they reappear in the edge list.

## Following Crumb Chains

1. **Edge trail**: `active --rel--> archived-id` → read the archived node → it promotes
2. **Chain rescue**: promoted node's orphaned neighbors surface back to archived → follow them
3. **ID scan**: no edges? Scan archived ID list by name. Recognize it, read it.
4. **Deep search**: `kg_search("keyword")` reaches all tiers including orphaned nodes

## Reading Strategies

### Scan for relevance
After kg_read, scan node IDs and gists for anything relevant to current task.

### Follow edges
Start from a known-relevant node and traverse its edges.

### Level-appropriate search
- User level: preferences, meta-learnings, cross-project patterns
- Project level: architecture, decisions, codebase knowledge

## Subagent Coordination

When spawning subagents that need domain context:
1. Include instruction: "First call kg_read(cwd=...) to load knowledge graph"
2. After completion, call kg_sync(session_id) to see their discoveries
3. Skip graph loading for straightforward tasks that don't need domain context
