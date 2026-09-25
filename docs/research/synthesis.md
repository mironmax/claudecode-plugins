# Synthesis: where the papers conflict, and what this plugin can answer

This page reads the [cards](README.md#cards) together. It names the genuine
disagreements, identifies the condition that best explains each one, and
lists the open questions this plugin's own logs are positioned to answer.
Statements about the plugin are limited to what the repository shows; the
proposals are hypotheses to test, not results.

## Conflict 1 — Is abstraction good or harmful?

- **For abstraction:** in 2604.14004, abstract Insights transfer across
  coding benchmarks better than raw traces (0.560 vs 0.534 average Pass@3).
  In 2606.04703, principle-level experience survives repeated internalization
  and instance-level experience does not.
- **Against:** in 2605.12978, consolidated lessons degrade and raw episodes
  stay competitive; an episodic-only control is "competitive with" the
  consolidators tested, and raw trajectories top its Table 5 at 76.6. In
  2606.05661, full raw context beats dedicated memory systems.

**The explaining condition is how abstraction happens, not whether.** In
2604.14004, each memory is written once from one trajectory and never
touched again. 2605.12978's own control shows the same pattern: a one-pass
consolidation of the pool stays at the 100% ceiling, while streamed in-place
rewriting falls to 52.6–54% (figure and text differ). The harm appears when an abstraction is
repeatedly rewritten in view of earlier abstractions. A second condition is
the distance between where memory was made and where it is used.
2604.14004's gains are cross-domain, where raw specifics mislead (the
brittle-anchoring cases). 2605.12978 and 2606.05661 are in-distribution,
where specifics apply directly.

**For this plugin:** nodes are written once at the moment of insight, which is
the regime where abstraction helped. The `gist` and `notes` chores, however,
are in-place LLM rewrites, the operation 2605.12978 isolates as most
damaging. The user and project levels also differ along the second
condition: user nodes travel across projects, while project nodes are used
where they were made.

## Conflict 2 — Does continuously updated memory compound or corrode?

- **Compounds:** ACE (2510.04618) grows an itemized playbook online and
  reports gains. WikiSkill (2608.27454) patch-edits a wiki every iteration
  and reports gains.
- **Corrodes:** 2605.12978 reports every one of seven frameworks, ACE
  included, degrading from its peak in at least one setting. AgingBench
  (2605.26302) reports decline across all its scenarios. CL-Bench
  (2606.05661) puts ACE near the bottom on gain.

**Explaining conditions, in order of evidence:**
1. **Horizon.** WikiSkill runs eight iterations. ACE makes one online pass
   or up to five offline epochs. 2605.12978's decline shows up over hundreds
   of steps, and its curves rise first. A short study observes the rise and
   not the fall.
2. **Feedback quality.** ACE's gains shrink or reverse without reliable
   labels (FiNER +0.4 offline without labels; Dynamic Cheatsheet −3.7 online
   without labels). WikiSkill gates every skill change on a labelled
   validation split.
3. **Update operation.** Delta and patch edits beat whole-store rewrites
   (ACE ablation 70.3 vs 56.9). Yet 2605.12978's append-only variant, with
   the prior store visible, still reaches only 50.0 against 76.6 for raw
   trajectories.

This conflict is not fully resolved by any one condition. No study runs a
gated, delta-edited store over a months-long horizon with sparse labels,
which is this plugin's regime.

## Conflict 3 — Push memory to the model, or let the model pull it?

- **Selective:** ExpWeaver (2605.07164) finds agent-triggered retrieval
  beating both init-only and every-step injection. 2606.04703 finds
  state-aligned (step-wise) injection beating global injection.
- **Everything in context:** CL-Bench's best system is full-context ICL.
  ACE argues for long, detailed contexts and letting "the model decide what
  matters". This plugin's architecture bets on the whole active graph fitting
  one read (`ARCHITECTURE.md`, "Load everything by default"), with a compact
  core preloaded at session start.

**The explaining conditions are model strength and store size relative to
context.** In ExpWeaver, the strongest backbones rarely retrieve at all
(GPT-5.2: 0.00 retrievals per sample in most settings), so selectivity is
largely a weaker-model effect there. The step-wise result comes from 4B–8B
students. CL-Bench's full-context advantage holds while the history fits.
This plugin combines the two regimes: a bounded core preload (the whole
active graph one `kg_read` away), plus selective recall per prompt. Neither literature tests that
combination.

## Conflict 4 — Who should curate, with what evidence?

- Model-curated notes lose to raw context on the same model (CL-Bench, ICL
  Notepad vs ICL).
- A curator with read-only access to the world beats one limited to the
  trajectory (2609.11060), though within overlapping intervals.
- An imperfect LLM filter on writes performs like oracle feedback (AgentCL
  MemProbe ablation).

**The explaining condition is the evidence available to the curator at write
time.** Curators that see only the model's own trajectory inherit its errors.
Curators that can check claims against the environment, or against ground
truth, do better. Chores here see the graph through MCP only; task 03 adds
precomputed file evidence instead of live file access.

## A cross-cutting caution on measurement

AgentCL (2606.02461) shows that loosely related task streams compress
differences between memory designs. Both it and CL-Bench show that in-stream
gains (plasticity) can coexist with zero or negative stability and held-out
gain. A positive in-stream result is therefore weak evidence about long-run
value. That applies to results reported in the literature and to replay
results this plugin will produce (task 02).

## Open questions this plugin is positioned to answer from its own logs

The labels available are sparse endorsements (`useful.jsonl`), every recall
decision including silences (`recall.jsonl`), chore records
(`chores.jsonl`) and the progress trail with its `declined` lines, and the
storage root's git history. None of these supplies a stateless
counterfactual, so none can measure *gain* in CL-Bench's sense. That needs
the paired with/without benchmark listed under "Later" in the roadmap.

| # | Question | Bears on | Data |
|---|---|---|---|
| 1 | Do nodes rewritten more often get endorsed less per exposure? | Conflicts 1–2; task 03 churn guard | git history of gists; `useful.jsonl`; `recall.jsonl` |
| 2 | Are pushed routes (`preload`, `full_read`, `ambient`) or pulled routes (`search`, `read`) endorsed at a higher rate per exposure? | Conflict 3; task 01 | `useful.jsonl` `via`; `recall.jsonl` |
| 3 | Are instance-shaped nodes endorsed less than principle-shaped ones, and does the gap widen outside the project that wrote them? | Conflict 1 | `node_id_has_date`; `recall.jsonl` `project`; `useful.jsonl` |
| 4 | Do chores cause a maintenance shock: lower endorsement or recall of touched nodes after the chore? | Conflict 2; AgingBench | `chores.jsonl` timestamps; `useful.jsonl`; `recall.jsonl` |
| 5 | Where does a dug-up endorsement fail: store (archived), retrieval (active, unmatched), or use (already injected)? | AgingBench ladder; task 02 | `recall.jsonl`; `useful.jsonl`; git history for archive state |
| 6 | Do `declined` lines stop re-proposals? | WikiSkill's audit log | `chores.jsonl`; progress trail |
| 7 | When a node changes, are its neighbours revisited? | STALE Type II | git history; graph edges |
| 8 | How large is replay noise across disjoint time windows, before any variant is compared? | AIDE² gate; AgentCL | task 02 replay on the baseline variant |

Each row can be computed read-only against existing files. Row 8 should come
first, because it sets the smallest difference any other row can
meaningfully report.
