# Knowledge Graph - Architecture Documentation

## Design Thesis

An agent's memory problem is not a retrieval problem — it is a **compression and
curation** problem. Every session an agent re-derives context it already earned:
project architecture, past decisions, user preferences, hard-won debugging
conclusions. The knowledge graph makes that context durable and cheap: captured
compressed at the moment of insight, connected by explicit relationships, and
loaded whole at the start of every session.

The design rests on one paradigm choice: **move the intelligence to entry, not
retrieval.** An LLM is at its best distilling an insight in the moment it is
understood — full context in the window, nuance still live. Knowledge stored
that way (a telegraphic gist, edges naming how it relates, notes carrying the
why) needs no retrieval machinery at all: the whole active graph fits in
context, and the model scans it natively. No embeddings, no vector store, no
query language — reading structured text is precisely what a language model is
built to do.

**Why this compounds with model capability.** The format is a bet on the reader.
A compressed gist plus its edges is decoded by the model consuming it — so every
generation of sharper models extracts more meaning from the same characters,
follows crumb trails with more initiative, and writes better-compressed nodes
back. Retrieval-engineering approaches age as models improve (their machinery
becomes the bottleneck); a compression-first graph gets *more* valuable, because
both its writer and its reader keep getting smarter. The architecture's job is
only to keep the loop fast, bounded, and lossless — the intelligence is
delegated to the models on either end.

Two graph levels carry the memory: **user** (cross-project wisdom — who the
agent works for) and **project** (codebase knowledge — what it works on). The
working currency of a session is **gists + edges**; notes are depth on demand,
one targeted read away.

#### Core Principles

1. **Compress on Entry, Not Retrieval**
   - **Insight**: LLM best at compression during creation, not search
   - Capture knowledge in distilled form immediately
   - Store only what truly matters (curated by AI)
   - No need for complex retrieval if storage is right

2. **Automatic Pruning & Evolution**  
   - Archival system based on usage, connectivity, recency
   - Auto-compaction when the size budget is reached
   - Self-cleaning (orphan node removal after grace period)
   - Knowledge graph evolves like living memory

3. **LLM-Native Format** 
   - LLMs read JSON graphs directly, fluently
   - No transformation layer (embeddings, queries, etc.)
   - Direct loading into context window
   - Simple beats clever

4. **Dual-Mode Access**
   - **Preloaded**: the SessionStart hook injects the rendered graph into context
     before the first turn — zero tool calls (kg_read is the fallback and re-read API)
   - **Read on demand**: `kg_read(id)` / `kg_read(ids=[...])` retrieves full content (promotes archived nodes)
   - **Memory traces**: Edges to archived nodes guide discovery
   - Sequential reading surfaces "hidden" knowledge

5. **Ambient Loop** (capture → recall → maintain, none of it asked for)
   - **Recall at the prompt**: every prompt is matched server-side against both
     graphs; unseen matching gists ride the hook's context injection — memory
     arrives exactly when it is relevant, with zero model round-trips
   - **Capture on proven re-derivation**: tool traffic (Read/WebFetch/WebSearch)
     is counted per target; an uncovered file read in a second distinct session
     earns a one-time capture nudge — first reads never do
   - **Maintenance by declared debt**: every read renders a per-graph `DEBT:`
     line (wear × staleness × activity); `/kg-maintain` is the bounded pass
     that pays it down and stamps itself, resetting the clock

#### Why This Works

**Load everything by default:**
- Budgets are **exact rendered characters**, fixed by design (no env overrides): `MAX_CHARS_PER_LEVEL` (17,500) per level, `READ_CHAR_BUDGET` (40,000) for the combined kg_read output — single source of truth in `core/constants.py`, line rendering in `core/render.py`
- The arithmetic guarantees kg_read always lands inline in context (never spills to a persisted file): two levels + wrapper < 40K < the MCP client's ~50K persistence threshold
- For graphs the compactor hasn't maintained yet, a render-time degradation ladder enforces the ceiling: lowest-scored archived anchors are hidden first (with a count + kg_search pointer), then lowest-value edges — active gists never
- LLM scans entire graph in milliseconds; no query language or retrieval algorithms — memory is preloaded at session start (or one `kg_read()` away)
- The rendering is node-centric: clusters render together (hub first), each node's relationships indented beneath it, every edge cited once at its first-rendered endpoint — the graph reads as connected knowledge paragraphs, not sections to join by id

**When memory grows beyond limit:**
- Archival scores nodes by: 0.33×recency + 0.66×connectedness (weighted sum of percentiles — see scorer.py)
- Connectedness weights edges by neighbour state: an edge to an active node counts full (1.0), to an archived node `ARCHIVED_EDGE_WEIGHT` (0.2), to an orphaned node 0 — then `in × 0.66 + out × 0.33`. The reduced-but-nonzero archived weight lets a cluster that archived together still be resurfaced by refill (a member isn't scored as fully disconnected just because its neighbours archived too)
- Archive nodes until graph is under `COMPACTION_TARGET_RATIO` (0.8) of the char budget
- Run a resurrection pass: any pre-existing archived node that outscores a just-archived node by ≥0.05 is restored to active
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
┌─────────────────────────────────────────────────────────────┐
│                     Claude Code Sessions                      │
│  Session A (project-a)    Session B (project-b)    Session C │
└────────────┬──────────────────────┬───────────────────┬──────┘
             │                      │                   │
             │ HTTP MCP (stateless) │                   │
             └──────────┬───────────┘                   │
                        ↓                               │
             ┌──────────────────────────────┐          │
             │  MCP Streamable HTTP Server  │          │
             │  (mcp_streamable_server.py)  │          │
             │                              │          │
             │  Endpoints:                  │          │
             │  - / (MCP protocol)          │◄─────────┘
             │  - /api/* (REST: editor,     │
             │    hook brains, debt survey) │
             │  - /health (status)          │
             └──────────┬───────────────────┘
                        │
                        ↓
             ┌──────────────────────────────┐
             │  MultiProjectGraphStore      │
             │  - User graph (singleton)    │
             │  - Project graphs (N)        │
             │  - Write-through persistence │
             │  - Auto-compact (17.5K chars)│
             │  - Self-heal on load/write   │
             └──────────┬───────────────────┘
                        │
                ┌───────┴────────┐
                ↓                ↓
      ┌─────────────────┐  ┌──────────────────────────┐
      │  user.json      │  │  project graphs           │
      │  ~/.knowledge-  │  │  ~/.knowledge-graph/      │
      │  graph/         │  │  projects/<slug>/graph.json│
      └─────────────────┘  └──────────────────────────┘
```

---

### Transport Layer

Two transports, each matched to its client:

- **Stateless HTTP (MCP protocol)** for Claude Code agents. Each request is
  independent; graph sessions are application-level (the `session_id` returned
  by `kg_read`), not transport-level. This matches how the Claude Code MCP
  client actually speaks, keeps the mental model simple, and makes every
  interaction visible in logs.
- **WebSocket** for the visual editor, where we control the client and a live
  view genuinely needs push: the store broadcasts every mutation to connected
  browsers in real time.

Cross-session awareness for agents is **explicit**: `kg_sync(session_id)`
returns a diff of what other sessions changed since the last sync. Explicit
sync fits the workload — a handful of concurrent agents, on-demand
coordination points, small JSON diffs — and keeps agent behavior predictable
and debuggable: sync happens exactly when the agent decides its next move
depends on shared knowledge.

### Transport Architecture

```
┌─────────────────┐         ┌──────────────┐
│ Claude Agents   │         │ Visual Editor│
│ (Claude Code)   │         │  (Browser)   │
└────────┬────────┘         └──────┬───────┘
         │                         │
   Stateless HTTP            WebSocket
   (MCP protocol)         (Real-time push)
         │                         │
         └────────► Server ◄───────┘
                      ↓
           MultiProjectGraphStore
                      ↓
              Broadcast updates
              (store → WebSocket clients)
```

**Both patterns coexist:**
- MCP tools: Explicit sync via `kg_sync()` (polling)
- Visual editor: Implicit updates via WebSocket (push)
- Same underlying store, different transport needs

### The Ambient Loop (hooks × server)

Three thin bash hooks post their raw stdin JSON to the server and print
whatever ready-made hook output comes back — every decision lives server-side,
the hook layer parses nothing and can never break a session:

| Hook | Endpoint | Server decides |
|------|----------|----------------|
| SessionStart (`kg-autostart.sh`) | `GET /api/session_bootstrap` | compact-core preload ≤10K chars (hook inline ceiling, measured), seeds the session's seen-set |
| UserPromptSubmit (`kg-remind.sh`) | `POST /api/prompt_context` | full-read nudge until the loud `kg_read` happens; then prompt-matched recall — RRF search over the prompt's terms, seen-deduped, corroboration threshold, ≤3 unseen gists, marked seen so nothing injects twice; `{}` falls back to staged reminder pools |
| PostToolUse (`kg-tool-event.sh`) | `POST /api/tool_event` | per-target counters (`tool_events.json`); capture nudge only for an uncovered target re-derived across sessions, throttled (session gap, per-session cap, per-target daily cap) |

Precision is the design constraint on this whole loop: an ambient channel that
speaks too often trains the model to ignore it. Thresholds make silence the
default — nothing repeats, weak matches stay quiet, first-time reads never
nudge.

Maintenance closes the loop. `kg_read` and the preload render a `DEBT:` line
per graph (`core/debt.py`: staleness since the last stamped pass × active
days × oversized/unconnected wear, raw factors printed for sanity-checking).
`GET /api/maintenance_debt` surveys every graph on disk, neediest first — the
hook for any dispatcher, from an in-session subagent to a cron tick. A pass
stamps itself via `kg_progress` task `"maintain"`; only stamped passes reset
staleness.

---

## Storage Layer

### File Structure

```
~/.knowledge-graph/
  ├── user.json                          # Cross-project insights (singleton)
  ├── sessions.json                      # Session registry
  └── projects/
      └── <slug>/
          ├── graph.json                 # Project-specific knowledge
          └── tool_events.json           # Read/fetch counters (capture nudges, DEBT activity)
```

**Centralized storage.** All graphs live under `~/.knowledge-graph/` — one place to inspect, back up, and version everything, with project isolation via slug-based subdirectories. A single directory holding all accumulated knowledge is also what makes the whole memory portable: copy it and every project's context moves with it.

**Write-through persistence.** Every mutation (node/edge create, update, delete) is immediately persisted to disk, so a crash can never cost more than the mutation in flight. The cost is negligible at knowledge-capture write rates.

**Periodic git auto-commit.** When the storage root is a git repository, the server itself commits pending changes on a timer (`core/autocommit.py`, `AutoCommitter` daemon thread). Every `KG_AUTOCOMMIT_INTERVAL` seconds (default 900; `0` disables) it commits only when the tree actually changed, using the `Auto-save YYYY-MM-DD HH:MM` message; a final best-effort commit runs on graceful shutdown *after* the store flushes. Committing from inside the server means history accumulates no matter how the process is started or killed — including the normal case where the SessionStart hook launches it and it dies with the machine. No `.git` directory means silent no-op, and git failures are logged, never fatal.

**Self-healing on load and write.** A node should be stored as discrete fields (`gist`, `notes`, `touches`). A client can occasionally serialize the whole node — including tool-call markup — into the `gist` string, leaving `notes` empty; the oversized gist then inflates the active-token budget on every `kg_read`. Rather than trust every writer to be well-formed, the store sanitizes defensively: `core.healer.heal_node_fields` is applied both on write (`put_node`) and on load (each graph is healed the first time it is read from disk, then rewritten). The same function powers both paths, so rendering and storage cannot drift, and it is idempotent — already-clean graphs pass through untouched. This is a third robustness layer alongside atomic writes and the `.prev` rolling backup: those guard against bad *I/O*; healing guards against bad *data*.

### Why JSON Files?

1. **Human-readable** — Inspect/edit with any text editor
2. **Version controllable** — Git tracks changes, diffs meaningful  
3. **Local** — No external dependencies, databases, or services
4. **Simple** — One concept, one format
5. **LLM-native** — Claude reads JSON fluently, no transformation
6. **Portable** — Copy file = backup/share knowledge

**Trade-off accepted:** File I/O instead of DB transactions (mitigated by in-memory store + atomic writes)

---

## Future Development Directions

### Completed

- **Visual Editor** — D3.js force-directed graph with real-time WebSocket updates, full CRUD, multi-panel UI, project selector. Managed via `manage_visual.sh` / `kg-visual` command.
- **Scout Skill** (`/skill kg-scout`) — Mine conversation history for patterns and insights, backfill knowledge graph from past sessions.
- **Extract Skill** (`/skill kg-extract`) — Map codebase architecture into the graph, generate compressed knowledge nodes linked to file paths.
- **Ranked Search** — `kg_search` with Reciprocal Rank Fusion (RRF): query tokenized, each term ranked by occurrence count across all nodes, results merged into a single unified ranking. Searches both user and project graphs; falls back to all loaded project graphs when session_id is absent.
- **Ambient recall & capture** — prompt-matched gist injection per prompt and re-derivation capture nudges on tool traffic; all decisions server-side behind thin hooks (see "The Ambient Loop").
- **Maintenance debt** — per-graph `DEBT:` line, disk-wide survey endpoint, and `/kg-maintain` as a bounded, resumable, self-stamping pass.

### Planned Features
- Collaborative editing (multi-user visual editor)
- Import/export (share graph snippets)
- Analytics (graph metrics, usage patterns)
- Plugin ecosystem (custom archival/scoring algorithms)
- Team memory (shared pools, role agents — see the KG Teams design direction)

---

## Design Principles

1. **Compress on entry, read natively.** The LLM distills knowledge at the moment
   of insight; what's stored is already in the form the next session consumes.
   With storage right, retrieval is just reading — the model's home ground.

2. **The format bets on the reader.** Gists + edges carry meaning the consuming
   model decodes. Sharper models extract more from the same characters, follow
   crumbs with more initiative, and compress better on capture — the graph
   appreciates with every model generation.

3. **Bounded and lossless.** Fixed character budgets keep the always-loaded core
   small and guaranteed inline; tiered archival (active → archived → orphaned)
   means growth never costs knowledge — everything stays reachable, and use
   promotes what matters back up.

4. **Explicit over implicit.** Sync happens when the agent asks; every
   interaction is a visible tool call. Predictable, debuggable, log-readable.

5. **Local, plain, portable.** JSON files under one directory, no services, no
   external APIs. The entire memory can be read with a text editor, versioned
   with git, and moved with `cp`.

6. **Evolution over perfection.** The graph is a garden: capture continuously,
   maintain lightly, let scoring and refill adapt what's active to how the
   knowledge is actually used.

---

**Architecture Status:** Stable (MCP, visual editor, skills, centralized storage all complete).
For the canonical plugin version, see [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json).
