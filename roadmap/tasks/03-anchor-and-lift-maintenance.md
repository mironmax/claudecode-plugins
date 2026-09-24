# 03 — Anchor repair and lift maintenance

## Goal

Two new maintenance kinds, so the graph stays anchored to the world and
trends toward fewer, more general nodes:

- **anchor** — a node's `touches` entry no longer resolves (file moved,
  renamed or deleted). Repair it, or drop it when the thing is truly gone.
- **lift** — several instance-level nodes (a dated session record, a one-off
  review, a status snapshot) carry a lesson that is true beyond them. Write
  that lesson once as a principle node, point it at the evidence, and let the
  instances archive.

And one guard: **churn** — a node rewritten many times recently is left alone,
because repeated rewriting of the same content is the one maintenance
pattern known to degrade memory.

## Why

In observed graphs, a noticeable share of file anchors no longer resolve and
nothing repairs them; and a large share of nodes are instance-shaped (dated
ids, per-session records) — episodes stored in the tier meant for durable
knowledge. Principle-level knowledge transfers and lasts; instance-level
traces do not. Session notes remain the place for episodes; a principle node
touches them as evidence.

## Where things are

- `server/core/debt.py` — the DEBT factors and disk survey.
- `server/core/chores.py` — `pick_chore`, per-kind pools, prompt builders
  (`build_chore_prompt`, `build_pass_prompt`); the full-pass runbook lives
  beside it and mirrors `skills/kg-maintain/SKILL.md`.
- `server/mcp_http/chore_dispatch.py` — gating and spawning (unchanged here).
- `chores/settings.json` — the MCP-only allowlist chores run under. A chore
  cannot read the filesystem; an anchor chore needs the server to precompute
  candidates.
- `core/utils.py:node_id_has_date`, `node_id_words` — existing id heuristics.

## What to build

1. Server-side detection, deterministic and cheap:
   - dangling touches per node, resolved against the project root (from the
     graph's `_meta.project_path`) and home; for each, candidate new paths
     where they can be found without guesswork (same basename elsewhere in
     the project tree; `git log --follow --name-status` when the project is a
     git repository). Precompute; the chore only decides.
   - instance-shaped nodes: dated ids, session/review/status-snapshot shapes,
     with clusters formed from shared touches, shared edges and shared terms.
     A cluster is a lift candidate only when it has at least two members.
2. Two chore kinds wired into `pick_chore` with their own prompt sections:
   - anchor: given node + dangling entry + candidates, update `touches` via
     `kg_put_node`, or remove the entry; never invent a path.
   - lift: given a cluster, write one principle node (claim that holds
     outside the episodes; notes phrased as when it matters and what goes
     wrong; touches to the evidence), connect it, and leave archival to the
     scorer — no deletions from a chore.
3. Churn guard: skip nodes whose gist was rewritten more than a threshold
   number of times within a window, for every rewriting kind. The version
   counter in `_meta.versions` is not enough — it also bumps on promotion
   from the archive — so record gist rewrite timestamps on the node from now
   on (a small bounded list, like `_useful_ts`).
4. Both kinds report into the existing DEBT line as factors, printed raw like
   the others.

## Constraints

- The small unit stays non-destructive (no deletes), as all chores are today.
- A lift must cite at least two members as evidence; one episode is not a
  principle.
- Follow the chore conventions already in `chores.py` (server names targets,
  in-context sessions are respected, declined decisions are not re-proposed).
- Self-contained tests; suite green; CHANGELOG entry.

## Done when

Tests show: dangling detection on relative/absolute/`~`/`path:lines` forms,
candidate discovery that refuses ambiguous matches, cluster formation with
the two-member floor, the churn guard excluding a hot node, and both chore
prompts naming their targets.
