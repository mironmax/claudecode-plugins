# Research cards

Notes that read the 2026 agent-memory literature against the conditions this
plugin actually runs under. The goal is that design decisions cite evidence
that applies here, not evidence that merely sounds relevant. Written for
roadmap item [04](../../roadmap/tasks/04-research-cards.md).

Start with the [synthesis](synthesis.md): where the papers conflict, what
explains each conflict, and which questions this plugin's logs can answer.

## The lens

Every card judges applicability against these conditions:

- Memory is mostly declarative (decisions, facts, constraints, preferences,
  lessons), not procedural trajectories.
- A human steers and corrects in the loop; sessions are real work, not
  benchmark streams.
- Nodes are written once at the moment of insight and rarely rewritten.
  Archival hides nodes rather than deleting them, and maintenance runs
  outside the agent loop.
- One frontier model family is the primary consumer. The store is personal
  and long-lived (months), with a few thousand nodes across many projects.
- Retrieval channels are a session-start preload, per-prompt recall, and
  explicit search and reads; tool-level recall is being added.

## Card format

Each card is at most a page:

- **Claim**: one sentence, with the key number quoted exactly.
- **Conditions**: models, tasks, memory representation, who writes and
  when, update schedule, horizon.
- **Applicability**: holds, partially, or does not, and why.
- **What it would change here**: a testable proposal, or nothing.
- **Verification**: how much of the paper was read. "Full read of the main
  body" means every section before the references. Partial reads list the
  sections read. Any internal inconsistency found in a paper is recorded
  there.

Numbers are quoted as the paper states them. Where a paper reports "+x%" for
what its own tables show is a difference in percentage points, the card says
so.

## Cards

| arXiv | Paper | Applicability | Read |
|---|---|---|---|
| [2605.12978](cards/2605.12978-consolidation-degradation.md) | Useful memories become faulty when continuously updated | partially | main body |
| [2604.14004](cards/2604.14004-memory-transfer-abstraction.md) | Memory transfer learning in coding agents | partially | main body |
| [2606.04703](cards/2606.04703-experience-internalization.md) | Continual experience internalization | does not (directly) | main body |
| [2605.07164](cards/2605.07164-experience-utilization.md) | Experience utilization at decision points (ExpWeaver) | partially | main body + App. A, D, E.1 |
| [2609.26457](cards/2609.26457-recursive-self-improvement-gate.md) | Recursive self-improvement with a keep-if-better gate (AIDE²) | partially | main body |
| [2608.27454](cards/2608.27454-wikiskill-persistent-wiki.md) | WikiSkill: skills co-evolved with a persistent wiki | partially | §1–5.2 |
| [2609.11060](cards/2609.11060-environment-probing-curation.md) | Environment-probing curation | partially (closest match) | main body |
| [2606.05661](cards/2606.05661-continual-learning-bench.md) | Continual Learning Bench | partially | main body |
| [2510.04618](cards/2510.04618-agentic-context-engineering.md) | Agentic Context Engineering (ACE) | partially | main body + App. A.5 |
| [2606.30306](cards/2606.30306-always-on-agents-survey.md) | Always-on agents survey | partially | selected sections |
| [2605.26302](cards/2605.26302-agent-aging.md) | Agent aging (AgingBench) — *addition* | partially | main body |
| [2605.06527](cards/2605.06527-stale-implicit-conflict.md) | STALE: implicit memory invalidation — *addition* | partially | §1–4.2, §5–6 |
| [2606.02461](cards/2606.02461-agentcl-controlled-streams.md) | AgentCL: controlled task streams — *addition* | partially | main body |

The three additions were found through the survey's references and chosen
because each bears directly on one of this phase's work items: maintenance
effects (task 03), stale anchors and superseded nodes (task 03), and
evaluation design (task 02).

## Sources and limits

- Texts were read from the arXiv HTML renderings of the versions named on
  each card. Figure-only numbers were read from the rendered PDF where a card
  quotes them (ExpWeaver Figure 2); otherwise figure-only results are
  described, not quoted.
- Nothing here re-runs any paper's experiments.
- Statements about this plugin cite files in this repository. Where a card
  proposes a measurement, it names the log or file that would supply it; none
  of those measurements has been run yet.
