---
name: kg-maintain
user-invocable: false
description: |
  Knowledge graph maintenance. Tend the garden — regular, light care keeps it healthy.
  Not a separate task: woven into every session, every capture.

  GARDEN RHYTHM — three modes, applied as needed:
    Water   (routine): after each task, glance at 2–3 recently-touched nodes.
                       Are their gists still accurate? Any note worth adding?
    Prune   (when dense): merge duplicate nodes, shorten verbose gists (→ notes),
                       remove stale touches, delete edges to removed concepts.
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

  ARCHIVAL POLICY: Never delete archived nodes unprompted — archival is reversible.
  Only delete when content is factually wrong and cannot be fixed by updating.
---

# Maintenance Reference (Detailed)

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
- **Archived nodes** — leave them alone. Automatic. Do not clean up archived nodes.
- **Orphaned nodes** — invisible in kg_read; searchable via kg_search. Automatically
  deleted after 365 days without recall. To rescue: read an adjacent archived node —
  the promotion chain will surface its orphaned neighbors back to archived.
- **Verbose gists** — if a gist is a paragraph, move procedure/details to notes. Gist = headline only.

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
