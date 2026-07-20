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

1. **Oversized gists — up to 8, longest first.** Rewrite the gist as headline
   ≤300 chars (subject + key fact); move the displaced detail into notes —
   merge with what's there, discard no facts. Keep the node id stable.
2. **Unconnected active nodes — up to 5.** Batch-read them
   (`kg_read(session_id, ids=[...])`), then give each ONE meaningful edge to
   an existing node. No honest edge exists? Sharpen the gist instead — an
   unconnected but crisp node beats a fake edge.
3. **Duplicate merges — up to 3.** Overlap spotted during the scan: merge
   into the richer node (union of notes/touches), re-point the poorer node's
   edges (`kg_put_edge` new, `kg_delete_edge` old), then delete the empty
   shell. Verify overlap before merging — presumed duplicates often aren't.
4. **Notes hygiene — up to 3 nodes** (the most-revised ones you touched
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
        state={"last_ts": <unix now>, "gists_tightened": N,
               "edges_added": N, "merges": N, "notes_rewritten": N})

## 4 — Report

One compact summary: debt before → after, counts per category, anything
found-but-deferred (it seeds the next pass's cursor).

# Dispatching Maintenance as a Subagent

When a session sees DEBT HIGH but is mid-task, spawn a subagent instead of
context-switching. Subagents get NO preload — the prompt must carry:

    Run a knowledge-graph maintenance pass in <cwd>.
    First call kg_read(cwd="<cwd>") — the result includes your session_id
    and both graphs with DEBT lines. Then follow the /kg-maintain skill's
    "Maintenance Pass" runbook against the <level> graph: oversized gists
    (≤8), unconnected nodes (≤5), duplicate merges (≤3), notes hygiene (≤3),
    then verify, STAMP kg_progress task "maintain", and report counts.
    Do not invent facts; sharpen wording, not meaning. ~25 kg_* calls max.

# Reference: what the DEBT factors mean

- **oversized gist(s)** — active gists >300 chars; the documented
  compactor-stall root cause and the top-value fix.
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
