---
name: kg-recall
user-invocable: false
description: |
  Knowledge recall rules. Active every session, integrated with all task work.

  After kg_read, scan all node IDs and gists — anything that feels related to the current
  task is worth reading in full, several at once: kg_read(session_id, ids=[...]).
  Lean toward reading more rather than less; a wrong guess costs one tool call,
  missing context costs the whole task.

  Gists + edges are the working currency (WHAT + how things relate). Notes hold
  rationale (WHY) — read a node in full when a decision depends on the reasoning.
  Node reads also return the node's edges: each one is the next crumb.

  Three tiers — nodes shift as the graph grows:
    active   → id + gist visible in kg_read
    archived → id only; edges visible as crumb trails
    orphaned → invisible in kg_read; reachable via kg_search

  Following crumbs: an edge pointing to an archived id is an invitation — reading it
  promotes it and surfaces any orphaned neighbors. Batch the whole trail into one
  ids=[...] call when several nodes look related. No edges? Scan the archived list
  by name. kg_search reaches all tiers when you need to cast a wider net.

  Before stating an assumption — it's worth a quick kg_search first; the graph may already
  have the answer. Include session_id so the project graph is searched too.

  Consider kg_sync(session_id) before decisions that depend on shared knowledge, when another
  session may have been active, or after subagents complete their work.
---

# Recall Reference (Detailed)

## Node States

| State | Visible in kg_read | Visible in kg_search | How to surface |
|-------|--------------------|----------------------|----------------|
| active | ✓ id + gist | ✓ | Already visible |
| archived | ✓ id only + edges | ✓ | kg_read(session_id, id) → promotes to active |
| orphaned | ✗ invisible | ✓ flagged | kg_search → then kg_read(session_id, id) |

## Automatic Loading

Every session starts with:
```
kg_read(cwd="<project root>")
```
Returns: full graph (gists only) + session_id for subsequent calls.

Output format:
- **Active nodes**: `id: gist` — compressed headline, no notes
- **Archived nodes**: `id` only — read with kg_read(session_id, id) to promote
- **Edges**: `from --rel--> to` — shown for active+archived nodes (crumb trails), no edge notes
- **Health stats**: node count, edge count, orphan %, avg edges/node
- **Session ID**: use for writes, deletes, and sync

The output always fits inline (the server guarantees it). If a very large graph had to be
trimmed, a closing note says how many archived anchors/edges were hidden — kg_search still
reaches them. Announce "I have recalled KG Memories" once both sections have been read.

## Reading Nodes in Full

```
kg_read(session_id="...", id="node-id")             # one node
kg_read(session_id="...", ids=["a", "b", "c"])      # several in ONE call — prefer this
```
Returns per node: gist + notes + touches + the node's own edges (crumbs to the next hop).
If archived or orphaned, promotes to active. Pass the session_id from the first kg_read —
don't re-pass cwd on every read.
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
