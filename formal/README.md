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
queue), F17–F23, from the dispatcher's pass tier, runners and budget notices,
F24–F26, from the server lifecycle, and F27–F32, from usefulness accounting,
are fixed there too. The compaction model pinned down F7's root cause; F7 and
F34 are fixed. The cross-session model found F35–F38; F35–F37 are fixed.
F33 (rebalance can cycle forever) and F38 (a hook reply lost to its timeout
still uses up the push) are reproduced and open: each fix is a choice of
behaviour, with the options in FINDINGS.md.

## Open work

The second pass covered every part added since 0.11: delivery (F13–F16), the
editor's subscriptions (F6), dispatch and budget notices (F17–F23), the
lifecycle (F24–F26), credits (F27–F32), the compaction tick (F7, F33, F34) and
cross-session awareness (F35–F38). Left:

- decisions on F33 and F38;
- running the models in CI. `run_all.sh` prints results but never fails, so a
  CI job would need expected outcomes per model and reproduction.

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
modelling raised, such as "which threads touch this dict?". F7 was first found
by a randomized search over the real compactor; the compaction model later
gave its root cause.

## Results

| # | Finding | How found | Status | Severity (judgement) |
|---|---|---|---|---|
| F1 | Chore dispatch decides on an unlocked state snapshot: double dispatch inside `min_interval`, lost count | Lean BFS | fixed, 0.9.44 | medium-low |
| F2 | A failed write-through still clears `dirty`, so the write is lost at shutdown while `put_node` reports success | Lean BFS | fixed, 0.9.44 | medium |
| F3 | Forced reload discards unsaved in-memory state | Lean BFS | reproduced | medium-low |
| F4 | Saver thread dies permanently on a session-dict race (no lock, no `try`) | reading + stress | fixed, 0.9.44 | medium |
| F5 | Rename re-points or drops cross-level edges (4 variants) | Lean enumeration | fixed, 0.10.2 | medium |
| F6 | The visual editor never receives project-level live updates | reading; fix checked in Lean | fixed, unreleased | low |
| F7 | Compaction and refill churn: a node archived on one tick is re-promoted on the next | randomized search; root cause by Lean enumeration | fixed, unreleased | low |
| F8 | A fork shares its live parent's KG session; the parent's hooks resolve to another session | Lean BFS | fixed, 0.10.2 | medium-low |
| F9 | Cross-site GETs with side effects (`reload=true`, `session_bootstrap`) | reading + test | fixed, 0.9.44 | low |
| F10 | The maintenance pass tier skips the "never rename a node a live session holds" rule; `_live_seen` fails open | reading | fixed, 0.9.44 | medium-low |
| F11 | A write built on a stale or partial view drops another session's note, or notes the writer never read | Lean BFS + reproduction | fixed, 0.10.1 | medium |
| F12 | A fork takes over a KG session no hook has bound yet | Lean BFS + reproduction | fixed, 0.11.0 | medium-low |
| F13 | A paged node read counts a node as read before its notes go out; a write can then drop them | Lean BFS + reproduction | fixed, unreleased | medium-low |
| F14 | The other-sessions notice marks nodes seen before its (queued or refused) Antigravity reply is delivered | Lean BFS + reproduction | fixed, unreleased | medium-low |
| F15 | A full read replayed after a checkpoint marks preloaded anchors seen | Lean BFS + reproduction | fixed, unreleased | low |
| F16 | A checkpoint with a full delivery queue refuses the fresh preload | Lean BFS + reproduction | fixed, unreleased | low |
| F17 | A timed-out Antigravity maintenance run leaves agy running | Lean BFS + reproduction | fixed, unreleased | medium-low |
| F18 | A configured `codex_bin`/`antigravity_bin` does not pin its runner under auto | enumeration + reproduction | fixed, unreleased | medium-low |
| F19 | An unwritable chore state file fails open: repeated dispatches | Lean BFS + reproduction | fixed, unreleased | low-medium |
| F20 | A carried five-hour gauge reading passes as fresh | reproduction | fixed, unreleased | low-medium |
| F21 | A runner command that raises wedges dispatch until restart | Lean BFS + reproduction | fixed, unreleased | low |
| F22 | The dispatch lock re-decides on stale config and clock | Lean BFS + reproduction | fixed, unreleased | low |
| F23 | An Antigravity budget notice describes a window that already reset | Lean BFS + reproduction | fixed, unreleased | low |
| F24 | A start that runs out of time leaves its server untracked under a start-error record nobody clears | Lean BFS + reproduction | fixed, unreleased | medium-low |
| F25 | A stale pid file is trusted for whatever process now holds the pid | Lean BFS + reproduction | fixed, unreleased | low |
| F26 | `kg stop`/`restart` don't hold the start lock: a start during a stop leaves nothing serving | Lean BFS + reproduction | fixed, unreleased | low |
| F27 | A rename lets a session vote twice for one node | Lean BFS + reproduction | fixed, unreleased | low |
| F28 | A maintenance rename resets the node's activity time to 0 | Lean BFS + reproduction | fixed, unreleased | medium-low |
| F29 | A server restart forgets that a session is maintenance | Lean BFS + reproduction | fixed, unreleased | medium-low |
| F30 | A maintenance tidy turns the author's own next case into a note credit | Lean BFS + reproduction | fixed, unreleased | low |
| F31 | A crash keeps a vote but loses its ledger entry | Lean BFS + reproduction | fixed, unreleased | low |
| F32 | An `instance-of` edge in the maintain graph credits a user lesson | reading + reproduction | fixed, unreleased | very low |
| F33 | Rebalance can swap the same nodes forever, rewriting the graph every tick | Lean enumeration + reproduction | reproduced, open (policy) | low-medium |
| F34 | A node stays archived although the whole graph fits under the fill ceiling | Lean enumeration + reproduction | fixed, unreleased | very low |
| F35 | A rename, an unchanged re-put or a promotion is pushed as another session's write | Lean BFS + reproduction | fixed, unreleased | low |
| F36 | A session's own rename hides another session's change from `kg_sync` and the push | Lean BFS + reproduction | fixed, unreleased | low |
| F37 | Antigravity: a queued and an inline reply both carry the same change | Lean BFS + reproduction | fixed, unreleased | very low |
| F38 | A hook reply lost to the hook's one-second timeout still uses up the push and marks the change seen | Lean BFS + reproduction | reproduced, open | low |

Several suspicions were checked and found to hold. They are listed in
FINDINGS.md so they need not be re-checked.

## Honesty notes

- "No counterexample" means none **within the stated bounds**: ≤3 threads,
  ≤9 clock ticks, ≤3 Claude sessions, 2 ids, ≤3 graph nodes, and so on. No unbounded
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
                      # ~30 min, most in the compaction searches; rewrites <concern>/evidence/*.log
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
