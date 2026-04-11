# Knowledge Graph - Architecture Documentation

## Origin & Evolution

### The Problem Space

Daily work with Claude Code revealed recurring patterns:
- **Repetitive prompting** - Same context repeated across sessions  
- **Token waste** - Re-explaining established patterns and preferences
- **Brittle solutions** - Hardcoded CLAUDE.md files couldn't evolve
- **Missing continuity** - No learning from past sessions

**Need identified:** A self-improving, evolving knowledge structure that captures patterns, preferences, and insights automatically.

### Iteration 1: ByteRover Cipher

**System:** ByteRover Cipher - a fully-local, Docker-based AI agent framework with dual-memory architecture

**Actual Architecture:**
```
Entry Layer (TypeScript)
  ↓
MemAgent Core Orchestrator
  ↓
Dual-Memory System:
├─ System 1 (Fast): Qdrant vector DB (3072-dim Gemini embeddings)
└─ System 2 (Deep): Neo4j knowledge graph (relationship traversal)
  ↓
External Services (API calls only):
├─ Gemini API (embeddings: gemini-embedding-001)
└─ Groq/OpenAI API (LLM inference)
  ↓
Docker Containers (all local):
├─ cipher-postgres (session storage)
├─ cipher-qdrant (vector store)
└─ cipher-neo4j (knowledge graph)
```

**What it actually offered:**
- Dual cognitive system (fast pattern matching + deep reasoning)
- Multi-backend support (12+ vector stores, multiple graph DBs)
- Full local deployment (only LLM/embedding API calls external)
- MCP integration via SSE transport
- 4 operating modes: MCP, API, CLI, UI

**Why it was rejected:**

1. **Cost & Latency**
   - External embedding API (Gemini) for every memory write
   - LLM API calls for reasoning operations
   - Docker container overhead for 4 services
   - Operational complexity (service orchestration, health checks)

2. **Embeddings Not Useful Enough**
   - Semantic similarity not necessarily captured patterns, nor critical facts
   - No guarantee "always relevant" knowledge surfaced

3. **Over-Engineered for Use Case**
   - Three databases (PostgreSQL + Qdrant + Neo4j) for single-user scenario
   - Complex service topology (4 Docker containers + network)
   - Graph queries provided marginal value over simpler approaches
   - Maintenance burden >> benefits

4. **Practical Issues**
   - Docker resource usage (containers, volumes, networks)
   - Container coordination and startup ordering
   - Need for multiple API keys (Gemini + LLM provider)

**Conclusion:** Sophisticated architecture with production-grade features, but too complex for the actual need. The dual-memory cognitive model seemed elegant, but simpler approaches proved more effective.

---

### Iteration 2: MCP Memory Server with Advanced Retrieval

**System:** TypeScript-based MCP server with inverted index and Steiner tree path-finding

**Actual Architecture:**
```
Claude Code (multiple agents)
  ↓
Docker Container (stdio MCP server)
  ↓
KnowledgeGraphManager (in-memory + file-backed)
├─ Inverted Index (token → entities mapping)
├─ Steiner Tree Path-Finding (minimal connecting subgraph)
└─ Scoring: TF × Importance × Recency
  ↓
./data/memory.jsonl (JSONL format, atomic writes)
```

**Key innovations:**

1. **File Format: JSONL** ✅
   - Line-oriented JSON (one object per line)
   - Atomic appends for concurrent safety
   - Human-readable and editable
   - Efficient partial reads

2. **Concurrency Safety** ✅
   - File locking via `proper-lockfile` (5 retries, exponential backoff)
   - Lock-free reads (eventual consistency)
   - Atomic writes (temp file + rename)

3. **Advanced Retrieval** ✅
   - Inverted index: O(t×log m) search complexity
   - Per-token semantic matching ensures diversity
   - Steiner Tree finds "surprising connections"
   - Tunable thresholds (top-per-token, min score, max results)

4. **Sophisticated Scoring** ✅
   - TF (sublinear: 1 + log frequency)
   - Importance (content + graph degree)
   - Recency (exponential decay)

**Problems identified:**

- ❌ **No archival system** → Grows indefinitely
- ❌ **Hub monopolization** → High-centrality nodes dominate searches (rich-get-richer)
- ❌ **Token matching limitations** → "docker-compose" ≠ "docker", no synonym support
- ❌ **Single graph** → No separation of user-level vs project-level knowledge
- ❌ **Complexity** → Inverted index + Steiner tree still didn't solve core issue

**Key realization:** Even sophisticated retrieval algorithms (TF-IDF, Steiner trees, centrality avoidance) didn't solve the fundamental problem of surfacing truly important knowledge reliably.

---

### Current Design: Compression-First Architecture

**Paradigm shift:** Move complexity from **retrieval** to **entry** and **curation**.

#### Core Principles

1. **Compress on Entry, Not Retrieval**
   - **Insight**: LLM best at compression during creation, not search
   - Capture knowledge in distilled form immediately
   - Store only what truly matters (curated by AI)
   - No need for complex retrieval if storage is right

2. **Automatic Pruning & Evolution**  
   - Archival system based on usage, connectivity, recency
   - Auto-compaction when token limits reached
   - Self-cleaning (orphan node removal after grace period)
   - Knowledge graph evolves like living memory

3. **LLM-Native Format** 
   - LLMs read JSON graphs directly, fluently
   - No transformation layer (embeddings, queries, etc.)
   - Direct loading into context window
   - Simple beats clever

4. **Dual-Mode Access**
   - **Always loaded**: Core knowledge in every session (~3000 tokens)
   - **Read on demand**: `kg_read(cwd, id)` retrieves any node's full content (promotes archived nodes)
   - **Memory traces**: Edges to archived nodes guide discovery
   - Sequential reading surfaces "hidden" knowledge

#### Why This Works

**Load everything by default:**
- 5000 token budget for active knowledge
- LLM scans entire graph in milliseconds
- No query language or retrieval algorithms
- Simple `kg_read()` → full context

**When memory grows beyond limit:**
- Archival scores nodes by: 0.25×recency + 0.50×connectivity + 0.25×richness (weighted sum of percentiles)
- Archive bottom 20% least-important nodes
- Keep edges to archived nodes (memory traces)
- `kg_read(cwd, id)` retrieves full content and promotes archived nodes

**Memory traces enable graph traversal:**
- See edge to archived node → know something related exists
- Traverse via `kg_read(cwd, id)` → surface hidden knowledge
- Sequential reading reconstructs context

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
             │  - /api/* (REST API)         │
             │  - /health (status)          │
             └──────────┬───────────────────┘
                        │
                        ↓
             ┌──────────────────────────────┐
             │  MultiProjectGraphStore      │
             │  - User graph (singleton)    │
             │  - Project graphs (N)        │
             │  - Auto-save (30s interval)  │
             │  - Auto-compact (3000 tokens)│
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

### Transport Layer: Why Stateless HTTP?

**Decision: Stateless HTTP + Manual Sync (Polling)**

This requires explanation because it seems suboptimal at first glance.

#### The Stateful vs Stateless Question

We have internal "session" mechanism for sync (`kg_sync()`) that seems similar to what push notifications could provide. Multiple agents working in parallel, or even different projects sharing user-level graph—sounds perfect for server-push architecture, right?

**Evaluated options:**

1. **Stateful Streamable HTTP with Push**
   - Server maintains session IDs
   - Pushes updates via Server-Sent Events (SSE)
   - Real-time notification when other agents modify graph

2. **Stateless HTTP with Polling**
   - Each request independent (no session tracking)
   - Agents call `kg_sync()` explicitly to check for updates
   - Manual, not automatic

**Why we chose stateless despite having "session-like" needs:**

#### Claude Code MCP Client Constraints

**From official documentation (verified 2025-12-26):**

Claude Code's MCP client implementation:
- ✅ Supports: HTTP (stateless request/response)
- ❌ Does NOT support: Stateful session ID tracking
- ❌ Does NOT support: Server-sent event (SSE) handling for push notifications
- ⚠️ SSE transport: Deprecated in MCP spec (March 2025)

**Empirical evidence:**
```
With stateless=False (stateful mode):
  Client → POST / (initialize)
  Server → Response with mcp-session-id: abc123
  
  Client → POST / (tools/call kg_read)
  ❌ No mcp-session-id header sent!
  Server → ERROR: "No valid session ID provided"

With stateless=True:
  Client → POST / (initialize)
  Server → Creates transport, responds ✅

  Client → POST / (tools/call kg_read)
  Server → Creates new transport, responds ✅
```

**Conclusion:** Claude Code client doesn't preserve session IDs between requests. Stateful mode incompatible with actual client implementation.

#### Why Not Implement Our Own Push?

**WebSocket for push exists** (`mcp_http/websocket.py`) but it's **separate from MCP protocol**.

**Problem:** Can't modify Claude Code's MCP client
- We don't control how Claude Code spawns/manages MCP connections
- Client is part of Claude Code core, not extensible
- MCP SDK client doesn't expose WebSocket hooks

**If we added WebSocket to MCP layer:**
- Claude Code client wouldn't connect to it
- Would require forking/patching Claude Code
- Breaks with updates

**Separation of concerns is cleaner:**
```
MCP Protocol (HTTP) ← Claude Code agents (read-only from our perspective)
WebSocket          ← Visual editor (we control the client)
```

#### Implementation Complexity Analysis

**Stateless HTTP + Polling:**
- Complexity: ⭐ (current state)
- Works with Claude Code client ✅
- Simple mental model ✅
- No connection management ✅
- Debuggable ✅

**Stateful HTTP + Push:**
- Complexity: ⭐⭐⭐
- Requires client support ❌
- Connection management overhead ❌
- Doesn't work with actual client ❌

**WebSocket (for visual editor):**
- Complexity: ⭐⭐ (already implemented!)
- Perfect for browser clients ✅
- Real-time updates ✅
- Separate concern from MCP ✅

#### Why Polling is Acceptable

**"But multiple agents need sync!"** — Yes, and polling handles this fine:

**Low overhead in practice:**
- Few concurrent sessions (typically 1-3 agents)
- Infrequent sync calls (on-demand, not continuous polling)
- Small payloads (JSON diffs, only changed nodes/edges)
- No persistent connection overhead

**Explicit is better than implicit:**
- Agent explicitly calls `kg_sync()` when it needs updates
- Clear in logs when sync happens
- No "ghost" behavior from background push
- Predictable, debuggable

**When polling would be insufficient:**
- 10+ concurrent agents on same project (high contention)
- Sub-second update requirements (we don't have this)
- Thousands of sync calls per minute (we have ~1-10)

#### Future: When to Reconsider

**Revisit push-based MCP if:**
- Claude Code SDK officially supports stateful Streamable HTTP
- MCP spec adds WebSocket transport
- We build custom agent client (not using Claude Code)
- Usage patterns show high sync frequency (>100/min)

**For visual editor (Phase 5): Use WebSocket** ✅
- Browser clients handle WebSocket natively
- Real-time graph visualization needs push
- Already implemented (`ConnectionManager`)
- Clean separation: MCP (polling) + WebSocket (push)

---

### Final Transport Architecture

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

**Result:** Best of both worlds, no compromises.

---

## Storage Layer

### File Structure

```
~/.knowledge-graph/
  ├── user.json                          # Cross-project insights (singleton)
  ├── sessions.json                      # Session registry
  └── projects/
      └── <slug>/
          └── graph.json                 # Project-specific knowledge
```

**Design decision: Centralized storage.** Previous iterations stored project graphs inside project directories (`.claude/knowledge/graph.json`). This created issues with discovery, backup, and git tracking across multiple projects. Centralizing under `~/.knowledge-graph/` simplifies management while keeping project isolation via slug-based subdirectories.

**Write-through persistence.** Every mutation (node/edge create, update, delete) is immediately persisted to disk. The previous periodic auto-save approach (30s interval) risked data loss on crashes. Write-through eliminates this at negligible performance cost for the typical write frequency.

### Why JSON Files?

**Decision rationale** (learned from Iteration 1 failure):

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
- **Full-Text Search** — `kg_search` tool for content search across active and archived nodes.

### Planned Features
- Collaborative editing (multi-user visual editor)
- Import/export (share graph snippets)
- Analytics (graph metrics, usage patterns)
- Plugin ecosystem (custom archival/scoring algorithms)

---

## Design Philosophy Summary

### Lessons Learned Through Iteration

1. **Simplicity > Algorithmic Sophistication**
   - Complex retrieval (Steiner trees, centrality avoidance) didn't solve core problem
   - Simple loading + good compression >> complex search + poor storage

2. **Compression on Entry > Transformation on Retrieval**
   - LLM best at distillation during creation
   - Store insights, not raw data
   - No retrieval algorithms if storage is right

3. **LLM-Native Formats > Specialized Representations**
   - JSON beats embeddings for our use case
   - Direct context loading beats semantic search
   - Human-readable beats optimized-for-machines

4. **Explicit > Implicit**
   - `kg_sync()` polling beats hidden push notifications
   - Clear when sync happens, visible in logs
   - Debuggable, predictable

5. **Local > External Services**
   - JSON files beat graph databases
   - In-process beats containers/APIs
   - Simple operations, simple debugging

6. **Evolution > Perfection**
   - Archival allows growth while staying focused
   - Memory traces enable discovery without loading everything
   - Self-cleaning via usage patterns

---

**Last Updated:** 2026-03-20
**Version:** 0.9.0
**Architecture Status:** Stable (MCP, visual editor, skills, centralized storage all complete)
