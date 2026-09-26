# Formal-methods pass over the knowledge-graph server

A trial of the "model → counterexample → reproduce → fix" loop on this
server's concurrent and stateful parts. Lean 4 does the modelling. This
directory holds the models, the real-code reproductions and the evidence.
**Nothing here changes the server.** Fixes are left for a separate change.
Findings with file:line references are in [FINDINGS.md](FINDINGS.md).

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
4. **Fix** (not done here). Candidate fixes were checked in the models only.
   One of them was incomplete, and the model caught it (see F5).

Not everything went through Lean. F4 and F10 came from the questions the
modelling raised, such as "which threads touch this dict?". F7 used a
randomized search over the real compactor instead of a model.

## Results

| # | Finding | How found | Status | Severity (judgement) |
|---|---|---|---|---|
| F1 | Chore dispatch decides on an unlocked state snapshot: double dispatch inside `min_interval`, lost count | Lean BFS | reproduced | medium-low |
| F2 | A failed write-through still clears `dirty`, so the write is lost at shutdown while `put_node` reports success | Lean BFS | reproduced | medium |
| F3 | Forced reload discards unsaved in-memory state | Lean BFS | reproduced | medium-low |
| F4 | Saver thread dies permanently on a session-dict race (no lock, no `try`) | reading + stress | reproduced | medium |
| F5 | Rename re-points or drops cross-level edges (4 variants) | Lean enumeration | reproduced | medium |
| F6 | The visual editor never receives project-level live updates | reading | reproduced | low |
| F7 | Compaction and refill churn: a node archived on one tick is re-promoted on the next | randomized search over the real compactor | reproduced | low |
| F8 | A fork shares its live parent's KG session; the parent's hooks resolve to another session | Lean BFS | reproduced | medium-low |
| F9 | Cross-site GETs with side effects (`reload=true`, `session_bootstrap`) | reading + test | reproduced | low |
| F10 | The maintenance pass tier skips the "never rename a node a live session holds" rule; `_live_seen` fails open | reading | confirmed by reading only | medium-low |

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
# once: Lean toolchain (pinned in lean-toolchain) + server test deps
curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y --default-toolchain none
pip install -r ../server/requirements.txt httpx

./run_all.sh          # ~35 s; rewrites <concern>/evidence/*.log
```

Each model can also be run on its own, e.g. `lean --run rename/lean/Rename.lean`.
While the findings are unfixed, the reproductions print `BUG`/`FAIL`. After a
fix, the same script is the regression check.

## Layout

```
<concern>/lean/*.lean    model + bounded search (executable: lean --run)
<concern>/repro/*.py     deterministic reproduction against the real code
<concern>/evidence/*.log output of the last run_all.sh
```
