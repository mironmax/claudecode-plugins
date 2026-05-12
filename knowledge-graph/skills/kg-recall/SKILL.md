---
name: kg-recall
user-invocable: false
description: |
  Knowledge recall rules. Active every session, integrated with all task work.

  After kg_read, scan all node IDs and gists — anything that feels related to the current
  task is worth reading in full: kg_read(cwd, id). Lean toward reading more rather than less;
  a wrong guess costs one tool call, missing context costs the whole task.

  Gists are headlines (WHAT). Notes and touches hold rationale (WHY) — read those when
  a decision depends on understanding the reasoning, not just the fact.

  Three tiers — nodes shift as the graph grows:
    active   → id + gist visible in kg_read
    archived → id only; edges visible as crumb trails
    orphaned → invisible in kg_read; reachable via kg_search

  Following crumbs: an edge pointing to an archived id is an invitation — kg_read(cwd, id)
  promotes it and surfaces any orphaned neighbors. No edges? Scan the archived list by name.
  kg_search reaches all tiers when you need to cast a wider net.

  Before stating an assumption — it's worth a quick kg_search first; the graph may already
  have the answer. Include session_id (from kg_read) so the project graph is searched too.
  No session yet? Run kg_read first, then search.

  Reading several related nodes at once is more efficient than one at a time.

  Consider kg_sync(session_id) before decisions that depend on shared knowledge, when another
  session may have been active, or after subagents complete their work.
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
- **Edges**: `from --rel--> to` — shown for active+archived nodes (crumb trails), no edge notes
- **Health stats**: node count, edge count, orphan %, avg edges/node
- **Session ID**: use for writes, deletes, and sync

**Large graph — output saved to file**: The graph has two sections — USER GRAPH and PROJECT GRAPH,
both needed for a complete picture. On large graphs the tool result may begin with `<persisted-output>`
and show only a short preview; the full output is saved to a file path shown in that preview.
Reading that file with the Read tool gives the complete graph including all archived IDs and the
session ID needed for subsequent calls. Announce "I have recalled KG Memories" once the full
output has been read.

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
