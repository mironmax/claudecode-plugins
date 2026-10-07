# Channels: where a coding-agent memory gets found

The places people browse for Claude Code, Codex and MCP tools as of
2026-10-07, how a project gets listed in each, and what kg-memory needs for
it. "Evidence" says whether anything shows that the channel drives installs.
A figure a channel reports about itself is marked *self-reported*.

## Listings

| Channel | How to get listed | Fit for kg-memory | Evidence |
|---|---|---|---|
| Own marketplace (`.claude-plugin/marketplace.json`) | No submission: users run `/plugin marketplace add owner/repo` | Already in place (`maxim-plugins`) | Used by the fastest growers in the survey |
| [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) (55.2k stars) | Web issue form only, one resource at a time, submitted by a human. The repository must be 14 days old or have 100 stars. A one-line description that "describes, not sells" | Eligible now | High traffic; not measured |
| Claude community directory ([claude-plugins-community](https://github.com/anthropics/claude-plugins-community)) | Form at clau.de/plugin-directory-submission. Pull requests to the repository are closed automatically | Eligible | Users must add the marketplace manually; no figures |
| Anthropic directory portal (claude.ai/directory/manage, opened 2026-09-25) | Needs a paid plan and `claude plugin validate --strict`. Each version is scanned automatically, and a human reviews new listings. MCP connectors must be remote ([docs](https://code.claude.com/docs/en/plugins/publish)) | Unclear: kg-memory's server is local over stdio. Check what the portal accepts for a hook-driven plugin with a local server before relying on it | New; no data |
| Anthropic official marketplace | Partner contact only ([docs](https://code.claude.com/docs/en/plugins/anthropic-marketplaces)) | Closed to independent projects | The default Discover tab |
| [claudemarketplaces.com](https://claudemarketplaces.com/) | Appears to crawl automatically | Check the page appears and reads correctly | 380k+ monthly visitors (*self-reported*) |
| [claude-plugins.dev](https://claude-plugins.dev/) | Registry with its own CLI; how to get listed is not stated | Check | Shows download counts |
| [Official MCP registry](https://modelcontextprotocol.io/registry/about) | `server.json`, then `mcp-publisher publish`. A PyPI package needs an `mcp-name: io.github.<user>/<server>` line in its README | The tools work without hooks, so the listing is honest if it says hooks come from the plugin | Indirect: aggregators such as PulseMCP pull from it |
| [Glama](https://glama.ai/mcp/servers) | "Add Server". It grades licence, quality and maintenance | After the registry | Sorts by usage and stars |
| [mcp.so](https://mcp.so/submit) | Form with the repository URL | After the registry | 266k monthly active users (*self-reported*) |
| [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) (95.9k stars) | Pull request | Eligible | Not measured |
| [skills.sh](https://skills.sh/) | Ranked automatically from installs by `npx skills add owner/repo` | Weak: the skills need the server and hooks | Strong for standalone skills |
| OpenAI plugin directory (ChatGPT and Codex) | Portal review. Lifecycle hooks and local stdio MCP are not accepted ([docs](https://developers.openai.com/plugins/deploy/submission)) | Closed to kg-memory's design; Codex users install from the marketplace | n/a |
| Codex community lists ([awesome-codex-plugins](https://github.com/hashgraph-online/awesome-codex-plugins)) | Pull request | Eligible | Not measured |
| Hermes and OpenClaw memory providers | An adapter in the host's memory-provider interface | Not supported today. This is the largest single lever in the survey (Hindsight), but it means a new harness | Hindsight went from 20k to 40k stars after its Hermes listing |

## Earned channels

| Channel | What worked for others | Note for kg-memory |
|---|---|---|
| GitHub Trending and Trendshift | Earned through star velocity over a day or a week. It compounds: claude-mem trended for 68 days | Needs a concentrated spike: all launch posts in one week, not spread out |
| Hacker News | A repository Show HN almost always scored 1–5 points in this category. A blog post with one concrete number scored 570 (context-mode) | Submit the write-up, not the repository |
| Reddit | claude-mem's launch was a Reddit post ("I built a context management plugin and it CHANGED MY LIFE") | See [the Reddit plan](reddit-launch.md) |
| X explainer accounts | Understand-Anything grew from threads by explainer accounts | Pitch the visual editor and the cross-harness moment |
| Newsletters | AlphaSignal on agentmemory ("3,000+ stars in ~3 days"); AI Coding Daily; Augmented Coding Weekly; swyx's AINews | Pitch a measured finding, not the tool |
| YouTube walkthroughs | A Better Stack video was an early accelerant for Understand-Anything | Requires a demo that works on the first try on macOS |
| DEV.to and personal blogs | These echoed every Trending spike; ai-memory's "walking it back" post set up its launch | A dated "what the logs say" post is the natural format |

## Proof that channels reward

These are the benchmarks memory tools cite:

- **LongMemEval-S** (agentmemory, agent-memory, ai-memory, Hindsight, mem0).
- **LoCoMo** (mem0, Zep). Its quality has been disputed.
- **Token savings against a baseline** (context-mode, codebase-memory-mcp).

Recall@k and QA accuracy are different measures. Reviewers now check which
one a claim uses. One independent controlled test of memory in coding agents
found 15–28% token savings, against the 80–95% that vendors claim
([*secondhand*, not peer-reviewed](https://medium.com/@mrsandelin/the-first-controlled-benchmark-of-ai-memory-in-coding-agents-8e0bb776d39e)).

Coding-specific memory benchmarks are only starting to appear
([DreamBench-SWE](https://arxiv.org/pdf/2608.20664)). An honest with/without
result on coding tasks is still an open niche.
