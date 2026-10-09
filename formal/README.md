# Formal-methods pass over the knowledge-graph server

A trial of the "model → counterexample → reproduce → fix" loop on this
server's concurrent and stateful parts. Lean 4 does the modelling. This
directory holds the models, the real-code reproductions and the evidence.
Nothing here changes the server; fixes land as ordinary releases, each
with a regression test in `knowledge-graph/server/tests/`. Findings with
file:line references are in [FINDINGS.md](FINDINGS.md).

**Current status:** F1, F2, F4, F9 and F10 were fixed in v0.9.44
(`tests/test_races_and_guards.py`). F3 is settled as a policy: a forced
reload lets disk win, logs what it drops, and the two paths that triggered
it without anyone asking (the visual editor, cross-site pages) are gone.
F11 was found later and fixed in v0.10.1; F5 and F8 in v0.10.2.
F12 (a fork taking an unbound session) was fixed in v0.11.0. F6 is fixed on
the development branch (project-bound editor subscriptions, modelled first).
F13–F16, found by a second pass on delivery (paged reads and Antigravity's
queue), are fixed there too. F7 remains open.

## Open work

A second pass started on the parts added since 0.11 that no model covered
yet. Delivery (F13–F16) and the editor's subscriptions (F6) are done. Still
to be modelled and reproduced, each in its own directory:

- `compaction/`: a Lean model of the per-tick archive / refill / rebalance /
  orphan sequence and the root cause of F7's one-tick churn;
- `cross-session/`: the push of other sessions' writes and `kg_sync`
  (each change pushed once, never to its writer, nothing falling between the
  push window and the sync watermark);
- `lifecycle/`: `kg start` serialisation, one server per storage directory,
  the start-error record, and the `kg mcp` rule that only a refused
  connection is retried;
- `credits/`: one vote per node per session, credit caps, idempotent
  reconciliation of repeat credits on load, maintenance sessions;
- `chore-dispatch/` (pass tier, runners) and `budget-notices/` (once per
  level per window under racing hooks).

The models are not yet run in CI; `run_all.sh` prints results but never
fails, so a CI job would need expected outcomes per model and reproduction.

## Method

1. **Model** one risky concern: a state machine, a thread interleaving, or a
   reference-resolution rule. Each model is small (60–200 lines of Lean). Its
   steps are the code's atomic regions, and each one cites the `file:line` it
   mirrors.
2. **Search for counterexamples.** Each model is an executable Lean program
   that runs an exhaustive breadth-first search (or, for rename, an exhaustive
   enumeration) within stated bounds. It prints the shortest labelled trace to
   a bad state.
3. **Reproduce** every counterexample against the real Python code. Only the
   parts that decide *whether* something runs (gauges, the Claude binary) are
   stubbed. The rest is real: locks, state files, `GraphPersistence.save`,
   REST/WS routing. A counterexample that does not reproduce is not reported
   as a bug.
4. **Fix**, in the server, as a normal release. Candidate fixes were checked
   in the models first. One of them was incomplete, and the model caught it
   (see F5).

Not everything went through Lean. F4 and F10 came from the questions the
modelling raised, such as "which threads touch this dict?". F7 used a
randomized search over the real compactor instead of a model.

## Results

| # | Finding | How found | Status | Severity (judgement) |
|---|---|---|---|---|
| F1 | Chore dispatch decides on an unlocked state snapshot: double dispatch inside `min_interval`, lost count | Lean BFS | fixed, 0.9.44 | medium-low |
| F2 | A failed write-through still clears `dirty`, so the write is lost at shutdown while `put_node` reports success | Lean BFS | fixed, 0.9.44 | medium |
| F3 | Forced reload discards unsaved in-memory state | Lean BFS | reproduced | medium-low |
| F4 | Saver thread dies permanently on a session-dict race (no lock, no `try`) | reading + stress | fixed, 0.9.44 | medium |
| F5 | Rename re-points or drops cross-level edges (4 variants) | Lean enumeration | fixed, 0.10.2 | medium |
| F6 | The visual editor never receives project-level live updates | reading; fix checked in Lean | fixed, unreleased | low |
| F7 | Compaction and refill churn: a node archived on one tick is re-promoted on the next | randomized search over the real compactor | reproduced | low |
| F8 | A fork shares its live parent's KG session; the parent's hooks resolve to another session | Lean BFS | fixed, 0.10.2 | medium-low |
| F9 | Cross-site GETs with side effects (`reload=true`, `session_bootstrap`) | reading + test | fixed, 0.9.44 | low |
| F10 | The maintenance pass tier skips the "never rename a node a live session holds" rule; `_live_seen` fails open | reading | fixed, 0.9.44 | medium-low |
| F11 | A write built on a stale or partial view drops another session's note, or notes the writer never read | Lean BFS + reproduction | fixed, 0.10.1 | medium |
| F12 | A fork takes over a KG session no hook has bound yet | Lean BFS + reproduction | fixed, 0.11.0 | medium-low |
| F13 | A paged node read counts a node as read before its notes go out; a write can then drop them | Lean BFS + reproduction | fixed, unreleased | medium-low |
| F14 | The other-sessions notice marks nodes seen before its (queued or refused) Antigravity reply is delivered | Lean BFS + reproduction | fixed, unreleased | medium-low |
| F15 | A full read replayed after a checkpoint marks preloaded anchors seen | Lean BFS + reproduction | fixed, unreleased | low |
| F16 | A checkpoint with a full delivery queue refuses the fresh preload | Lean BFS + reproduction | fixed, unreleased | low |

Several suspicions were checked and found to hold. They are listed in
FINDINGS.md so they need not be re-checked.

## Honesty notes

- "No counterexample" means none **within the stated bounds**: ≤3 threads,
  ≤9 clock ticks, ≤3 Claude sessions, 2 ids, and so on. No unbounded
  proofs were attempted.
- A model is a claim about the code. Each step cites the lines it mirrors,
  so check those citations when reviewing.
- Severity is a judgement from reading how the code is used. None of it was
  measured in production.

## Running

```bash
# once: Lean toolchain (pinned in lean-toolchain) + the server's venv
curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y --default-toolchain none
../knowledge-graph/cli/kg-dev version     # builds ../knowledge-graph/server/venv on first use

PYTHON="$(realpath ../knowledge-graph/server/venv/bin/python)" ./run_all.sh
                      # ~1 min; rewrites <concern>/evidence/*.log
```

Each model can also be run on its own, e.g. `lean --run rename/lean/Rename.lean`.
While a finding is unfixed, its reproduction prints `BUG`/`FAIL`; after the
fix it prints `PASS`. The F2 reproduction keeps printing `FAIL B, C`: those
two checks expect a reload to keep unsaved memory, which F3's policy
declines.

## Layout

```
<concern>/lean/*.lean    model + bounded search (executable: lean --run)
<concern>/repro/*.py     deterministic reproduction against the real code
<concern>/evidence/*.log output of the run at f17349a, before any fix:
                         the counterexamples as found (concurrent-writes and
                         sessions were re-run with the F11 and F12 fixes)
```
