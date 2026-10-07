# Landscape: how agent-memory projects got adopted

A survey of the most-starred open-source memory projects for AI agents, read
for positioning, proof and distribution rather than algorithms. Researched
2026-10-07. Star counts come from GitHub search on that day. Every other
figure is cited below, and anything not checked at the source is marked
*secondhand*. Reddit and Medium refused automated fetches, so posts there are
known only through other pages that link to them.

## The field at a glance

### Memory plugins for coding agents

| Project | Stars | Created | First-screen claim | Install | What drove growth |
|---|---:|---|---|---|---|
| thedotmack/claude-mem | 97.6k | 2025-08 | "Persistent memory compression system built for Claude Code"; GIF beside a live star chart; 32 README translations | `npx claude-mem install`, or two `/plugin` commands | Reddit launch post, then 68 days on GitHub Trending (#1 overall 2026-02-03) |
| rohitg00/agentmemory | 29.2k | 2026-02 | "#1 Persistent memory for AI coding agents based on real-world benchmarks"; stat pills "95.2% R@5", "92% fewer tokens", "0 external DBs" | `npx` wizard; `INSTALL_FOR_AGENTS.md` | Rode Karpathy's LLM-Wiki gist (author's fork has 1.7k stars), then the AlphaSignal newsletter ("3,000+ stars in ~3 days") |
| gastownhall/beads | 27.7k | 2025-10 | "A memory upgrade for your coding agent"; no number, founder authority | `brew install beads`, then `bd init` | Steve Yegge's blog and the Gas Town saga (HN 354, 403 points) |
| mksglu/context-mode | 25.6k | 2026-02 | "The other half of the context problem"; "315 KB becomes 5.4 KB. 98% reduction." | two `/plugin` commands | A blog post with one concrete number: 570 points on HN |

### Funded memory platforms

| Project | Stars | Category noun | Proof | Funding | Distribution lever |
|---|---:|---|---|---|---|
| mem0ai/mem0 | 66.8k | "memory layer" | arXiv paper; LoCoMo 92.5, LongMemEval 94.4 (README) | YC S24, $24M | Show HN 201 points (2024); AWS Strands; Claude Code and Codex plugins that need a platform key |
| vectorize-io/hindsight | 46.7k | "agent memory that learns" | arXiv paper; LongMemEval-S 94.6% on its own benchmark site | parent company seed $3.6M | Native memory provider in Hermes Agent; "60+ integrations"; went from 20k to 40k stars in 45 days |
| getzep/graphiti | 31.5k | "temporal knowledge graph", now "context layer" | arXiv paper; DMR and LongMemEval | YC W24 | MCP server; credits it with "hundreds of thousands of weekly users" |
| topoteretes/cognee | 31.5k | "AI memory platform" | arXiv paper; BEAM, with candid caveats | $7.5M seed | Hackathons, its own category subreddit (r/AIMemory), Claude Code and Codex plugins |
| supermemoryai/supermemory | 31.1k | "memory and context engine" | "#1 SOTA" claims; a "parody" 99% post that backfired | about $3M seed | Founder-led X; one plugin per harness |
| letta-ai/letta | 25.0k | "LLMs as operating systems", then "machines that learn" | MemGPT paper; LoCoMo filesystem result | $10M seed | 363-point HN launch (2023); DeepLearning.AI course |

### Indie and local-first projects

| Project | Stars | Created | Position | What drove growth |
|---|---:|---|---|---|
| Egonex-AI/Understand-Anything | 85.5k | 2026-03 | "Graphs that teach > graphs that impress"; live interactive demo | X explainer threads, a Better Stack video, #1 weekly on Trendshift |
| DeusData/codebase-memory-mcp | 46.0k | 2026-02 | "99% fewer tokens", arXiv preprint, supply-chain badges | #1 on GitHub Trending (2026-06-17), then a wave of tutorials |
| akitaonrails/ai-memory | 9.0k | 2026-05 | Cross-vendor handoff; credits the LLM Wiki | The author's own audience, and a "walking it back" teardown of a rival |
| Gentleman-Programming/engram | 7.1k | 2026-02 | "One brain. Local or cloud." | The creator's YouTube channel (about 123k subscribers, *secondhand*) and a Discord of about 13k |
| eugeniughelbur/obsidian-second-brain | 4.7k | 2026-03 | "Your vault is the memory. Claude, Grok Bot, Codex – same brain." | The LLM-Wiki and Obsidian wave; dense topics |
| basicmachines-co/basic-memory | 4.1k | 2024-12 | "Never re-explain your project to your AI again." | Early MCP mover; steady growth through many integration channels |
| tigerless-labs/agent-memory | 2.4k | 2026-09 | "Claude Code and Codex share one store. No API key."; caveated LongMemEval-S with p-values | An existing lab portfolio (a sibling repo has 9.2k stars) and an org X account; no PyPI release yet |

tigerless-labs/agent-memory is the closest in pitch to kg-memory: local, a
shared store for Claude Code and Codex, no API key, and a maintenance layer.
It reached 2.4k stars in five weeks without a launch post that could be
found. Its growth came from an audience the lab already had.

## What the popular ones have in common

1. **One number or one picture on the first screen.** context-mode leads
   with "98%". agentmemory leads with "95.2% R@5" and "92% fewer tokens".
   codebase-memory-mcp leads with "99% fewer tokens". Understand-Anything
   leads with a live demo. Beads is the exception, and it had Steve Yegge's
   name instead.
2. **Install inside the agent, then "restart and done".** All four coding
   plugins ship their own `.claude-plugin/marketplace.json`, plus an `npx` or
   `brew` one-liner. None is in Anthropic's official marketplace, which is
   partner-only.
3. **A borrowed or existing audience made the spike.** Every one traces to a
   creator's following, a newsletter, a viral gist, or a host runtime. A
   Show HN of the repository alone almost never worked: 1–5 points for nearly
   every project here. A blog post with one concrete claim did work
   (context-mode, 570 points).
4. **GitHub Trending compounds whatever starts the spike.** Repeated #1 days
   produced vendor articles of the "X hits N stars" kind and a long tail of
   tutorials.
5. **Being the memory inside a host is the biggest lever.** Hindsight went
   from 20k to 40k stars after becoming Hermes Agent's native memory
   provider.
6. **Multi-harness is now table stakes.** Since early 2026 the claims "every
   agent", "17 platforms", "20+ harnesses" and "Claude Code and Codex share
   one store" have become the default pitch. They no longer differentiate.
7. **"Local, no API key, no database" has become a common pitch as well.**
   What still separates a project is proof that the memory changes the work,
   and a cost the user can see.

## What the criticism says

The public complaints repeat across projects, and each one marks an opening:

- **Hidden token spend from a background observer.** claude-mem issues
  report $17 in 3 hours, 76M tokens in a day, and an estimated 649M tokens
  ([#1742](https://github.com/thedotmack/claude-mem/issues/1742),
  [#2643](https://github.com/thedotmack/claude-mem/issues/2643)).
- **Daemon and process bloat.** Orphaned chroma-mcp processes and a 157 GB
  store (claude-mem). "It got slower with each release": an "I replaced
  Beads" Show HN got 84 points.
- **Self-measured or unreproducible benchmarks.** These include Zep vs Mem0
  ("Lies, Damn Lies, & Statistics"), Letta vs Mem0, and MemPalace's 100%
  LongMemEval from tuning on the test set. Recall@k is often presented as if
  it were answer accuracy. After MemPalace, reviewers check the baselines.
- **Unsourced social proof.** context-mode shows a "users 652.6k+" badge that
  sums npm downloads, and a "used at" wall of 18 company logos that all link
  to `#`.
- **Upsell and trust.** claude-mem has a hosted-observer trial at install and
  a crypto token "officially embraced by the creator". Supermemory's plugins
  were first Pro-only. Mem0's OpenMemory "local" mode still calls OpenAI.
  Zep deprecated its Community Edition.
- **Security slips found by reviewers.** In agentmemory, reviewers found
  plaintext bearer tokens, a port bound to 0.0.0.0, and state written into
  the git tree. AkitaOnRails withdrew his recommendation five days after
  making it.

## kg-memory against the field

Strengths that matter for positioning:

| Strength | Why it matters against the field |
|---|---|
| The model writes the lesson while it works; there is no second model watching the session. Background maintenance is off by default and gated on quota when on. | The most common complaint against the leader (claude-mem) is background token spend. kg-memory can show that cost instead of asserting it. |
| Memory is curated and forgets: archival by recency, connectedness and endorsement. | Most pitches say "remembers everything". "Remembers what was worth learning" is a distinct claim, and a believable one for long-lived stores. |
| Local, MIT licensed, no API key, no cloud tier, no telemetry. Memory is kept on uninstall. `kg setup` backs up every file it edits and asks before each change. | These are the trust failures people complain about elsewhere, answered by design. |
| A rare engineering record: a retrieval evaluation harness, follow-through measurement, research cards, a Lean formal pass that found real concurrency bugs, and an output style benchmarked blind. | After MemPalace, honest and caveated evidence reads as credible. Few memory projects can show any of this. |
| Depth of harness integration: per-file recall, including Codex shell reads; recovery after compaction; fork-safe sessions; budget notices from each harness's own quota. | This makes a strong demo, but it is invisible in a feature list. |
| `INSTALL.md` written for agents, plus `kg doctor` and `kg uninstall`. | This matches agentmemory's `INSTALL_FOR_AGENTS.md` and context-mode's `ctx-doctor`. |
| A visual graph editor. | A graph picture was the whole hook for Understand-Anything. |

Weaknesses, in order of cost to adoption:

| Weakness | Evidence |
|---|---|
| No audience and no social proof. | 6 stars and 1 fork. A web search finds one third-party listing, under the old repository name. |
| No headline number on the first screen. | The README opens with a feature description and a 3.1 MB GIF. The −27% output-token figure belongs to the earlier v2 output style, not to memory. |
| A generic name that collides with others. | "Knowledge graph memory" is also the name of the official MCP reference memory server and of kg-mcp, memory-kg and hilyfux/knowledge-graph. The plugin is called `knowledge-graph` in the `maxim-plugins` marketplace. |
| Not listed anywhere people browse. | Not in awesome-claude-code, the Claude community directory, the official MCP registry, Glama, mcp.so or awesome-mcp-servers. |
| Linux first. | macOS has no autostart service; the server starts on demand through `kg mcp`. macOS and Windows are not verified in CI. Most Claude Code users on Reddit are probably on Macs (*inference*). |
| Installing the plugin alone is not enough. | Since 0.12.0 the plugin needs the `kg` command, so `/plugin install` without `uv tool install kg-memory` gives a session that only says how to install. Codex adds a manual `/hooks` trust step. |
| Docs written for the careful reader. | Precise and caveated (a strength later), but the first ten seconds read as a feature specification, not as pain, promise and proof. |
| No community surface in use. | Discussions and the wiki are enabled. There is no X account, no blog and no newsletter presence. |
| Positioning that is now common. | "Claude Code and Codex share one memory" is also tigerless-labs' headline, and multi-harness is every competitor's. |

## Patterns to avoid

Each of these drew public criticism that outlasted the gain: a crypto token,
an unsourced logo wall, downloads relabelled as users, "#1" claims, a
benchmark parody, attack posts about rivals, a cloud upsell at install, and
bought stars (now audited by star-timestamp checks).

## Sources

**Coding plugins**

- **claude-mem:** [README](https://raw.githubusercontent.com/thedotmack/claude-mem/HEAD/README.md) ·
  [star history](https://www.star-history.com/thedotmack/claude-mem/) ·
  [Trendshift](https://trendshift.io/repositories/15496) ·
  [HN item for the Reddit launch post](https://hn.algolia.com/api/v1/items/45676686)
- **agentmemory:** [README](https://raw.githubusercontent.com/rohitg00/agentmemory/HEAD/README.md) ·
  [AlphaSignal](https://alphasignalai.substack.com/p/how-agentmemory-works-and-how-to) ·
  [LLM Wiki v2 gist](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)
- **Beads:** [README](https://raw.githubusercontent.com/gastownhall/beads/HEAD/README.md) ·
  [HN 111](https://news.ycombinator.com/item?id=46075616) ·
  [Gas Town HN 354](https://news.ycombinator.com/item?id=46458936) ·
  [Maggie Appleton's essay, HN 403](https://news.ycombinator.com/item?id=46734302) ·
  ["I replaced Beads", HN 84](https://news.ycombinator.com/item?id=46487580)
- **context-mode:** [README](https://raw.githubusercontent.com/mksglu/context-mode/HEAD/README.md) ·
  [HN 570](https://news.ycombinator.com/item?id=47193064) ·
  [blog post](https://mksg.lu/blog/context-mode) ·
  ["Help spread the word" issue](https://github.com/mksglu/context-mode/issues/134) ·
  [stats.json](https://cdn.jsdelivr.net/gh/mksglu/context-mode@main/stats.json)

**Funded platforms**

- **mem0:** [README](https://raw.githubusercontent.com/mem0ai/mem0/main/README.md) ·
  [TechCrunch funding](https://techcrunch.com/2025/10/28/mem0-raises-24m-from-yc-peak-xv-and-basis-set-to-build-the-memory-layer-for-ai-apps) ·
  [Show HN 201](https://news.ycombinator.com/item?id=41447317)
- **Zep and Letta disputes:** [Zep rebuttal](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/) ·
  [Letta benchmarking](https://www.letta.com/blog/benchmarking-ai-agent-memory) ·
  [Graphiti 20k post](https://www.getzep.com/blog/graphiti-hits-20k-stars-mcp-server-1-0/) ·
  [Zep CE deprecation](https://help.getzep.com/ce-deprecation-notice)
- **Letta:** [MemGPT HN 363](https://news.ycombinator.com/item?id=37901902) ·
  [TechCrunch seed](https://techcrunch.com/2024/09/23/letta-one-of-uc-berkeleys-most-anticipated-ai-startups-has-just-come-out-of-stealth)
- **Cognee:** [README](https://raw.githubusercontent.com/topoteretes/cognee/main/README.md) ·
  [labels](https://github.com/topoteretes/cognee/labels)
- **Supermemory:** [TechCrunch](https://techcrunch.com/2025/10/06/a-19-year-old-nabs-backing-from-google-execs-for-his-ai-memory-startup-supermemory/) ·
  [plugins made free](https://supermemory.ai/changelog/coding-plugins-now-free)
- **Hindsight:** [Hermes provider](https://hindsight.vectorize.io/blog/2026/04/06/hermes-native-memory-provider) ·
  [40k post](https://hindsight.vectorize.io/blog/2026/09/28/hindsight-40k-stars)

**Indie projects**

- **ai-memory:** [launch post](https://akitaonrails.com/en/2026/05/23/i-built-memory-system-for-coding-agents-ai-memory/) ·
  [2.0 post](https://akitaonrails.com/en/2026/09/02/ai-memory-2-0-best-memory-system-for-agents-and-teams/)
- **agent-memory:** [repository](https://github.com/tigerless-labs/agent-memory) ·
  [Trendshift](https://trendshift.io/repositories/214454)
- **engram:** [repository](https://github.com/Gentleman-Programming/engram) ·
  [channel tracker (*secondhand*)](https://developereducators.com/channel/gentlemanprogramming/)
- **codebase-memory-mcp:** [repository](https://github.com/DeusData/codebase-memory-mcp) ·
  [arXiv](https://arxiv.org/abs/2603.27277) ·
  [Trendshift](https://trendshift.io/repositories/23635)
- **Understand-Anything:** [repository](https://github.com/Egonex-AI/Understand-Anything) ·
  [Better Stack video](https://www.youtube.com/watch?v=VmIUXVlt7_I)
- **MemPalace:** [benchmark criticism](https://hackernoon.com/resident-evil-star-milla-jovovich-shipped-an-ai-memory-system-devs-shredded-its-benchmarks)
