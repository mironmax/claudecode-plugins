# 12 — Adoption: positioning, proof, distribution

## Goal

People who would benefit from kg-memory find it, understand within ten
seconds what it does differently, install it on the first try, and see
evidence that it changes their work. The memory itself is not in question
here. What holds back adoption is everything around it.

## Why

The survey in [`docs/adoption/`](../../docs/adoption/) read 17 popular
memory projects for how they got adopted. kg-memory has 6 stars. The
projects that grew all had:

- one number or one picture on the first screen;
- an install that finishes inside the agent;
- a borrowed or existing audience that made one concentrated spike, which
  GitHub Trending then compounded.

Their weak points are the same across the field:

- hidden background token spend;
- process bloat;
- self-measured benchmarks;
- unsourced social proof;
- cloud upsell.

kg-memory's design answers most of these. Its public surface says none of
it. "Multi-harness", "local" and "no API key" are now every competitor's
pitch. They are no longer enough to stand apart.

## Positioning

**The claim to own:** coding-agent memory that learns lessons, not logs. The
model that did the work writes down what it learned, while it still has the
context. There is no second model watching your sessions. Claude Code and
Codex share the memory, and it forgets what stopped mattering.

Candidate lines for the first screen, for the maintainer to choose from or
rewrite:

- "Your coding agents learn lessons, not logs. One local memory for Claude
  Code and Codex."
- "Memory without a second model watching: the agent writes the lesson
  while it still has the context."
- "What Claude Code learns, Codex knows. Local, no API key, and it forgets
  what stopped mattering."

**The proof to lead with** is a measured cost and a measured effect, stated
with caveats (item 3). The engineering record backs it up: the evaluation
harness, the follow-through report, the formal pass and the research cards.
This is the honest-builder story the field now rewards, after the benchmark
disputes.

## Work items, in order

Items 1–3 come before any launch. A post that sends people to a page without
a promise, or to an install that fails on their machine, uses up the one
chance to be noticed.

1. **First-run reliability beyond Linux.**
   - Install from scratch on a clean macOS machine: `uv tool install
     kg-memory && kg setup`, then a Claude Code session and a Codex session
     that each receive the preload.
   - Fix what breaks. Decide whether macOS needs an autostart service.
     Today the server starts on demand through `kg mcp` and the SessionStart
     hook.
   - State in the README exactly which platforms are verified.
   - If CI can run on macOS, add `macos-latest` to `tests.yml` for the unit
     suite.
2. **The first screen.**
   - Rewrite the README's opening as name, one line of promise, one line of
     proof, a 30–60 second video (Codex learns something, Claude Code recalls
     it unprompted, end on the visual editor), then install.
   - Move the current feature description below the install.
   - Make the GitHub description, the topics (add `antigravity`), the PyPI
     summary and `marketplace.json` say the same thing.
   - Replace the 3.1 MB GIF with a compressed video or a lighter GIF.
3. **One honest headline number.**
   - From the existing logs, measure what the memory costs a session:
     preload size, recall injections and capture calls, in tokens per session.
     Measure what it saves where that can be observed: re-reads avoided, or
     follow-through by route (`python -m eval --transcripts`).
   - Publish the method and the caveats next to the number.
   - The with/without benchmark under "Later" in the roadmap is the stronger
     proof. Its design still needs to be agreed.
   - A LongMemEval-S run is a judgement call. It is the benchmark the field
     cites, but it measures recall over chat haystacks, not capture-time
     memory in coding work. If it is run, state that in the result.
4. **Install that finishes inside the agent.**
   - When the `kg` command is missing, the SessionStart hook
     (`hooks/kg-autostart.sh`, and its equivalent in `kg-agy.py`) currently
     tells the agent to pass the install command to the user.
   - Point it at `INSTALL.md` instead. The agent can then run the install
     with the user's consent, as the agent install guide already describes.
     A plugin-only install from `/plugin marketplace add mironmax/kg-memory`
     then reaches a working memory in one session.
5. **Free listings.**
   - Work through [channels](../../docs/adoption/channels.md): awesome-claude-code
     (web form), the Claude community directory, the official MCP registry
     (`mcp-name` line in the README and a `server.json`), Glama, mcp.so,
     awesome-mcp-servers and awesome-codex-plugins.
   - Check what claudemarketplaces.com and claude-plugins.dev already show.
   - Each listing describes the project; none of them sells it.
6. **An honest comparison page.**
   - Write `docs/comparison.md`: kg-memory alongside claude-mem, agentmemory,
     mem0 and basic-memory, each in its own words.
   - Cover where each is the better choice, and when not to use kg-memory.
   - It answers the first question every launch thread will ask.
7. **Launch week.** Follow the [Reddit plan](../../docs/adoption/reddit-launch.md):
   - r/ClaudeCode first: a post that leads with a finding.
   - An X thread and a dated blog write-up of the same finding in the same
     week. Submit the write-up, not the repository, to Hacker News.
   - Pitch the finding to one or two newsletters (see channels).
8. **Community surface, sized to the people who exist.**
   - Set up Discussions categories for Q&A and "show your graph".
   - Add `good first issue` and `agent-ready` labels on a few real issues.
   - Open no Discord until people ask for one. An empty server reads worse
     than none.
9. **Release notes as signal.** Each tagged release gets a short,
   human-readable note: one sentence on what changed for the user, with the
   CHANGELOG entry linked. Frequent releases are visible activity.

## Decisions for the maintainer

- **Name.** "Knowledge graph memory" collides with the official MCP reference
  memory server and at least three other projects. The options are:
  - keep `kg-memory` and make the tagline carry the identity;
  - give the project a distinct brand and keep `kg-memory` as the package.

  The repository was just renamed, so a second rename has a cost.
- **A host runtime.** Becoming the memory provider inside a host agent
  (Hermes, OpenClaw) was the single largest lever in the survey: Hindsight
  went from 20k to 40k stars after it. It means a new harness adapter. It
  belongs with "More harnesses" under "Later", not in this item.
- **A headline benchmark** (item 3): LongMemEval-S, the with/without coding
  benchmark, or a token-cost measurement only.

## Constraints

- Never claim what is not measured. Every number on the first screen links
  to its method, and every platform claim names what was verified.
- None of the tactics in the survey's "patterns to avoid": no token, no logo
  wall, no relabelled download counts, no "#1", no bought stars, no attack
  posts.
- No telemetry to measure adoption. Use what is public: PyPI downloads, the
  repository's traffic and referrers, and issues and discussions from
  people other than the maintainer.

## Done when

- Items 1–6 are shipped.
- The launch in item 7 has run.
- A short note in `docs/adoption/` records what each channel brought in:
  stars, PyPI downloads and referrers in the two weeks after.
