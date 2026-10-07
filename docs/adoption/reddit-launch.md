# Reddit launch plan

**Decision: a showcase post first, not an AMA.** An AMA draws questions only
when people already know the author or the project, and at 6 stars few
would. A showcase post where the author answers every comment for the first
hours gets the AMA's benefits without needing an audience. An AMA on agent
memory itself becomes worth doing after the post has worked. It could run as
"I logged every recall decision for months, ask me anything".

## Communities, in order

1. **r/ClaudeCode.** Its readers use Claude Code every day and feel the
   "re-explaining my project every session" pain directly. This is the
   strongest fit.
2. **r/ClaudeAI**, a few days later. Since April 2026 a showcase post needs
   an account with at least 50 karma and must follow Rule 7. Below that it
   goes to the megathread, where it is rarely seen
   ([mod post](https://redlib.groet-infra.nl/r/ClaudeAI/comments/1sly3jm/built_with_claude_project_showcase_megathread/oq2qy78/?context=3)).
   The text of Rule 7 was not found; read the sidebar before posting.
3. **A Codex community**, for the shared-memory angle. Which Codex
   subreddit is active was not verified.
4. **Not r/LocalLLaMA.** kg-memory does not run on local models, so the fit
   is weak.

Do not post to several communities on the same day. Same-day cross-posts are
reported to get flagged. That is anecdotal advice, not a stated rule. When
unsure, message the moderators first, and also ask them whether they allow an
AMA format later.

## The post

**Lead with a finding, not the tool.** "What N weeks of logs taught me about
when agent memory actually gets used" will earn more goodwill than "I built
X". The project then answers the question the finding raises.

**Value proposition, in this order:**

1. **One memory that Claude Code and Codex share.** What one agent learns,
   the other knows. This matters to anyone who switches harnesses when a
   quota runs out.
2. **No second model watching your sessions.** The agent writes the lesson
   while it still has the context, and background maintenance is off unless
   you turn it on. Back this with a measured token cost; see
   [roadmap 12](../../roadmap/tasks/12-adoption.md), item 3.
3. **It forgets what stopped mattering.** Archival by recency,
   connectedness and endorsement keeps the store small.
4. **Local, MIT licensed, no API key, no database, no cloud tier.**

**Material only kg-memory has**, each stated with its caveat:

- **Follow-through by recall route**, from `python -m eval --transcripts`.
  This is descriptive, not causal.
- **The archival scoring change, replayed on three real graphs.** Endorsed
  nodes among the orphaned fell from 108 to 53, 88 to 39, and 6 to 0.
- **The Lean formal pass over the server.** It found ten issues, and two more
  were found later.
- **The blind A/B benchmark of the earlier v2 output style.** It measured
  −27% output tokens. That figure belongs to the output style, not to the
  memory, and v3 has not been re-benchmarked.

**Format:**

- A title that states the finding.
- A 30–60 second video at the top. A Codex session learns something, then a
  Claude Code session recalls it unprompted. End on the visual editor.
- Three short paragraphs: the pain, what the logs showed, what kg-memory does
  about it.
- The two install commands. Name the platforms honestly.
- One line that says you are the author.

**Prepare answers before posting**, as short and plain as the post:

- How is this different from claude-mem? claude-mem has 97.6k stars, so
  this comes in the first hour. claude-mem records what the agent did and
  compresses it with a background model. kg-memory has the working model
  distil the lesson as it goes, keeps typed relationships, prunes over time,
  and shares the store across harnesses. Say where claude-mem is the better
  choice.
- How is it different from mem0 and supermemory? Those are cloud-first
  platforms. kg-memory needs no API key.
- What does it cost in tokens? Give the measured number.
- Does it work on macOS or Windows? State exactly what has been verified.
- What leaves my machine? Memories the agent reads reach its model provider
  like any other context. Nothing else is sent.

## Before posting

- [ ] Install from scratch on a clean macOS machine, then fix what breaks. A
      first comment saying "install failed" costs more than not posting.
- [ ] The README first screen is rewritten (roadmap 12, item 2), so visitors
      from the post see the same promise.
- [ ] The video is recorded. The README's GIF stays as a fallback.
- [ ] The account has at least 50 karma, earned by answering memory and
      context questions in these communities for a couple of weeks.
- [ ] The free listings in [channels](channels.md) are done, so a search
      after the post finds the project.
- [ ] Prepared answers to the questions above are ready.

## On the day

- Post on a weekday morning, US Eastern time. This is common practice, not
  measured here.
- Stay in the thread for the first three to four hours. Answer every comment,
  criticism first.
- Open a GitHub issue for every bug a commenter reports, link it in your
  reply, and fix the easy ones the same day.
- Put the X thread and the blog post out in the same week, so GitHub Trending
  sees one spike rather than three small ones.

## After

- Write down what was asked, what broke and what readers misunderstood.
  Feed it into the README and the FAQ.
- Post to r/ClaudeAI a few days later, adjusted to what r/ClaudeCode
  asked.
- Consider an AMA on agent memory itself only after the project has a few
  hundred users or a measured result worth defending.
