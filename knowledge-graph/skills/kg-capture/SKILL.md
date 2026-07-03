---
name: kg-capture
user-invocable: false
description: |
  Knowledge capture rules. Capture mid-conversation, not after — context is cached,
  so a write costs almost nothing now but saves full re-derivation next session.

  Good moments to capture (as things happen, not at task end):
    - Opened files with no component node → a brief node now saves a re-read later
    - Discovered how two things connect → an edge, while the insight is fresh
    - Understood why something works a certain way → a note on the existing node
    - 10+ min debugging resolved → root cause node before moving on
    - User expressed a preference, style, or constraint → user-level node
    - User corrected your approach → capture what was missed, not just the fix
    - Explained something non-obvious → node before it scrolls away
    - Approach agreed with user → capture the methodology, not just the decision
    - Architectural decision made → node with rationale in notes
    - Context window feels deep → a good moment to check for anything unrecorded

  When reading a file with no component node, consider creating one.
    Gist = what it handles + what it does NOT (the exclusion is the skip signal).

  Before creating a node, a quick kg_search helps avoid duplicates — update if it exists.

  Gist = subject + key fact, ≤120 chars. Notes = rationale and steps.
  When something is a relationship between two existing things, an edge is better than a new node.
  Touches = precise pointers into files: path:line-range (+ short anchor) — a
  pointed touch saves a whole-file read next session.
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
  gist="the insight itself",           # terse but complete
  touches=["src/api/auth.py:42-60 (validate flow)"],  # optional: precise pointers
  notes=["caveat or context"]          # optional: rationale, constraints
)
```

### Creating an edge
```
kg_put_edge(
  level="project",
  from="source-node",
  to="target-node",
  rel="relationship-type",
  notes=["optional context"]
)
```

## Touches vs Edges — where does a file reference go?

- **Edges relate concepts** (node→node). **Touches locate concepts in files.**
- A touch is best as a precise pointer: `path:start-end` plus a short semantic anchor
  (`"config/prod.yaml:30-40 (upstream block)"`). Line numbers drift — the anchor keeps
  the pointer recoverable. A pointed touch lets the next session read 10 lines instead
  of the whole file.
- Don't create edges to file paths. If a file matters enough to relate to several
  concepts, **graduate it to a component node** (gist = what it handles + what it
  does NOT), then use normal edges.

## Cross-Level Edges

A project node may point up to a user-level node — this is how project work hooks into
cross-project doctrine:
```
kg_put_edge(level="project", from="this-projects-decision",
            to="user-level-principle", rel="applies")
```
Always store cross-level edges in the **project** graph (the user graph is always loaded,
so the reference resolves; the reverse direction does not).
