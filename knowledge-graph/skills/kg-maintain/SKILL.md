---
name: kg-maintain
user-invocable: true
description: |
  Knowledge graph maintenance — a bounded, resumable pass that pays down the
  graph's DEBT line (rendered after HEALTH in every kg_read). Run it when
  invoked, when DEBT shows HIGH, or as a dispatched maintenance subagent.

  The same work also arrives in a smaller shape: a CHORE, one category and
  one or two named targets, dispatched by the server while someone is
  working. Chores carry their own runbook in their prompt — this skill is
  the full pass, and the place the chore memory is described.

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

`kg_progress(session_id, task_id="maintain", level=<target>)` → prior state,
plus `_trail`: the last ~20 stamps, newest last. Read it before working the
list. It carries what earlier passes did and — where they said so — what they
looked at and DECLINED. A merge already weighed and refused twice does not
need weighing a third time; a cursor left behind says where to resume.

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
        state={"entities_consolidated": N, "gists_tightened": N,
               "ids_renamed": N, "edges_added": N, "merges": N,
               "notes_rewritten": N,
               "declined": ["<what you considered and did not do, and why>"]})

Do not supply a timestamp. You have no clock, and passes that were asked for
one demonstrably invented it — measured on the live graphs, every stamp was a
round hour, one landed six days in the future and one a year in the past,
while staleness is the leading term of the debt score that decides which
graph gets tended next. The server writes `last_ts` itself.

`declined` is the half that compounds. A pass that examined a merge and
decided against it has done real work; without recording it, the next pass
repeats the examination and reaches the same answer. One short line per
rejection — what, and the reason in a few words. Empty list when there was
nothing to refuse.

## 4 — Report

One compact summary: debt before → after, counts per category, anything
found-but-deferred (it seeds the next pass's cursor — and belongs in
`declined` above, where the next pass will actually see it).

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
    kg_progress task "maintain" — counts plus a `declined` list of what you
    considered and refused — and report counts.
    Do not invent facts; sharpen wording, not meaning. ~25 kg_* calls max.

# Chores — maintenance in a unit that fits a working day

A pass is the right shape for a scheduled run and the wrong shape for a
laptop. Measured over the 45 days after the systemd dispatcher was armed: 28
passes across 12 graphs, one per graph per ~19 days, against a staleness
horizon of 14. Its gate is a conjunction of five conditions whose terms fight
each other — the quota gauge is only fresh while someone is working, which is
exactly when the usage gate is shut, and the machine is suspended the rest of
the time.

A **chore** is the same work re-cut: ONE category, one or two targets the
server names up front, and no orientation at all. The server computes every
debt factor already, so choosing what to do is free; what remains is five or
six tool calls. It is dispatched on the signal that actually correlates with
opportunity — a prompt arriving — as a detached headless agent, so it costs
the live session no context.

Three rules shape which nodes a chore may touch, and each is a bug that would
otherwise be:

- **Never RENAME a node a live session is holding.** The session keeps the
  old id, so the rename turns its next read into a NOT FOUND. Rewriting a
  gist does not break it (the meaning is preserved, only the phrasing ages)
  and an added edge is invisible to it — so the seen-set bars renames and
  merely demotes the other kinds. A blanket veto looks safer and is not: a
  session that did the loud full read holds every active node, so it would
  refuse every chore in the project actually being worked on.
- **Never a node an earlier pass declined.** The `declined` lines are
  decisions; re-proposing them is the work the trail exists to prevent.
- **Never the same category forever.** Categories rank by the weight the debt
  formula gives them, so gists lead — but three chores of one kind yield to
  the next non-empty category, or thirteen long ids rot while gists get
  tightened two at a time.

One guard outranks the three: **never keep rewriting the same node.**
Repeated in-place rewriting of stored text is the one maintenance pattern
measured to degrade memory (docs/research/cards/
2605.12978-consolidation-degradation.md). The server stamps every real gist
change on the node (`_gist_ts`, bounded, carried by rename), and a node
rewritten more than twice in 30 days is skipped by every kind that writes
text back in place — gist, notes, and anchor, which re-sends the gist.

Two kinds exist only as chores, because the server has to prepare them:

- **anchor** — a `touches` entry no longer resolves. The chore cannot see the
  filesystem, so the server finds where the file went first (the rename git
  recorded, or the ONE same-named file in the project) and the chore only
  applies it: `moved` → the replacement as given, `gone` → drop the entry,
  `ambiguous`/`unknown` → leave it and say so. It never writes a path it was
  not given.
- **lift** — two or more instance-level nodes (dated records, sessions,
  reviews, status snapshots) that share a lesson. The chore writes that
  lesson once as a principle node — a claim that holds outside the episodes,
  notes saying when it matters and what goes wrong, touches to the evidence —
  and edges each supporting member to it with `instance-of`. It never edits
  or deletes the members; once the principle carries the lesson, archiving
  them is the scorer's job. One episode is not a principle: fewer than two
  supporting members means nothing is written.

Chores never do the two judgement-heavy categories: entity consolidation and
duplicate merges stay in the full pass, where there is context to weigh them.

# When the full pass happens

Not when debt says so — debt cannot answer this question. Chores pay down
exactly the terms debt counts, and the deficit factor bottoms out at the
formula's own constant: a groomed, fully-active graph caps at **0.25**, under
the 0.3 the scheduled dispatcher selects on. Left on debt alone, a
well-chored graph would never see a pass again, and the two structural
categories would never happen on it at all. A health metric says the state is
correct; it never says there is work left.

So the pass is triggered by **time since the last stamped pass** (default 21
days) on a graph in use — and paid for out of the **weekly surplus**:

    pace = seven_day_pct / (100 × fraction of the 7-day window elapsed)

`pace ≤ 1.0` means the week is running under the burn it would need to finish
at 100%, so quota is on course to expire unspent. The 5h gauge is what the
day's own work needs and is protected hard (a pass needs it under 40%); the
weekly allowance is the one routinely left over, and that is what funds the
expensive tier. The test is self-adjusting, which is why no day-of-week rule
appears anywhere: a real working day early in the week puts usage above the
line and the pass waits, while a quiet week drifts further under it each day,
so firings concentrate near the reset by arithmetic. An absolute 7d ceiling
(70%) backstops it, because at 6.5 days elapsed the linear line sits at 93%
and would otherwise wave through a nearly spent week.

Operations — enabling it, the config, the log — live in `/kg-ops`.

# The maintenance memory (`level="maintain"`)

Chores accumulate craft, and it is kept in a third graph: `maintain.json`,
one per machine, shared by every chore in every project. Isolated by
construction — absent from the preload, from `kg_read`'s graph render, from
search, and from the debt survey — so a gardening lesson can never surface as
prompt recall. Read it with `kg_read(session_id, level="maintain")`; the
dispatcher renders it into every chore prompt so no chore has to spend a call
fetching it.

What belongs there is the craft of maintenance, never the subject matter of
the graph being gardened:

    rename-keeps-prompt-terms: A rename that drops the term prompts actually
    use costs recall even when the new id is objectively better.

    thin-gists-rarely-have-honest-edges: An unconnected node with a one-line
    gist usually needs sharpening first — the edge becomes obvious afterwards.

The trail (`kg_progress` task "chore" / "maintain") records what was DONE and
DECLINED per graph; the maintain graph records what was LEARNED, across all
of them. A lesson is written only when a future chore would act on it
differently, and reinforced by writing to its existing id rather than minting
a near-duplicate. Learning nothing is the normal outcome of a routine chore.

During a full pass, read the maintenance memory in step 0 and, at step 4,
consider whether the pass earned a lesson — the same bar.

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
- **dangling touch(es)** — `touches` entries that no longer resolve against
  the project root (relative), home (`~`) or the filesystem (absolute). Paid
  down by anchor chores, which only apply what the server found.
- **lift cluster(s)** — groups of two or more instance-shaped active nodes
  sharing a touched file, an edge, or neighbours and vocabulary: episodes
  whose shared lesson has not been written down once. Paid down by lift
  chores.
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
