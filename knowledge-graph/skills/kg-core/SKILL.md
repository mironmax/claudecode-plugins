---
name: kg-core
user-invocable: false
description: |
  Knowledge Graph — persistent, granular, evolving memory.

  Part of memory initially arrives preloaded: a "KG MEMORY PRELOADED" block carrying the
  session_id and most important portion of memory.
  Then the full read is on you: kg_read(cwd="<project root>") comes
  before any work, whatever the task. Full read still brings up only most important memories.

  Memory is highly optimised and served in layers.
  Recall: if needed details are not in preload/read and there is a gist
  that points in the right direction, recall full node;
  Capture: at the moment of learning, once the dots connect;
  Name: the id names the SUBJECT in 3-5 words and the gist makes the claim —
  ids are load-bearing in search, so a sentence-shaped id is a retrieval cost;
  Connect rather than duplicate — an edge beats a new node;
  Search for more — the read/sync shows the top of graph,
  not all of it. In a rich graph the fact you need is often
  buried under fresher work; search reaches every tier.
  Endorse at wrap-up: kg_useful(ids) on the ≤5 nodes that demonstrably
  changed this session's outcome. That credit is what keeps a node alive —
  it is never earned by being read, only by being named at the end.

  Mechanics live in the kg_* tool descriptions; operations in /kg-ops.
---

# Knowledge Graph Reference

## Session start

The preload block is a compact core — the top-scored slice of both graphs, not
the whole. The one full `kg_read(session_id)` renders everything it dropped
without repeating what is already shown (preloaded gists collapse to id-only
anchors). The session_id from the preload — or from the first kg_read — goes on
every later kg_* call: it keeps one session, includes the project graph in
searches, and avoids minting spurious sessions.

No preload block (Desktop sessions, server still warming up):
`kg_read(cwd="<project root>")` returns the full graph plus your session_id.
Connection refused usually means the server is starting — retry after a few
seconds; persistent trouble is a /kg-ops matter.

Resuming an earlier conversation: `kg_sync(session_id)` picks up what other
sessions wrote meanwhile.

## Recall

After the full read, scan gists for anything touching the task and read those
nodes in depth — several per call: `kg_read(session_id, ids=[...])`. Lean
toward reading more: a wrong guess costs one call, missing context costs the
task. Node reads return the node's own edges — each a crumb to the next hop —
and reading an archived node promotes it and surfaces its orphaned neighbours.

Three tiers as a graph grows: **active** (id + gist on the surface),
**archived** (id + edges as crumb trails), **orphaned** (invisible — only
search reaches them).

### Searching below the surface

What kg_read renders is the surface of the graph, not its extent. A young,
sparse graph has little beneath — searching it rarely pays. But a graph grown
through months of work holds far more than any render shows, and the fact you
need now is often exactly the one buried under fresher nodes. That is not a
defect — it is how a living memory works — and kg_search is the instrument
built for it, reaching all tiers at once.

So before asserting an assumption, before re-deriving from files, when the work
enters territory this project has plausibly visited before: search first. The
cost is one call. And retrieval is only half the value — surfacing a node at the moment it is truly needed feeds
its usefulness score, which is what makes it more prominent. A graph that is
only ever written to silts up; one that is searched keeps its most-needed
facts on top.

## Capture

Capture mid-conversation, at the moment of learning — a write costs almost
nothing now and saves a full re-derivation later. Worth capturing: whatever
took effort to obtain (how the project is wired, how the thing work, project how tos,
decisions and their rationale, preferences and constraints, where is what),
and whatever gives future sessions navigation — a component node for files you
explored: what the cluster handles and what it does not.

Placement is the craft:

- One concept per node; a gist joining two ideas with "and" wants to be two
  nodes and an edge.
- Name things once. When a thing recurs across sessions — a service, a
  feature, a saga — one node owns it; session and event nodes record what
  changed and edge to the owner instead of re-describing it. A gist that
  re-explains what the graph already names should have been an edge —
  re-description is how an entity smears across a dozen narratives until
  search can no longer tell which node owns it.
- **The id names the subject; the gist makes the claim.** Three to five
  kebab-case words. `a-401-in-a-log-is-an-event-not-a-state` is a gist wearing
  an id's clothes; the subject was `auth-401-is-an-event`. This is not
  tidiness: search weights the id ×3 and matches it for the recall gate, so
  every extra word is another token that can drag the node into an unrelated
  prompt. Seven words or more is refused at the write boundary.
- **No dates — not in the id, not in the gist.** A date says when something
  was written down, never what it is. A dated node is nearly always a node
  minted per EVENT where the graph wanted one enduring node for the subject:
  keep `ambient-recall-audit`, update it as each audit lands, and let its
  `touches` point at the current handover doc. The document's own filename
  carries the date, which is where a date is actually useful.
- Gist = subject + key fact, telegraphic. Rationale and steps go in notes.
- Touches are precise pointers — `path:line-range (short anchor)` — so the
  next session reads ten lines instead of the file.
- Cross-level edges (project decision → user principle) are legitimate; store
  them in the project graph.
- Ids can be changed later, but only with `kg_rename_node` — it carries the
  edges, the history and the cross-level references. Writing a new node and
  deleting the old one is not a rename; it is a quiet amputation.

Documents point INTO memory, never the other way round. A letter, a handover
or a README naming a node id is a stationary artifact taking a dependency on a
moving one: memory keeps evolving — nodes get renamed, merged, archived — and
the doc silently rots. Write what the doc means in its own words, and let the
node carry the doc's path in `touches`. That direction survives both.

Other memory systems (CLAUDE.md, auto-memory) are supplementary — their
exclusion rules govern their own storage. When in doubt, record here.

## Levels

**user** — cross-project wisdom: preferences, principles, patterns that
travel. **project** — this codebase and its operations: architecture,
decisions, component nodes, discoveries.

## Subagents

Subagents receive no preload. The dispatching session sizes memory into each
prompt: exploration and mining agents get kg_* instructions plus the
session_id; ordinary task agents get the few relevant gists pasted; narrow
mechanical tasks get nothing.

## Operations

Server lifecycle, reconnect after restart, backup, troubleshooting — the
/kg-ops runbook covers it; don't improvise server management from here.
