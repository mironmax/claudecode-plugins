---
name: kg-capture
user-invocable: false
description: |
  Knowledge capture rules. Capture mid-conversation, not after — context is cached,
  so a write costs almost nothing now but saves full re-derivation next session.

  CAPTURE CONTINUOUSLY — the moment something is confirmed, not at task end:
    - Opened files with no component node → create one immediately
    - Discovered how two things connect → edge, right now
    - Understood why something works a certain way → note on existing node
    - 10+ min debugging resolved → root cause node before moving on
    - User expressed preference, style, or constraint (even proactively) → user-level node
    - User corrected your approach → also capture the signal you missed, not just the fix
    - Explained something non-obvious (first time or repeated) → node before it scrolls away
    - Approach agreed with user → capture the methodology, not just the decision
    - Architectural decision made → node with rationale in notes
    - Context window feels deep → capture anything unrecorded before it's lost

  COMPONENT NODES: Read files with no KG node → create one.
    Gist = what it handles + what it does NOT (exclusion is the skip signal).

  BEFORE CREATING: kg_search first — update existing nodes rather than duplicating.

  ENCODING: Gist = subject + key fact, ≤120 chars. Notes = rationale/steps.
  EDGE-FIRST: relationship between existing things → edge, not a new node.
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

Two capture motivations — both valid:

**Navigation value** — file cluster has no component node yet, and a future session
would benefit from a read/skip signal. Capture even if the information is technically
recoverable from the files: the point is to avoid the re-read cost.

**Knowledge value** — something non-obvious, hard-won, or easily forgotten:
1. Recoverable from artifacts in <10s? AND no navigation gap? → Skip
2. Required real effort to discover? → Capture
3. Would this help future sessions avoid repeating work? → Capture

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
