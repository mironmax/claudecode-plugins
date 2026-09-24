# 04 — Research cards

## Goal

A small, honest body of research notes that reads the 2026 agent-memory
literature against the conditions this plugin actually runs under, so design
decisions can cite evidence that applies instead of evidence that merely
sounds relevant.

## This system's conditions (the lens for every card)

- Memory is mostly declarative (decisions, facts, constraints, preferences,
  lessons), not procedural trajectories.
- A human steers and corrects in the loop; sessions are real work, not
  benchmark streams.
- Nodes are written once at the moment of insight and rarely rewritten;
  archival hides rather than deletes; maintenance runs out of the agent loop.
- One frontier model family is the primary consumer; long-lived personal
  store (months), a few thousand nodes across many projects.
- Retrieval channels: session-start preload, per-prompt recall, explicit
  search and reads; tool-level recall is being added.

## What to produce

`docs/research/` with one card per paper and a synthesis page.

Each card, one page at most:
- claim, in one sentence, with the key number;
- conditions: models, tasks/benchmarks, memory representation, who writes
  and when, update schedule, horizon;
- applicability to the conditions above: holds / partially / does not, and
  why, in two or three sentences;
- what it would change here, if anything, as a testable proposal;
- verification level: full read, abstract plus excerpts, or secondhand.

Starting corpus (extend with anything newer and relevant you find):
2605.12978 (consolidation degradation), 2604.14004 (memory transfer and
abstraction level in coding agents), 2606.04703 (continual experience
internalization: granularity, injection pattern), 2605.07164 (experience
utilization at decision points), 2609.26457 (recursive self-improvement with
a keep-if-better gate), 2608.27454 (skill evolution with a persistent wiki),
2609.11060 (environment-probing curation), 2606.05661 (continual learning
benchmark), 2510.04618 (agentic context engineering), 2606.30306 (survey of
persistent memory and governance).

The synthesis page names where the papers genuinely conflict, what condition
explains each conflict, and which open questions this plugin is positioned
to answer from its own logs.

## Constraints

- Quote numbers exactly and mark anything not read in full.
- No claims about this plugin's internals beyond what the repository shows.
- Neutral voice; no recommendations dressed as findings.

## Done when

Cards for the starting corpus plus any additions, and the synthesis page,
committed under `docs/research/`.
