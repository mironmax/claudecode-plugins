# Adoption research

How popular open-source agent-memory projects got adopted, and what that
means for kg-memory. Written for roadmap item
[12](../../roadmap/tasks/12-adoption.md). It reads 17 projects for marketing,
positioning and distribution, not for their algorithms. The research date is
2026-10-07.

| Document | What it holds |
|---|---|
| [Landscape](landscape.md) | The field, what the popular projects have in common, what people criticise, and kg-memory's strengths and weaknesses against them |
| [Channels](channels.md) | Where coding-agent tools get found, how to get listed, and the proof those channels reward |
| [Reddit launch plan](reddit-launch.md) | Why a showcase post comes before an AMA, which communities, the value proposition, and a checklist |

## In one paragraph

The projects that grew all had:

- one number or one picture on the first screen;
- an install that finishes inside the agent;
- one concentrated spike, made by a borrowed or existing audience (a
  creator, a newsletter, a viral gist, a host runtime) and compounded by
  GitHub Trending.

Hacker News Show HNs of repositories almost never worked in this category.
"Multi-harness", "local" and "no API key" are now everyone's pitch.

The field's recurring weaknesses are:

- background token spend;
- process bloat;
- self-measured benchmarks;
- unsourced social proof;
- cloud upsell.

kg-memory's design answers most of these, and its engineering record is
unusually honest. But it has no audience, no headline number, a generic name,
no listings and an install verified only on Linux. The order of work follows
from that: reliability, the first screen, one measured number, the free
listings, then one launch week.

## Method

Three research passes covered:

- coding-agent plugins: claude-mem, agentmemory, Beads, context-mode;
- funded platforms: mem0, Letta, Zep and Graphiti, Cognee, Supermemory,
  Hindsight;
- indie and local-first projects, and distribution channels: ai-memory,
  tigerless-labs/agent-memory, engram, basic-memory, codebase-memory-mcp,
  Understand-Anything, obsidian-second-brain.

Sources were READMEs read raw, the HN Algolia API, star-history and
Trendshift pages, the npm downloads API, and Discord's public invite counts.

Reddit and Medium refused automated fetches, so posts there are known only
through pages that cite them. Figures not checked at the source are marked
*secondhand*. Claims about kg-memory are checked against this repository.
