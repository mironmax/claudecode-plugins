---
name: kg-maintain
user-invocable: true
description: |
  Knowledge graph maintenance. Tend the garden — regular, light care keeps it healthy.
  Not a separate task: woven into every session, every capture.

  GARDEN RHYTHM — three modes, applied as needed:
    Water   (routine): after each task, glance at 2–3 recently-touched nodes.
                       Are their gists still accurate? Any note worth adding?
    Prune   (when dense): merge duplicate nodes, shorten verbose gists (→ notes),
                       split oversized nodes, remove stale touches, delete edges to removed concepts.
    Fertilize (on use): when a node proves valuable, connect it to newly-discovered
                       related nodes. One new edge makes a node far more durable.

  AFTER CAPTURE: when you save a node, immediately ask —
    - Do any adjacent nodes now need updating?
    - Is this a duplicate of something existing? Merge if so.
    - Does this node's gist still fit, or did context shift?

  REACTIVE TRIGGERS (act immediately, mid-conversation):
    Uncertainty (spinning wheels, deja vu, about to search) → kg_search first.
    About to assume something → check KG; if missing, capture the assumption.
    User correction → update the stale node before continuing.
    Node just proved useful → add one edge to current context.
    Gist feels vague after using it → sharpen while context is live.

  Archival is reversible — leave archived nodes alone unless content is factually wrong
  and can't be fixed by updating. Deletion is a last resort, not routine cleanup.
---

# Maintenance Reference (Detailed)

## When Invoked Directly (/kg-maintain)

Run a focused maintenance pass in this order:
1. `kg_read(cwd)` — check health stats: orphan %, avg edges/node, size warning
2. **Always**: scan all gists against the current kg-capture standard — tighten any that exceed it, regardless of graph size
3. **Always**: spot-check notes on recently-touched nodes — rewrite any that have grown chaotic or redundant (see "Notes Hygiene" below)
4. If graph is large or has size warning → **Prune**: merge duplicates, split oversized nodes, remove stale touches
5. After pruning → **Fertilize**: connect nodes clarified during pruning, add missing edges
6. **Water** throughout: update any gist that feels stale given what you just read

Announce findings: "Graph health: N nodes, E edges, O% orphans. Running [prune|fertilize|water] pass."
Report what changed: nodes merged, gists tightened, edges added.

## What a Healthy Graph Looks Like

A healthy graph is a mesh of connections, not isolated facts. Most nodes participate
in at least one edge. Health stats show this at a glance:
- Low orphan rate — most nodes connected
- Reasonable edge density — linked but not over-connected
- Mix of levels — user patterns inform project decisions

## Maintenance Operations

When auditing the graph with kg_read:
- **Disconnected nodes** — appear in no edges. Connect them with edges if appropriate. Only delete if truly orphaned AND factually incorrect.
- **Duplicates** — overlapping gists or IDs. Merge: keep/update richer one, delete the other.
- **Outdated knowledge** — about removed code or old decisions. Update/improve the node with current state rather than deleting.
- **Broken edges** — pointing to renamed or removed concepts. Update the edge target, or kg_delete_edge if the relationship no longer exists.
- **Archived nodes** — automatic process, leave them be. They're memory traces, not clutter.
- **Orphaned nodes** — invisible in kg_read; searchable via kg_search. Automatically
  deleted after 365 days without recall. To rescue: read an adjacent archived node —
  the promotion chain will surface its orphaned neighbors back to archived.
- **Verbose gists** — if a gist exceeds the current kg-capture standard, tighten it. Move procedure/details to notes. Gist = headline only. See "Gist Hygiene" below.
- **Bloated notes** — notes that have grown into a changelog or contain contradictions. Rewrite to current truth only. See "Notes Hygiene" below.
- **Oversized nodes** — single node spanning multiple responsibilities. Split and link. See "Oversized Node Detection" below.

## Gist Hygiene

No extra tool needed — gists are visible in the main `kg_read` output. Scan them all.

**Standard:** check the current kg-capture skill for the active gist length target. Any gist exceeding that limit needs tightening — this applies to all nodes regardless of graph size or when the node was created. Old nodes do not get a pass on the current standard.

**Signals:**
- Gist uses "and" to join independent ideas → split the node
- Gist restates what the ID already says → delete the redundant part
- Gist contains a procedure step → move to notes
- Gist longer than the current target → cut; move excess to notes

**Action:** rewrite to subject + key fact only. Everything else belongs in notes or an edge.

## Notes Hygiene

Notes are hidden by default, so they don't inflate the visible graph — but they accumulate silently. A note added in session 1, amended in session 5, and partially updated in session 20 becomes contradictory and bloated. Periodic rewrite is the fix.

**When to rewrite notes (not just append):**
- Node has been touched 3+ times and notes feel like a log of changes rather than current truth
- Notes contain contradictory statements ("X is required" and "X is optional" both present)
- Notes repeat information already in the gist
- Notes contain a chain of "actually…" corrections — collapse to the final truth
- A node covers two separable concerns — split and redistribute notes

**How to rewrite:**
1. Read the full notes block as-is
2. Extract the current truth: what invariants hold now? What constraints? What rationale?
3. Discard the history (corrections, "turns out", "actually") — keep only the conclusion
4. Rewrite as a clean set of bullets, each a standalone fact

Notes are not a changelog. They're a compressed memo to a future session that has no other context.

**When to do a notes pass:**
- During any /kg-maintain invocation: spot-check 3–5 nodes with most touches
- Any time you read a node's notes and feel confused before feeling informed
- After a long debugging session where the same node was updated repeatedly

## Oversized Node Detection

When gist + notes together are very large, or a single node spans multiple distinct responsibilities:

**Signals:**
- A single node covering two separable concepts → split into two nodes linked by an edge
- An edge with a very long notes field → distill to one sentence; create a node for the reasoning if it matters

**Actions:**
1. **Split node** — when a node covers two separable concepts, split and link with an edge (`relates_to`, `part_of`, `depends_on`).
2. **Promote to CLAUDE.md** — if notes contain operational procedures or project conventions, they belong in CLAUDE.md rather than the KG. Remove them from the node once moved.

This pass is independent of graph size — a small graph can have a bloated node.

## Operational Safety

### Tool result size limit
Claude Code persists tool results over ~50K chars to disk — model sees only a 2KB preview.
kg_read output is the full graph as text. If kg_read shows a size warning (>40K chars):
1. Review nodes for staleness, duplicates, disconnected entries
2. Delete or merge low-value nodes
3. KG_MAX_TOKENS default is 4000 per graph level — two graphs ≈ 32K chars, safe under 50K limit

### Project renames
When project folder renamed, graph slug changes. Server handles via alias detection.
If project graph is unexpectedly empty, check ~/.knowledge-graph/projects/ for old name.

### Server restart
Server can be safely restarted (kg-memory restart). Validates PIDs, uses setsid,
drains connections, write-through persistence means no data loss.

## First Session in a Project

When project graph is empty:
1. Don't document the whole codebase. Capture what surprises you.
2. 2-5 foundational nodes for major components, connected by edges.
3. Add knowledge organically as you work.
4. Quality over quantity — every node earns its place through reuse.
