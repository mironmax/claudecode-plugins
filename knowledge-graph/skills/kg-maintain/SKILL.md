---
name: kg-maintain
user-invocable: true
description: |
  Knowledge graph maintenance — a bounded, resumable pass that pays down the
  graph's DEBT line (rendered after HEALTH in every kg_read). Run it when
  invoked, when DEBT shows HIGH, or as a dispatched maintenance subagent.

  Always-on reactive triggers (no pass needed, act mid-conversation):
    User correction → update the stale node before continuing.
    Node just proved useful → add one edge to current context.
    Gist feels vague after using it → sharpen while context is live.
    Just saved a node → check: duplicate? adjacent nodes need updating?

  Archival is automatic and reversible — leave archived nodes alone.
  Deletion is a last resort, only for the factually wrong and unfixable.
---

# The Maintenance Pass

One graph per pass, hard-bounded, checkpointed through `kg_progress` so a cut
session loses nothing. The DEBT line is both the trigger and the scoreboard:

    DEBT: HIGH (0.72) — 14 oversized gist(s), 7 unconnected, never maintained, active 4/7d

## 0 — Orient

`kg_read(session_id)` (dispatched subagent with no preload: `kg_read(cwd)`
first — the result carries your session_id). Read both DEBT lines; target the
higher-debt level unless the dispatch said otherwise. Announce:
"Maintenance pass: <level> graph, debt <score> — <factors>."

## 1 — Resume

`kg_progress(session_id, task_id="maintain", level=<target>)` → prior state.
If a previous pass left a cursor, continue where it stopped.

## 2 — Work the list (bounded per pass)

Work in this order — each category caps, so a pass ends instead of sprawling:

1. **Entity consolidation — exactly ONE smeared term** (only when the DEBT
   line lists any, as `term×count→hub`). Smearing is many nodes re-describing
   one entity in prose instead of edging to its owner — it is what makes
   project-central vocabulary useless to search. Confirm the named hub (or
   anoint a better undated node), move the durable entity facts satellites
   carry into the hub, then rewrite the worst satellites' gists to their own
   delta only and edge them to the hub. This is the biggest lever and may
   take half the pass — let the later caps shrink accordingly.
2. **Oversized gists — up to 8, longest first.** Rewrite the gist as headline
   ≤300 chars (subject + key fact); move the displaced detail into notes —
   merge with what's there, discard no facts. Leave the id alone here; ids
   have their own category below and their own tool.
3. **Id refinement — up to 5.** Ids are load-bearing: search weights them ×3
   and matches them for the recall gate, so a wrong name is a retrieval cost
   paid on every prompt. Two kinds of work here, and the second is the one
   worth showing up for:

   *Repair.* Ids carrying the claim instead of naming the subject
   (`a-401-in-a-log-is-an-event-not-a-state` → `auth-401-is-an-event`), and
   dated ids — a date says when a thing was written down, never what it is.
   A run of dated siblings (`…-week1-…`, `…-week2-…`) is the graph asking for
   ONE enduring node, updated in place, touching the current document; that
   is a merge, so propose it rather than renaming each in turn.

   *Refinement.* Naming is like categorising a growing archive — at the start
   you cannot know the right categories, and only after a body of work
   accumulates does the vocabulary the graph ACTUALLY uses become visible.
   Read a cluster of related nodes together and ask what they are collectively
   about, then rename toward that shared vocabulary so siblings read as
   siblings and the terms you really search for are the terms in the ids.

   Always `kg_rename_node(old_id, new_id)` — it carries every edge, the
   creation time, the endorsements, the version history and the cross-level
   references in project graphs that are not even loaded. Never put_node +
   delete_node: that is not a rename, it is a quiet amputation.

4. **Unconnected active nodes — up to 5.** Batch-read them
   (`kg_read(session_id, ids=[...])`), then give each ONE meaningful edge to
   an existing node. No honest edge exists? Sharpen the gist instead — an
   unconnected but crisp node beats a fake edge.
5. **Duplicate merges — up to 3.** Overlap spotted during the scan: merge
   into the richer node (union of notes/touches), re-point the poorer node's
   edges (`kg_put_edge` new, `kg_delete_edge` old), then delete the empty
   shell. Verify overlap before merging — presumed duplicates often aren't.
6. **Notes hygiene — up to 3 nodes** (the most-revised ones you touched
   above). Notes that read as a changelog ("actually…", contradictions,
   repeats of the gist) → rewrite to current truth only: clean standalone
   bullets, history discarded, conclusions kept.

Rules that bound every action:
- Never invent facts — when unsure, tighten wording, not meaning.
- Archived nodes stay untouched except promotions your edges cause.
- Roughly 25 kg_* calls is a full pass — stop there, checkpoint, report.

## 3 — Verify and stamp

Re-run `kg_read(session_id)`: the DEBT factors you worked should have
dropped. Then stamp — **mandatory, the stamp is what resets staleness; an
unstamped pass didn't happen**:

    kg_progress(session_id, task_id="maintain", level=<target>,
        state={"last_ts": <unix now>, "entities_consolidated": N,
               "gists_tightened": N, "ids_renamed": N, "edges_added": N,
               "merges": N, "notes_rewritten": N})

## 4 — Report

One compact summary: debt before → after, counts per category, anything
found-but-deferred (it seeds the next pass's cursor).

# Dispatching Maintenance as a Subagent

When a session sees DEBT HIGH but is mid-task, spawn a subagent instead of
context-switching. Subagents get NO preload — the prompt must carry:

    Run a knowledge-graph maintenance pass in <cwd>.
    First call kg_read(cwd="<cwd>") — the result includes your session_id
    and both graphs with DEBT lines. Then follow the /kg-maintain skill's
    "Maintenance Pass" runbook against the <level> graph: entity
    consolidation (ONE smeared term, if the DEBT line lists any), oversized
    gists (≤8), id refinement via kg_rename_node (≤5), unconnected nodes
    (≤5), duplicate merges (≤3), notes hygiene (≤3), then verify, STAMP
    kg_progress task "maintain", and report counts.
    Do not invent facts; sharpen wording, not meaning. ~25 kg_* calls max.

# Reference: what the DEBT factors mean

- **smeared: term×count→hub** — the term appears in that many nodes'
  id+gist across all tiers while an undated node (the hub) plausibly owns
  it. Consolidation turns prose mentions into edges so search can find the
  owner again.
- **oversized gist(s)** — active gists >300 chars; the documented
  compactor-stall root cause and the top-value fix.
- **long id(s)** — active ids over five words, or carrying a date. The id
  names the subject; the gist makes the claim. Fix with kg_rename_node.
- **unconnected** — active nodes in no edge; one honest edge makes a node
  far more durable (connectedness is 40% of the archival score).
- **untended Nd / never maintained** — days since the last stamped pass
  (saturates at 14; "never" counts as fully stale).
- **active N/7d** — distinct days with graph reads or tracked tool traffic;
  activity weights debt up (active graphs wear faster and repay sooner).

Debt formula and thresholds live in `server/core/debt.py` — deliberately
legible; the line's raw numbers let you sanity-check the verdict.

# Notes Hygiene (how to rewrite)

1. Read the full notes block; extract what holds NOW — invariants,
   constraints, rationale.
2. Discard the history ("turns out", "actually", superseded corrections).
3. Rewrite as standalone bullets — a compressed memo to a future session
   with no other context. Notes are not a changelog.

# Operational safety

- kg_read output is budget-guaranteed inline; a "degraded to fit" note means
  the graph carries more anchors/edges than the ceiling — a prune-pass cue.
- Project renamed? The graph slug follows via alias detection; if a project
  graph looks unexpectedly empty, check ~/.knowledge-graph/projects/ for the
  old name.
- Server restarts are safe (PID-validated, setsid, write-through persistence).
