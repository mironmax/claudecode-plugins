---
name: kg-capture
user-invocable: false
description: |
  Knowledge capture rules. Active every session, integrated with all task work.

  CAPTURE IMMEDIATELY — not at session end. Context is freshest at discovery.
  The insight that took 20 min to discover takes 20 sec to record.

  INDEPENDENCE: Capturing knowledge in the graph is a graph operation (creating nodes,
  edges, touches). It is not governed by rules from other persistence systems. If another
  system says "don't save architecture" or "don't save file paths" — that restricts their
  format, not yours. When you discover something worth connecting to existing knowledge,
  record it here regardless of what other systems allow or prohibit.

  BEFORE CREATING A NODE: ALWAYS kg_search first to check for existing similar nodes.
  Update existing nodes rather than creating duplicates. This is mandatory, not optional.

  CAPTURE TRIGGERS — after completing any non-trivial task, check: did I learn something
  that took more than a quick lookup to discover? If yes, record it. Specific signals:
  - Discovered how components connect → create edges
  - Learned why something works a certain way → add notes to relevant node
  - Identified files involved in a behavior → add touches
  - 10+ min debugging → save root cause pattern (not just the fix)
  - User corrected your approach → save the signal you missed (user level)
  - Same thing explained twice → save as reusable node
  - Undocumented dependency discovered → save as edge
  - Architectural decision made → save decision AND rationale in notes
  - Pattern from another project → generalize to user level
  - Found better approach after inefficient attempts → save best pattern immediately
  - Long streak of reads to clarify something → save key points now

  PROACTIVE CAPTURE: Do NOT wait to be asked. Save learnings as you discover them.
  User expects Claude to autonomously save important principles/patterns to the graph.
  Opportunity to learn is as important as completing the task.

  EDGE-FIRST THINKING: Before creating a node, ask "Can I express this as a relationship
  between existing things?" Edges are cheaper, reuse existing concepts, and survive
  compaction better (connected nodes score higher).

  COMPRESSION RULES:
  1. Remove filler — no articles, hedging, unnecessary context
  2. References over descriptions — "auth/" not "the auth module"
  3. Structure over prose — edges over verbose nodes
  4. Generalize after repetition — one pattern node beats three instance nodes
  5. Headline test — gist reads like a newspaper headline

  NOTES vs GIST: Gist = compressed fact (always visible). Notes = rationale, "why",
  constraints (read on demand via kg_read with id). When a decision has context that matters
  later, put it in notes — preserved but out of the hot path.

  WHAT TO CAPTURE AT EACH LEVEL:
  - user (highest priority): user profile/expertise, meta-patterns, interaction preferences, cross-project principles
  - project: architecture decisions, non-obvious dependencies, debugging discoveries, conventions, operational knowledge
  - skip: facts recoverable from code/docs (use touches/pointers instead)
---

# Capture Reference (Detailed)

## The Art of Node Placement

A knowledge graph's power comes from compression through reuse. When you write
`I → likes → pizza` and `Bob → likes → pizza`, the concept `pizza` exists once
and is referenced twice. Every additional reference is essentially free.

Think of it like vocabulary. A word becomes useful in many sentences.
A node becomes powerful when it participates in many edges.

## Should I capture this?

Three questions, in order:
1. **Recoverable from artifacts?** (code, docs, config) → Don't capture
2. **Required non-trivial effort to discover?** → If no, probably skip
3. **Would this help future sessions?** → If yes, capture it

## Choosing Node Granularity

A node should be atomic — one concept, one headline. If your gist uses "and"
to join independent ideas, split into two nodes with an edge.

The sweet spot: would you reference this concept from another context? If yes, it deserves a node.

## Compression Through Reuse (Example)

**Without reuse** (3 separate nodes):
```
node: "api-auth-needs-session"
node: "websocket-auth-needs-session"
node: "cron-auth-needs-session"
```

**With reuse** (1 node + 3 edges):
```
node: "session-handler" — Session lifecycle manager
edge: api/auth.py --requires--> session-handler
edge: websocket/auth.py --requires--> session-handler
edge: cron/auth.py --requires--> session-handler
```

Same information, one-third the tokens, and `session-handler` is now reusable.

## API Usage

### Creating a node
```
kg_put_node(
  level="project",
  id="kebab-case-id",
  gist="the insight itself",   # terse but complete (~15 words)
  touches=["file.py"],         # optional: related artifacts
  notes=["caveat or context"]  # optional: rationale, constraints
)
```

### Creating an edge
```
kg_put_edge(
  level="project",
  from="source-node-or-path",
  to="target-node-or-path",
  rel="relationship-type",
  notes=["optional context"]
)
```

Direct artifact references work without wrapping in nodes:
```
kg_put_edge(level="project", from="src/api/auth.py", to="src/session/handler.py",
            rel="requires-init", notes=["auth.validate() assumes session.current exists"])
```
