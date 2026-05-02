---
name: kg-extract
description: Map codebase architecture into the knowledge graph
---

# Codebase Extract — Systematic Architecture Mapping

## Purpose

Extract builds a two-tier navigation index in the project graph. The goal is precise:
**let future sessions answer "should I read this file?" without opening it.**

Filenames and directory listings tell you what exists. The KG tells you what each piece
*handles* and — equally important — what it *doesn't*, so skip decisions are reliable.

## Two Tiers

### Tier 1 — Subsystem nodes (5–10 per project)
Directory-scale orientation. Answers: "which part of the codebase?"

```
kg_put_node(level="project", id="auth-subsystem",
  gist="JWT issue/verify + refresh flow. No user lookup, no permissions — those are in user-subsystem.",
  touches=["src/auth/"])
```

### Tier 2 — Component nodes (built lazily, 15–40 total when mature)
File-cluster-scale. Answers: "which file within that area?"
One node covers 1–5 tightly related files that always change together.

```
kg_put_node(level="project", id="auth-token-signing",
  gist="Signs/verifies JWTs only. Key rotation logic here, not in middleware.",
  touches=["src/auth/jwt.ts", "src/auth/keys.ts"])
```

Component nodes are **never created upfront in bulk** — only when you actually explore
that area during a task. They accumulate naturally as the codebase is worked on.

## Node Types

| Type | Tier | What it represents |
|------|------|--------------------|
| `subsystem` | 1 | Major bounded area (auth, payments, ingestion pipeline) |
| `component` | 2 | Specific file cluster with a single clear responsibility |
| `resource` | — | External state: database, cache, queue, third-party API |
| `entry` | — | Invocation surface: HTTP route group, CLI command, cron, event |
| `contract` | — | Shared interface: API schema, event type, shared types package |

## Writing Gists That Enable Skip Decisions

The skip signal comes from knowing what's *not* here, not just what is.

| Pattern | Example |
|---------|---------|
| scope + explicit exclusion | `"Stripe webhook ingestion — signature validation + idempotency. No business logic."` |
| what it owns vs. delegates | `"Rate limiting middleware. Reads Redis counters; does not write them — see rate-counter component."` |
| surprising non-obvious fact | `"Config parser runs at import time — any module reading config must import this first or gets stale values."` |

Bad gist: `"Authentication module"` — tells you nothing about whether to open the file.
Good gist: `"JWT issue/verify only — stateless, no DB calls, no session state"` — skip decision made.

## Edge Types

| Edge | Meaning |
|------|---------|
| `calls` | Runtime dependency (A uses B) |
| `persists` | Reads/writes a resource |
| `serves` | Handles an entry point |
| `exposes` | Provides a contract |
| `consumes` | Depends on a contract |
| `configures` | Config artifact affects behavior |
| `guards` | Middleware/validation wrapping another component |

## Extraction Process

### Step 1: Check progress
```
kg_progress(session_id, task_id="extract")
```

### Step 2: Survey
```
Glob("**/package.json")  # or pyproject.toml, go.mod, Cargo.toml
Glob("src/**", limit=2)  # directory shape only
Glob("**/README*")
```
Identify: main directories, entry points, config files, key abstractions.

### Step 3: Orientation pass — Tier 1 only
Map the 5–10 subsystems. Fast, coarse. Connect them to resources and entries.

```
kg_put_node(level="project", id="api-subsystem",
  gist="HTTP layer: routing, validation, response shaping. No business logic.",
  touches=["src/api/"])
kg_put_node(level="project", id="postgres-db",
  gist="Primary store. Schema via Alembic migrations.", touches=["migrations/"])
kg_put_edge(level="project", from="api-subsystem", to="postgres-db",
  rel="persists", notes=["via domain-subsystem ORM calls"])
```

Save progress:
```
kg_progress(session_id, task_id="extract", state={
  "tier1_done": true,
  "subsystems": ["api", "domain", "data", "auth"],
  "last_updated": "2026-05-01"
})
```

### Step 4: Component pass — Tier 2, only for areas you explore
When you open files in an area, add a component node for the file cluster.
Do not create component nodes for areas you haven't touched.

```
kg_put_node(level="project", id="auth-token-signing",
  gist="Signs/verifies JWTs. Key rotation here. Middleware is separate.",
  touches=["src/auth/jwt.ts", "src/auth/keys.ts"])
kg_put_edge(level="project", from="auth-subsystem", to="auth-token-signing",
  rel="contains")
```

## Guidelines

- **Skip-signal first:** Every gist must enable a read/skip decision. If it doesn't, rewrite it.
- **Exclusions are valuable:** "No X here" is often more useful than "does Y".
- **Lazy Tier 2:** Component nodes accumulate through real work — never bulk-create them upfront.
- **Sparse beats complete:** 20 accurate nodes > 50 approximate ones. Noise degrades navigation.
- **Prefer edges:** If two components interact, that's an edge — don't describe the interaction as a new node.
- **Staleness kills trust:** If you discover a node's gist is wrong, fix it immediately.

## When to Run Extract

**Good:** First session on a new codebase · after major refactor · user asks to map it

**Bad:** Mid-task (just add component nodes for what you're actually touching) ·
graph near token limit · project is small enough to Glob in one pass

## Example: FastAPI Service

```
# Tier 1 — Subsystems
kg_put_node(level="project", id="api-subsystem",
  gist="FastAPI routes + request validation. No business logic — delegates everything to domain.",
  touches=["src/api/"])
kg_put_node(level="project", id="domain-subsystem",
  gist="Business rules + orchestration. Framework-free. Entry point for all logic.",
  touches=["src/domain/"])
kg_put_node(level="project", id="data-subsystem",
  gist="SQLAlchemy models + async sessions. Schema owned here via Alembic.",
  touches=["src/data/", "migrations/"])
kg_put_node(level="project", id="postgres-db", gist="Primary store.")
kg_put_node(level="project", id="redis-cache", gist="Session store + rate limit counters.")

kg_put_edge(level="project", from="api-subsystem", to="domain-subsystem", rel="calls")
kg_put_edge(level="project", from="domain-subsystem", to="data-subsystem", rel="calls")
kg_put_edge(level="project", from="data-subsystem", to="postgres-db", rel="persists")
kg_put_edge(level="project", from="api-subsystem", to="redis-cache",
  rel="persists", notes=["rate limiting only"])

# Tier 2 — Components (added later, as files are explored)
kg_put_node(level="project", id="auth-middleware",
  gist="Validates JWT on every request. Injects user_id into request state. Does NOT issue tokens.",
  touches=["src/api/middleware/auth.py"])
kg_put_edge(level="project", from="auth-middleware", to="api-subsystem", rel="guards")
```
