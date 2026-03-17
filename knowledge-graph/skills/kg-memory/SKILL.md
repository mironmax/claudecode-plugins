---
name: kg-memory
description: Persistent knowledge graph for cross-session learning
---

# Knowledge Graph Memory

Capture insights worth remembering. Retrieve them when relevant.

## Session Start (Required)

Every session, immediately:
```
kg_register_session(cwd="<project root>")  # Register session, get session_id
kg_read(session_id="<id>")                 # Load graph
```

## Core Mental Model

**Two storage levels:**
- `user` — Wisdom that applies everywhere (preferences, meta-learnings, principles)
- `project` — Codebase-specific knowledge (architecture, decisions, patterns)

**Two entry types:**
- `node` — A named concept, pattern, or insight
- `edge` — A relationship between two things (nodes, files, or concepts)

**One principle:**
Compress meaning. Maximum insight per symbol. If something can be expressed as a relationship between existing things, prefer an edge over a new node.

## Quick API

```
kg_put_node(level, id, gist, touches?, notes?, session_id?)   # Create/update node
kg_put_edge(level, from, to, rel, notes?, session_id?)         # Create/update edge
kg_read()                                                       # Load full graph
kg_sync(session_id)                                            # Get updates from other sessions
kg_recall(level, id)                                           # Retrieve archived node
kg_delete_node(level, id, session_id?)                         # Remove node + its edges
kg_delete_edge(level, from, to, rel, session_id?)              # Remove edge
kg_progress_get(task_id, level?)                               # Read task progress
kg_progress_set(task_id, state, level?, session_id?)           # Write task progress
kg_session_stats(session_id)                                   # Session duration, ops, graph sizes
kg_search(query, level?, session_id?)                          # Full-text search across nodes
kg_ping()                                                       # Health check
```

## What to Capture

| Priority | What | Level |
|----------|------|-------|
| Highest | User profile: domain expertise, background knowledge — calibrate explanations to what they already know | user |
| Highest | Meta-patterns: "I tend to X when I should Y" | user |
| High | Architectural principles that apply across projects | user |
| High | User interaction signals and preferences | user |
| Medium | Architecture decisions + rationale | project |
| Medium | Non-obvious dependencies, debugging discoveries | project |
| Low | Facts recoverable from artifacts (use touches/pointers) | project |

**Capture trigger:** Would this help avoid a similar mistake/inefficiency next time? → Capture it.

**Notes vs gist:** Gist = compressed fact (always visible). Notes = rationale, constraints, "why" (recalled on demand). When a decision has context that matters later, put it in notes — not lost, just out of the hot path.

## Compression Rules

1. Remove filler words — articles, hedging, unnecessary context
2. Use references instead of descriptions — `auth/` not "the auth module"
3. Encode relationships as structure — edges over verbose nodes
4. Generalize when patterns repeat
5. Headline test — gist should read like a newspaper headline

## Memory Traces

Edges to archived nodes remain visible as hints. Use `kg_recall(level, id)` when relevant to current task.

## Auto-Compaction

System archives lowest-scored nodes when graph exceeds token limit. Nodes protected for 3 days after update. Score = 0.25×recency + 0.50×connectedness + 0.25×richness (weighted sum of percentiles). Archived nodes remain on disk; edges to them stay visible.

## Available Skills

| Skill | Purpose |
|-------|---------|
| `/skill kg-scout` | Mine conversation history for patterns and insights |
| `/skill kg-extract` | Map codebase architecture into the knowledge graph |

## Reference Files

Detailed guidance in same directory:
- **CAPTURE.md** — Decision trees, compression techniques, edge-first thinking, examples
- **RECALL.md** — Sync timing, memory trace protocol, batch recall, reading strategies
- **MAINTAIN.md** — Self-reflection triggers, session lifecycle, graph health, maintenance ops

## Multi-Session

All sessions share the same server. Changes visible via `kg_sync(session_id)`. Last write wins — sync before important writes.

## Best Practices

1. **Capture immediately** — Context is freshest at discovery
2. **Prefer edges** — Connect existing things rather than creating new nodes
3. **Be terse** — Maximum insight per symbol
4. **Level consciously** — User for personal wisdom, project for team knowledge
5. **Follow memory traces** — Edges to missing nodes hint at useful archived knowledge
