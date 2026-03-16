# Retrieving Knowledge

## Automatic Loading

Every session starts with:
```
kg_register_session(cwd="<project root>")
kg_read(session_id="<id>")
```

Output format:
- **Active nodes**: `id: gist` — full gist, no notes
- **Archived nodes**: `id` only — visible as recall hints
- **Edges**: `from --rel--> to`
- **Health stats**: node count, edge count, orphan count/%, avg edges/node

Notes are never included in `kg_read` — they're on-demand via `kg_recall` or `kg_search`.

### Health awareness after loading

After `kg_read`, notice the health line. High orphan rate means many nodes float unconnected — they cost tokens but don't benefit from the graph's compression through reuse. When you encounter orphans naturally during work, consider connecting them.

## When to Sync

Call `kg_sync(session_id)` when:
- Before decisions depending on shared knowledge
- When you suspect another session has been active
- Every 30+ minutes in long sessions
- After spawning subagents that write to the graph

## Memory Traces

When you see an edge pointing to a node not in your active view:
```
active-node --influenced-by--> archived-node-id
```

This is a **memory trace** — the archived node's edges remain visible as hints.

### When to follow

Is the archived node relevant to current task?
- YES → `kg_recall(level, id)`
- UNCLEAR → Consider the relationship type. Suggests importance? Recall it. Tangential? Skip.
- NO → Skip

### How to recall

By ID:
```
kg_recall(level="project", id="archived-node-id")
```

By content (when you don't know the ID):
```
kg_search(query="chown permissions docker", level="project", session_id="...")
```

**Recall liberally when:** Making architectural decisions, debugging something familiar, user asks "why did we do X?", you need historical context.

### Batch recall

When exploring a topic, recall several related nodes at once:
```
kg_recall(level="project", id="old-auth-decision")
kg_recall(level="project", id="session-redesign")
kg_recall(level="project", id="security-audit-findings")
```

## Reading Strategies

### Scan for relevance
After `kg_read()`, scan node IDs and gists for anything relevant to the current task.

### Follow edges
Start from a known-relevant node and traverse its edges — what depends on it? What does it depend on? This is where the graph's structure pays off: relationships reveal context that flat notes cannot.

### Level-appropriate search
- **User level:** Personal preferences, meta-learnings, cross-project patterns
- **Project level:** Architecture, decisions, codebase-specific knowledge

## Subagent Coordination

When spawning subagents that need domain context:
1. Include instruction: "First call `kg_read()` to load knowledge graph"
2. Subagent writes are visible to parent via shared server
3. After completion, call `kg_sync()` to see their discoveries

Skip graph loading for simple subagents (file operations, searches) — unnecessary context.
