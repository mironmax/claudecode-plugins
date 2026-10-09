# Knowledge Graph - Architecture Documentation

## Design Thesis

An agent's memory problem is not a retrieval problem — it is a **compression and
curation** problem. Every session an agent re-derives context it already earned:
project architecture, past decisions, user preferences, hard-won debugging
conclusions. The knowledge graph makes that context durable and cheap: captured
compressed at the moment of insight, connected by explicit relationships, and
served as a bounded overview plus relevant detail during each session.

The design rests on one paradigm choice: **move the intelligence to entry, not
retrieval.** An LLM is at its best distilling an insight in the moment it is
understood — full context in the window, nuance still live. Knowledge stored
that way (a telegraphic gist, edges naming how it relates, notes carrying the
why) can be read directly: the bounded active graph fits in context, and the
model scans it natively. Lexical search and a touches index reach deeper
content without embeddings or a vector store.

**Why this compounds with model capability.** The format is a bet on the reader.
A compressed gist plus its edges is decoded by the model consuming it — so every
generation of sharper models extracts more meaning from the same characters,
follows crumb trails with more initiative, and writes better-compressed nodes
back. Retrieval-engineering approaches age as models improve (their machinery
becomes the bottleneck); a compression-first graph gets *more* valuable, because
both its writer and its reader keep getting smarter. The architecture's job is
to keep the loop fast, bounded, and explicit — the intelligence is
delegated to the models on either end.

Two graph levels carry the memory: **user** (cross-project wisdom — who the
agent works for) and **project** (codebase knowledge — what it works on). A
third, **maintain**, is the maintenance agent's own craft memory and is never
shown to a working session. The working currency of a session is **gists +
edges**; notes are depth on demand, one targeted read away.

The memory is harness-neutral. Claude Code and Codex CLI run the same plugin,
Antigravity CLI (experimental) a native package, and Claude Desktop the tools
alone, all against one local server. Harness differences live in
`mcp_http/harness.py`, plus the Antigravity adapter (see "The Harness Layer").

#### Core Principles

1. **Compress on Entry, Not Retrieval**
   - **Insight**: LLM best at compression during creation, not search
   - Capture knowledge in distilled form immediately
   - Store only what truly matters (curated by AI)
   - No need for complex retrieval if storage is right

2. **Automatic Pruning & Evolution**  
   - Archival system based on usage, connectivity, recency
   - Auto-compaction when the size budget is reached
   - Self-cleaning (orphaned nodes deleted after 365 days unrecalled, `KG_ORPHAN_GRACE_DAYS`)
   - Knowledge graph evolves like living memory

3. **LLM-Native Format**
   - Stored as plain JSON; rendered for the model as compact text (one line
     per gist, each node's edges indented beneath it)
   - No transformation layer (embeddings, queries, etc.)
   - Direct loading into context window
   - Simple beats clever

4. **Dual-Mode Access**
   - **Preloaded**: the SessionStart hook injects a compact core of the graph
     before the first turn — zero tool calls; one full `kg_read`, followed
     through its parts with `more=true`, renders everything the preload had
     to drop
   - **Read on demand**: `kg_read(id)` / `kg_read(ids=[...])` retrieves full content (promotes archived nodes, except in a maintenance session)
   - **Memory traces**: Edges to archived nodes guide discovery
   - Sequential reading surfaces "hidden" knowledge

5. **Ambient Loop** (capture → recall → maintain, none of it asked for)
   - **Recall at the prompt**: every human prompt is matched server-side against
     both graphs; unseen matching gists ride the hook's context injection —
     memory arrives exactly when it is relevant, with zero model round-trips.
     Harness records (task notifications, image-paste placeholders, dragged
     paths) carry no user intent and stay silent
   - **Recall at the file**: when a tool reads or edits a file (Read, Edit,
     Write, MultiEdit, NotebookEdit, Codex's apply_patch, Antigravity's
     view_file and file-edit tools, and shell commands that plainly read one;
     the grammar lives in `core/file_requests.py`),
     the unseen nodes whose touches name it ride the tool hook's output —
     archived ones too, without promoting them
   - **Capture on proven re-derivation**: tool traffic (Read/WebFetch/WebSearch)
     is counted per target; an uncovered file read in a second distinct session
     (or a URL or query fetched twice) earns a capture nudge, at most once a
     day per target — first reads never do
   - **Maintenance by declared debt**: every read renders a per-graph `DEBT:`
     line (staleness × activity × wear, wear including long or dated ids); `/kg-maintain` is the bounded pass
     that pays it down and stamps itself, resetting the clock

#### Why This Works

**Load everything by default:**
- Budgets are **exact rendered characters**, fixed by design (no env overrides): `MAX_CHARS_PER_LEVEL` (22,000) per level, `READ_CHAR_BUDGET` (50,000) for the combined kg_read output — single source of truth in `core/constants.py`, line rendering in `core/render.py`
- The combined full-graph render targets 50K characters. It can exceed that target when active gists alone are too large, and node batches have no combined budget. So delivery is bounded separately, per client (`mcp_http/paging.py`): a kg_read reply longer than the client keeps whole (Claude Code 45,000 UTF-16 units of its measured 50,000; Codex 36,000 bytes of its 40,000) goes out in parts cut at line ends, and `kg_read(session_id, more=true)` returns the next. The read's effects are recorded and split by part: a node counts as seen, read or promoted only when the part showing it goes out, the full read completes with its last part, and a new read or a compaction drops undelivered parts. Antigravity queues large replies through its hooks instead (`mcp_http/delivery.py`).
- For graphs the compactor hasn't maintained yet, a render-time degradation ladder enforces the ceiling: lowest-scored archived anchors are hidden first (with a count + kg_search pointer), then lowest-value edges — active gists never
- The agent reads the bounded active graph directly; lexical search and file matching reach memories below that surface.
- The rendering is node-centric: clusters render together (hub first), each node's relationships indented beneath it, every edge cited once at its first-rendered endpoint — the graph reads as connected knowledge paragraphs, not sections to join by id

**When memory grows beyond limit:**
- The newest nodes form a fresh tier: by creation time, newest first, while their render cost (the node's line plus the edge citations it brings live) fits in 30% of the level's budget (`FRESH_BUDGET_RATIO`). They are unscored, never archived, and first back when archived; a window by budget rather than days follows each project's pace. Between the fill ceiling and the budget, on a tick that neither archived nor refilled, rebalance swaps the best archived node for the worst active one when it wins by the resurrection margin, at most three swaps per tick (`REBALANCE_MAX_SWAPS`), reverting any swap that would cross the budget. Ranking changes apply without the graph crossing a threshold.
- Archival scores nodes by 0.25×recency + 0.40×connectedness + 0.35×usefulness, a weighted sum of percentiles (`core/scorer.py`). Usefulness is the decayed sum of a node's usefulness stamps (`_useful_ts`, 90-day half-life): the one term an agent controls, and the only one that can say "this was needed" rather than "this was touched". Explicit `kg_useful` endorsements write most of them. Three credits share the same stamps and decay: a repeat (a later `instance-of` case under an older lesson, dated on the case's day), a note credit (a case added as a note to a lesson another session wrote counts as that session's endorsement, once per node), and a maintenance credit (a pass props up a lesson it judges should stay in view, 1–3 stamps per node, 15 per pass, recorded on `_credited_ts`; a credited orphan returns to the archive, where its score decides). Repeats and maintenance credits are not use: the evaluator drops both, and a credited lesson stays only if sessions go on to endorse it. Recency is the latest of the last write, the last full node read (`kg_read` by id) and the latest of these stamps: any credit is recent use as of its stamp, since a gist that works never needs the full read and a right standing rule never gets rewritten. A maintenance pass's own reads and rewrites keep the activity time they found
- Connectedness weights edges by neighbour state: an edge to an active node counts full (1.0), to an archived node `ARCHIVED_EDGE_WEIGHT` (0.2), to an orphaned node 0 — then `in × 0.66 + out × 0.33`. The reduced-but-nonzero archived weight lets a cluster that archived together still be resurfaced by refill (a member isn't scored as fully disconnected just because its neighbours archived too)
- Connectedness also has a floor of `0.5 × log1p(incident edges)`, counting every edge whatever its far end, and is the larger of the weighted degree and that floor; this preserves hubs while their neighbours are archived.
- When archived anchor lines outgrow 30% of the level budget (`ARCHIVED_BUDGET_RATIO`), the lowest-scored archived nodes become orphaned: invisible in reads, still searchable, rescued back to archived when a neighbour is promoted. Orphan selection uses the same archival score, including endorsements, rather than active-edge count alone.
- Archive nodes until graph is under `COMPACTION_TARGET_RATIO` (0.8) of the char budget
- Run a resurrection pass: the best-scored archived node replaces a just-archived one when it leads by ≥0.05 and the swap stays within budget
- `kg_read(session_id, id)` retrieves full content and promotes archived nodes

**When memory sits *under* the fill ceiling (reverse refill):**
- Compaction only moves nodes down; a separate refill pass (`refill_if_room`) moves them back up so headroom isn't wasted
- A single threshold governs refill: it acts whenever the rendered size is below `COMPACTION_TARGET_RATIO` (0.8 × budget) and fills up to that same ceiling — one number is both trigger and target, so headroom can never sit unused between two thresholds.
- No-thrash comes from the ceiling (0.8) sitting below the archive threshold (1.0) — a refill can never push the graph into an immediate archive — plus the store skipping refill on any tick that just archived
- A top-scored candidate too large for the remaining headroom is *skipped*, not allowed to block smaller candidates behind it (the fit check uses an exact O(degree) promotion delta, so walking past blockers is cheap)

**Edges as resurfacing "strings" (render == charge):**
- An edge is a *string* you pull to resurface a connected node: holding an active node, you see its edges and know what is worth reading next, without reading it first.
- A string is only useful if you hold at least one end. So `kg_read` renders — and the char budget charges — an edge **only when at least one endpoint is active** (or is a file/artifact reference, which is always present). See `core/utils.edge_is_live`.
- An edge between two archived nodes is a dangling thread between things you are not holding: it adds output mass and budget cost with zero resurfacing value. These are suppressed from `kg_read` and not charged. They reappear automatically the moment either end is promoted — nothing is lost.
- A single predicate (`edge_is_live`) drives **both** rendering and charging, and the estimator measures the *exact strings* kg_read renders (`core/render.py`), so the visible output and the compaction budget can never drift apart: active gist lines + archived anchor lines + live edge lines, character for character.
- Cross-level edges (a project node pointing up to a user-level node) and artifact edges (a node pointing at a file path) are legitimate: the far endpoint renders as-is and counts as "present". They live in the **project** graph.

**Memory traces enable graph traversal:**
- See a live edge to an archived node → know something related exists, ready to pull
- Traverse via `kg_read(session_id, id)` — or several hops in one call with `ids=[...]` — to surface hidden knowledge (and its now-live neighbours)
- Node reads return the node's own edges, so every read hands back the next crumbs

**Result:** Simplicity + reliability >> algorithmic complexity

---

## Current Architecture

### System Overview

```
 Claude Code · Codex CLI · Antigravity CLI · Claude Desktop      Browser
        │ MCP over stdio              │ hooks (JSON)               │
        ↓                             │                            ↓
 ┌──────────────────────┐             │                ┌──────────────────────┐
 │ kg mcp (stdio shim)  │             │                │ Visual editor :8766  │
 │ cli/mcp_shim.py      │             │                │ (kg editor)          │
 └──────────┬───────────┘             │                └──────────┬───────────┘
            │ one POST per message    │                           │ REST + /ws
            ↓                         ↓                           ↓
 ┌───────────────────────────────────────────────────────────────────────────┐
 │  Memory server :8765  (mcp_streamable_server.py)                          │
 │  /           MCP protocol, stateless HTTP, JSON responses                 │
 │  /api/*      hook endpoints, editor REST, debt survey, projects           │
 │  /ws         live updates for the editor          /health   status        │
 └──────────────────────────────────┬────────────────────────────────────────┘
                                    ↓
 ┌───────────────────────────────────────────────────────────────────────────┐
 │  MultiProjectGraphStore: user graph, project graphs, maintain graph;      │
 │  write-through persistence, compaction (22K chars per level),             │
 │  self-heal on load/write; one server per storage directory (flock)        │
 └──────────────────────────────────┬────────────────────────────────────────┘
                                    ↓
   ~/.knowledge-graph/  user.json · maintain.json · projects/<slug>/graph.json
```

---

### Transport Layer

Two transports, each matched to its client:

- **MCP for agents, from any harness.** Claude Code, Codex, Antigravity and
  Claude Desktop launch `kg mcp` (`cli/mcp_shim.py`), a stdio shim that
  starts the server when it is down and forwards each JSON-RPC message as one
  POST to the server's stateless HTTP endpoint. Only a refused connection is
  retried, so a write never lands twice, and a server restart never
  disconnects the tools. Graph sessions are application-level (the
  `session_id` returned by `kg_read`), not transport-level, which keeps the
  mental model simple and makes every interaction visible in logs. Hooks talk
  to `/api/*` directly.
- **WebSocket** for the visual editor, where we control the client and a live
  view needs push: user-graph mutations arrive live. Project notifications
  still need project-bound subscriptions (F6); the editor uses **Refresh** for them.

**Concurrency.** Many sessions share one server process. Every store
mutation runs under one lock, every save is atomic (temp file, fsync,
rename), and the session registry has its own lock. Two agents editing the
*same node* are arbitrated optimistically: every content change is stamped
(`_written`: time, session), every session records when it last saw each node
and when it last read one in full (times taken before the render), and
`kg_put_node` refuses a write built on a stale view — the node changed since
this session saw it — or on a partial one — it would replace notes or touches
this session never read. The refusal returns the node as it stands and counts
as a full read, so the merged retry goes through. The visual editor's writes
are not checked, but are stamped. Modelled and reproduced first:
`formal/concurrent-writes/`.

Cross-session awareness for agents is **pushed, with an explicit pull
behind it**. A session assumes it works alone: measured on parallel runs, no
session ever called `kg_sync` unprompted, and five concurrent sessions wrote
one lesson as four nodes. So the server says it instead (`mcp_http/foreign.py`):
on prompt and tool hook replies (Claude Code, Codex) and on
`kg_put_node`/`kg_search` replies it appends up to three gists of nodes other
sessions wrote since this session last looked, plus a count and a pointer to
`kg_sync` for the rest. Each change is pushed once (the window is claimed
atomically), never the session's own writes, an archived node, or a node this
session has seen since that write. Only writes that could hold this session's lesson
qualify: none from a maintenance session, and user-level ones only from a
session in the same project, since every project shares the user graph.
`kg_sync(session_id)` still returns the full diff since the last sync.

### The Ambient Loop (hooks × server)

Thin hooks forward the harness's hook JSON to the server and print whatever
ready-made hook output comes back. Every recall decision lives server-side,
and a hook failure never breaks the session: it prints nothing, a staged reminder, or the outage cause.
The SessionStart hook also starts the server through `kg` when it is down, or
offers the install when `kg` is missing. (Antigravity's hooks are listed under
"The Harness Layer".)

| Hook | Endpoint | Server decides |
|------|----------|----------------|
| SessionStart (`kg-autostart.sh`) | `GET /api/session_bootstrap` | starts the server through `kg` if needed (hook side); compact-core preload within each harness's hook ceiling, in the unit that client counts (Claude Code ≤9,500 UTF-16 units of its 10,000; Codex ≤9,000 UTF-8 bytes of its 10,000; measured), seeds the session's seen-set; binds the Claude session id and reuses the existing KG session for ANY source except `clear` (seen-set + full-read state preserved — recovered from the transcript's own KG markers when resume/fork mints a new Claude sid; source-agnostic on purpose, `fork` arrived unannounced and re-preloaded for a week; a recovered session still bound to another Claude sid is cloned, not moved, since that session may still be running); compact resets the session's context state (seen-set, preload set, full-read flag; view times stay for stale-write protection) and re-renders the core, since the summary kept only part of it; every other reused source gets only a continuity note (the transcript still holds the original preload — re-rendering would duplicate); `clear` starts fresh |
| UserPromptSubmit (`kg-remind.sh`) | `POST /api/prompt_context` | full-read nudge until the loud `kg_read` happens; then prompt-matched recall — gated to the humanly-typed part of the prompt (task notifications and image/path placeholders stay silent; path tokens reduce to basenames), run through the shared search core (subtokens, stems, bigrams, field-weighted, sharpened IDF — ubiquitous words carry no signal), seen-deduped, corroboration threshold plus an evidence gate (a hit speaks only corroborated, near-unique, or named by the node's id/gist — lexical strays stay silent), hits injected in evidence-quality order: unseen gists + seen id-anchors + connection edges, marked seen so no gist injects twice; `{}` falls back to staged reminder pools. The same request dispatches background upkeep off-thread and appends budget notices and other sessions' writes |
| PostToolUse (`kg-tool-event.sh`) | `POST /api/tool_event` | file recall (`mcp_http/file_recall.py`): the file a tool touched is looked up in a touches reverse index (user + project graph, rebuilt only when that graph's write generation moves; `path:12-40 (anchor)`, `./`, `~` and absolute touches normalise to the file they name), unseen nodes injected as gist lines — archived included, never promoted — at most 3 within 1,200 chars, ranked by node score then recency, marked seen via `file`, throttled per session (3 per 10 min); Bash counts only for `cat`/`head`/`tail`/`less`/`sed -n`/`grep`/`jq`/`nl`/`rg` operands that exist as files; `apply_patch` for every file its patch adds, updates, deletes or moves to. Otherwise, for Read/WebFetch/WebSearch (and, under Codex, which has no Read tool, shell reads): per-target counters (`tool_events.json`); capture nudge only for an uncovered target re-derived across sessions, throttled (session gap, per-session cap, per-target daily cap). A covered file never nudges. The hook fires for every tool, so other sessions' writes reach a session whatever tools its work runs through |

Both recall channels log every decision to `recall.jsonl`, silences
included: prompt recall under its own reasons with the prompt's terms, file
recall under reason `file_recall` with an `outcome` and the files it looked up.

Shell directory resolution (`mcp_http/shell_context.py`) precedes operand
lookup. Explicit absolute `workdir` wins; otherwise a profile with trustworthy
hook cwd uses it, while Codex requires an exact `tool_use_id` match to a completed
`CommandExecution` in the same session/turn. The resolver reads one tail capped
at 256 KiB and 2,048 records, checks a 50 ms elapsed budget, and caches up to
16 snapshots by file identity, size and modification metadata. It accepts local
absolute paths or local file URIs, rejects conflicting/missing/incomplete evidence,
and never evaluates rollout JavaScript. Execution cwd changes path resolution,
not the memory session's project scope. A partial last line is skipped as a
later record still being written. Shell recall records carry resolution source,
reason and elapsed time, never the command; a Bash call with no file to recall
is logged only when a relative operand could not be placed (`unresolved_cwd`,
`ambiguous_call`). `nl` and `rg` accept explicit
existing file operands with recognized options; directory expansion is excluded.

Precision is the design constraint on this whole loop: an ambient channel that
speaks too often trains the model to ignore it. Thresholds make silence the
default — nothing repeats, weak matches stay quiet, first-time reads never
nudge.

Maintenance closes the loop. `kg_read` and the preload render a `DEBT:` line
per graph (`core/debt.py`: staleness since the last stamped pass × active
days × oversized/unconnected/long-id/smeared/dangling/lift wear, raw factors
printed for sanity-checking; *long id* = over five words or carrying a date; *smeared* = an entity re-described across many nodes'
id+gist while one undated node plausibly owns it — the pass consolidates one
such entity per run, prose mentions becoming edges; *dangling* = `touches`
entries that no longer resolve, a stat per entry and never a walk; *lift* =
clusters of instance-shaped nodes — dated records, sessions, reviews — whose
shared lesson has not been written once as a principle).
Chores (`core/chores.py`) pay the same factors down one category at a time.
Anchor candidates are found by the server before a chore is chosen
(`core/anchors.py`: git's recorded rename, or the one same-named file in the
project; two is a refusal), because a chore has no filesystem; lift clusters
come from `core/lift.py`. Every kind that rewrites text in place skips a node
whose gist changed more than twice in 30 days (`_gist_ts` on the node), since
repeated rewriting is the pattern measured to degrade memory.
`GET /api/maintenance_debt` surveys every graph on disk, neediest first — the
hook for any dispatcher, from an in-session subagent to a cron tick. A pass
stamps itself via `kg_progress` task `"maintain"`; only stamped passes reset
staleness.

### The Harness Layer

The server makes every decision; a harness only carries events in and
context out. Harness detection and capability declarations live in
`mcp_http/harness.py`: which harness sent an event, told apart structurally
(a hook's `transcript_path` — Codex writes `rollout-*.jsonl` under
`$CODEX_HOME/sessions` — or an MCP call's User-Agent, `codex-mcp-client/…`),
and a `Profile` of what differs per harness: the preload limit and the unit
the client counts it in (UTF-16 units or UTF-8 bytes), the size of one paged
reply part, whether shell reads count as reads and whether the hook's shell
directory can be trusted, and the hint a Codex session gets when the plugin's
hooks have never reached the server (Codex keeps plugin hooks off until the
user trusts them in `/hooks`). MCP calls carry the client's name in the
User-Agent the `kg mcp` shim builds from `clientInfo`; Antigravity is also
recognised by its transcript path and the conversation id in MCP `_meta`. The hook scripts and `hooks.json` are shared
unchanged; Codex reads the same `.claude-plugin/` manifest.
The measured rollout completion format is parsed in `mcp_http/shell_context.py`
when the profile says the hook's shell cwd is not trustworthy.

Maintenance dispatch (`mcp_http/chore_dispatch.py`) is split the same way:
target selection and every gate are harness-neutral, and a **runner** per
harness owns its binary, the headless command with scoped MCP access,
and its quota gauge. Codex disables shell and hosted web and blocks filesystem
writes with a read-only sandbox; other built-in tools can still appear.
The gauge belongs to the runner, not to
the harness that sent the prompt: a chore run through Codex spends the
ChatGPT plan's windows (the latest quota event across recently written
rollouts, including resumed sessions in old date directories) and is gated on
them; one run through Claude Code reads `~/.claude/last-limits.json`. The
Antigravity runner (`mcp_http/agy_chore.py`) gates on a live `agy -p /usage`
reading, runs a tool-less `kg-maintainer` agent in its own workspace, and
refuses while paid credits could be spent.
Chores fire on a prompt arriving, off the request thread, at most one at a
time, re-deciding on fresh state inside a lock.

Design notes and the survey behind this split: `docs/harnesses/`.

Codex hooks can omit the shell execution directory. The completed-call
resolver described above restores it from exact rollout evidence; ambiguous
or missing evidence still leaves relative operands unresolved. The same
resolved targets feed file recall and capture counters, including explicit
`nl` and `rg` file reads.

Antigravity CLI (experimental) has its own native package beside the
Claude Code/Codex files: root `plugin.json`, `hooks.json` and
`mcp_config.json`, and `hooks/kg-agy.sh`, which runs `hooks/kg-agy.py`: it
relays hook JSON to `/api/antigravity/hook/<event>`, starts the server on
`SessionStart`, and acknowledges delivered chunks. Its MCP results are
truncated above about 10 KB, so `mcp_http/antigravity.py` returns replies up
to 3,500 UTF-8 bytes inline and queues larger ones per conversation for the
next `PreInvocation` hook, in chunks of up to 40,000 bytes
(`mcp_http/delivery.py`). Every effect of a queued reply, session or graph,
waits in a `DeferredView` until the hook acknowledges the final chunk.
Compaction fires no hook; a new `CHECKPOINT` row in the transcript triggers
the same context reset as Claude Code's `source: compact`. Details:
[ANTIGRAVITY.md](ANTIGRAVITY.md).

### Retrieval evaluation harness

`server/eval/` replays the logged recall decisions (`recall.jsonl`) against the
graphs as they were at each prompt and scores them by the endorsement log
(`useful.jsonl`), so a change to ranking, thresholds or channels can be judged
against real use:

```
cd knowledge-graph/server
python3 -m eval [--root DIR] [--since T] [--until T] [--variant NAME]... [--json]
```

The evaluator uses only the standard library, so any Python 3.10+ runs it.

It reports, overall and per project: fire rate, payload size, the route by
which endorsed nodes reached the session, the share of endorsements that were
dug up, and how often an injected node was endorsed afterwards. Then, per
variant: how many dug-up endorsements it would have surfaced earlier in the
same session, how many ambient endorsements it would still surface, and how
many injections it adds or drops against the baseline. A variant is a function
`(terms, graphs, seen) -> ranked ids` (`eval/variants.py`); the baseline runs
the store's own `search` and `ambient.decide_recall`, not a copy, so it cannot
drift from production. A consistency check replays every record whose graph
state is provable and reports whether the baseline reproduces the logged
decision.

File recall records (reason `file_recall`) carry files, not terms, so they
are not replayed. The descriptive block reports them on their own line, apart
from the prompt statistics; what they injected counts as seen in the
reconstructed seen-set, from the record's time on.

`--transcripts` adds an optional **Activity after staged recall** block.
It reads Claude Code transcripts from `~/.claude/projects` and Codex rollouts
from `${CODEX_HOME:-~/.codex}/sessions`; `--claude-projects` and `--codex-home`
can override those roots. Without the flag the evaluator remains log-only,
with the same output. `--json` includes private per-exposure evidence paths
and line numbers, so keep that output local.

The unit is a recall event × graph level × node. Prompt and file routes,
harnesses, unseen injections, seen anchors, all-seen file candidates and
throttled unseen candidates are counted separately, with distinct
session/node and session/level/node counts alongside repeated exposures.
The window covers the current response and two later human requests, ending
before the third later request or after 30 minutes; a five-minute view uses
the same boundary. Tool decisions already underway at injection are excluded,
including the file operation that triggered a PostToolUse hook.

Signals are explicit `kg_read(id/ids)` requests, exact ids or unique eight-word
historical gist spans in assistant prose, exact-level `kg_put_node` requests,
and accepted exact-session/level endorsements in `useful.jsonl`. Later exact
touched-path read/edit requests and recorded completions are counted apart:
ongoing work often involves those files regardless of recall. Requests do
not prove success. Literal search operands can name directories; matches
remain exact and do not expand to descendants. A completion means the tool
finished, not that a particular file's contents were read. Tool results,
injected context, summaries, replay and
inherited fork prefixes do not count as model-use prose. Codex adapters use
structured completed calls and exact ids, deduplicate UI copies, never execute
JavaScript, and leave ambiguous parallel completions unknown.

Missing/ambiguous transcripts, no later activity, unknown operations, and
missing historical nodes are coverage gaps, not negative labels. Gists and
touches come only from known or approximate git snapshots; current graphs
never substitute for missing history. Rates name their observable denominator,
and endorsement windows predating the endorsement log are censored. The
combined memory-specific rate uses windows observable for every constituent
signal; positive counts outside that denominator remain visible. These
are descriptive associations: the cohorts have different selection, repeated
windows overlap, and none of the comparisons estimates causal benefit.

It is read-only: logs and graph files are only read, git only through
`log`/`show` with optional locks off, and nothing it imports starts the
server.

**What it cannot measure.** An endorsement is the only explicit label, and it
is sparse. A dug-up endorsement (reached by search, a read by id, or never
shown) is a miss recall should have prevented; an injected node nobody
endorsed is a weak negative, not proof of noise, so the harness reports no
precision or recall. The seen-set is not logged and is reconstructed (nodes
active at the session's full read, plus ids logged records flag as seen);
the report prints how often that reconstruction agrees with the logged
speak/silent decision. "Earlier in the session" means before the endorsement,
since the log does not record when a node was dug up. Graph state is *known*
only when git proves the file unchanged between the snapshot and the prompt
(autocommit stages the whole tree, so a later commit that leaves the file
alone proves it); otherwise it is *approx* or, with no history, *current*,
and only *known* prompts enter the consistency check.

---

## Storage Layer

### File Structure

```
~/.knowledge-graph/                      # KG_STORAGE_ROOT; one server holds it (flock)
  ├── user.json  (+ user.prev)           # Cross-project insights (singleton)
  ├── maintain.json  (+ maintain.prev)   # The maintenance agent's own lessons
  ├── sessions.json                      # Sessions: seen-sets, view times, pending read
  │                                      #   parts, Antigravity delivery queues
  ├── aliases.json                       # Old project slugs after a project folder rename
  ├── chores.json / chore_state.json     # Upkeep switch + settings / spacing and counts
  ├── chores.jsonl                       # Every upkeep decision, refusals included (+ .prev)
  ├── recall.jsonl                       # Recall decisions, prompt and file (+ .prev)
  ├── useful.jsonl                       # Endorsements and credits with their route (+ .prev)
  ├── agy-runner/                        # Workspace of the Antigravity maintenance runner
  ├── .git/                              # Optional: enables periodic auto-commit
  └── projects/
      └── <slug>/
          ├── graph.json  (+ graph.prev) # Project-specific knowledge
          └── tool_events.json           # Read/fetch counters (capture nudges, DEBT activity)

~/.local/state/knowledge-graph/          # Runtime state of the kg command (port-<N>/ for
  ├── server.pid · start.lock            #   a server on another port)
  ├── last_start_error                   # Why the last start failed, read by the hooks
  ├── mcp_server.log                     # When kg started the server (systemd: the journal)
  └── backups/                           # Files kg setup / uninstall changed
```

**Centralized storage.** All graphs live under `~/.knowledge-graph/` — one place to inspect, back up, and version everything, with project isolation via slug-based subdirectories. A single directory holding all accumulated knowledge is also what makes the whole memory portable: copy it and every project's context moves with it.

**Write-through persistence.** Node/edge creates, updates, deletes and promotions trigger an immediate save. Read timestamps and maintenance changes also use the periodic save thread. A failed save leaves the graph dirty for retry and is logged; atomic writes protect the previous disk snapshot, not unsaved in-memory work.

**Periodic git auto-commit.** When the storage root is a git repository, the server itself commits pending changes on a timer (`core/autocommit.py`, `AutoCommitter` daemon thread). Every `KG_AUTOCOMMIT_INTERVAL` seconds (default 900; `0` disables) it commits only when the tree changed, using the `Auto-save YYYY-MM-DD HH:MM` message; a final best-effort commit runs on graceful shutdown after the store flushes. Abrupt termination can leave changes since the last commit uncommitted. No `.git` directory means silent no-op, and git failures are logged, never fatal.

**Self-healing on load and write.** A node should be stored as discrete fields (`gist`, `notes`, `touches`). A client can occasionally serialize the whole node — including tool-call markup — into the `gist` string, leaving `notes` empty; the oversized gist then inflates the active-token budget on every `kg_read`. Rather than trust every writer to be well-formed, the store sanitizes defensively: `core.healer.heal_node_fields` is applied both on write (`put_node`) and on load (each graph is healed the first time it is read from disk, then rewritten). The same function powers both paths, so rendering and storage cannot drift, and it is idempotent — already-clean graphs pass through untouched. This is a third robustness layer alongside atomic writes and the rolling backup (`user.prev` beside `user.json`, `graph.prev` beside each `graph.json`): those guard against bad *I/O*; healing guards against bad *data*.

### Why JSON Files?

1. **Human-readable** — Inspect/edit with any text editor
2. **Version controllable** — Git tracks changes, diffs meaningful  
3. **Local** — No external dependencies, databases, or services
4. **Simple** — One concept, one format
5. **LLM-native** — the render is plain text a model reads fluently, no transformation
6. **Portable** — Copy file = backup/share knowledge

**Trade-off accepted:** File I/O instead of DB transactions (mitigated by in-memory store + atomic writes)

---

## Future Development Directions

### Completed

- **The `kg` command** — published on PyPI as `kg-memory`: `kg setup` connects every installed harness item by item with backups, `kg doctor` checks, `kg update` upgrades kg, the server and the plugins together, `kg uninstall` reverses setup and keeps the memory; `kg mcp` is the stdio shim every harness connects through (`cli/`).
- **Visual Editor** — D3.js graph with server-owned projects, ranked search, score inspection, node/edge editing, and live user-graph updates. Project changes require Refresh; ID renames use `kg_rename_node`. Run with `kg editor`.
- **Scout Skill** (`/kg-scout`) — Mine conversation history for patterns and insights, backfill knowledge graph from past sessions.
- **Extract Skill** (`/kg-extract`) — Map codebase architecture into the graph, generate compressed knowledge nodes linked to file paths.
- **Ranked Search** — `kg_search` and prompt recall share one core (RRF, k=60): whitespace tokens plus their `./_-` subtokens, light stemming (schedule ≈ scheduling), adjacent-subtoken bigram terms with their own co-occurrence IDF, field-weighted occurrences (id ×3, gist ×2, notes ×1) and sharpened IDF so one term naming the right node isn't outvoted by several dull ones. Searches both user and project graphs; falls back to all loaded project graphs when session_id is absent. Write-side, the same pipeline powers `put_node`'s near-duplicate and hub-mention nudges.
- **Ambient recall & capture** — prompt-matched gist injection per prompt and re-derivation capture nudges on tool traffic; all decisions server-side behind thin hooks (see "The Ambient Loop").
- **Retrieval evaluation** — `python -m eval` replays logged recall decisions under ranking variants and scores them by endorsements (see "Retrieval evaluation harness").
- **Maintenance debt** — per-graph `DEBT:` line, disk-wide survey endpoint, and `/kg-maintain` as a bounded, resumable, self-stamping pass.
- **Activity-triggered maintenance** — chores and the full pass dispatched as detached headless agents on a prompt arriving, gated on the runner's own quota (Claude Code, Codex or Antigravity).
- **More harnesses** — Codex CLI runs the plugin unchanged, Antigravity CLI through a native package (experimental), Claude Desktop through the tools alone; the harness layer holds what differs.
- **Cross-session awareness** — other sessions' relevant writes are pushed on hook and write replies; a repeat (`instance-of`) credits the lesson it repeats.
- **Delivery sized per client** — the preload measured in each client's own unit, long reads paged so nothing is cut, and only what a part showed counts as seen.
- **Budget notices** — the server reads each session's own quota gauge and says when to plan the wrap-up and when to wrap up now (`mcp_http/budget.py`).
- **User-only sessions** — a session without a project folder (Desktop chat, the home directory) opens user memory alone and can attach a project later.
- **Formal checking** — Lean models and real-code reproductions of the concurrent and stateful parts (`formal/` at the repository root), each fix with a regression test.

### Planned Features
- More harnesses (Cursor is unimplemented — see `docs/harnesses/`), and Antigravity out of experimental
- Collaborative editing (multi-user visual editor)
- Import/export (share graph snippets)
- Analytics (graph metrics, usage patterns)
- Plugin ecosystem (custom archival/scoring algorithms)
- Team memory (shared pools, role agents)

---

## Design Principles

1. **Compress on entry, read natively.** The LLM distills knowledge at the moment
   of insight; what's stored is already in the form the next session consumes.
   With storage right, retrieval is just reading — the model's home ground.

2. **The format bets on the reader.** Gists + edges carry meaning the consuming
   model decodes. Sharper models extract more from the same characters, follow
   crumbs with more initiative, and compress better on capture — the graph
   appreciates with every model generation.

3. **Bounded context, tiered storage.** Fixed character budgets keep the core
   small. Archival preserves full content for later reads; orphaned content
   remains searchable until its deletion grace period expires. Reads promote
   what matters back up.

4. **Decided on the server, written down.** Context arrives without being
   asked — preload, prompt and file recall, nudges, budget notices, other
   sessions' writes — but every decision is made server-side, and the ones
   that shape what memory is used are logged: prompt and file recall in
   `recall.jsonl` (silences included), credits in `useful.jsonl`, upkeep in
   `chores.jsonl`. Predictable, debuggable, measurable. `kg_sync` remains the
   explicit pull.

5. **Local, plain, portable.** JSON files under one directory and one local
   server process; no database, no hosted service, no API key. The entire
   memory can be read with a text editor, versioned with git, and moved with
   `cp`.

6. **Evolution over perfection.** The graph is a garden: capture continuously,
   maintain lightly, let scoring and refill adapt what's active to how the
   knowledge is actually used.

---

**Architecture Status:** Stable (MCP, `kg` command, visual editor, skills, centralized storage, harness layer); Antigravity adapter experimental.
For the canonical plugin version, see [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json).
