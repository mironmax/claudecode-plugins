---
name: kg-capture
user-invocable: false
description: |
  Knowledge capture rules. Active every session — capture as you discover, not after.

  CAPTURE TRIGGERS: after any task, ask — did I learn something that took real effort?
    - Component connections discovered → edges
    - Why something works a certain way → notes on existing node
    - 10+ min debugging → root cause pattern, not just the fix
    - User corrected approach → the signal you missed (user level)
    - Same thing explained twice → reusable node
    - Architectural decision made → node with rationale in notes

  BEFORE CREATING: kg_search first — update existing nodes rather than duplicating.

  ENCODING — telegraphic style:
    Gist = one headline: subject + key fact. No filler, no hedging. ≤120 chars.
    "Storage safety: atomic writes + .prev backup. No git."  ← good (54 chars)
    If gist needs "and" to join two ideas → split: two nodes + one edge.

  GIST vs NOTES:
    Gist  = compressed fact, always visible — the headline (≤120 chars ideally)
    Notes = rationale, constraints, "why", step-by-step — read on demand
    Procedure steps, caveats, examples → notes, not gist.

  EDGE-FIRST: Can this be expressed as a relationship between existing things?
    If yes → edge, not a new node. Edges protect connected nodes from archival.

  LEVELS: user = preferences/principles/meta-patterns · project = codebase/decisions/ops
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
2. **Required effort to discover?** → If no, probably skip
3. **Would this help future sessions?** → If yes, capture it

## Choosing Node Granularity

A node should be atomic — one concept, one headline. If your gist uses "and"
to join independent ideas, split into two nodes with an edge.

The sweet spot: would you reference this concept from another context? If yes, it deserves a node.

## Telegraphic Encoding

Gists are telegrams, not essays. Strip all words that carry no information:

| Verbose | Telegraphic |
|---------|-------------|
| "When you are working with Docker containers and you need to edit files..." | "Docker file edit: chown→edit→chown-back. chmod -R 777 alone fails." |
| "It is important to always verify that each layer works before building on it" | "Debugging: verify each layer before building on it." |

Procedure steps belong in **notes**, not gist. The gist names the pattern; notes explain the how.

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
  gist="the insight itself",   # terse but complete
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
