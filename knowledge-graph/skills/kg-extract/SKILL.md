---
name: kg-extract
description: Map codebase architecture into the knowledge graph
---

# Codebase Extract — Systematic Architecture Mapping

## Overview

Extract maps a codebase's architecture into the project-level knowledge graph. You use standard tools (Read, Glob, Grep) to explore, then `kg_put_node`/`kg_put_edge` to record structure.

The goal: a navigable map of how the codebase fits together — not documentation of every file.

## Prerequisites

```
kg_read()                                # Load existing graph
kg_register_session()                    # Enable sync
kg_progress_get(task_id="extract")       # Check prior progress
```

## Node Types

| Type | What it represents | Examples |
|------|--------------------|----------|
| `module` | Cohesive unit of functionality | service, package, feature, bounded context |
| `resource` | Persistent state or external system | database, cache, external API, message queue |
| `entry` | How the system is invoked | HTTP endpoint, CLI command, cron job, event handler |
| `artifact` | File or directory containing code/config | source file, config, directory |
| `contract` | Explicit interface between modules | API schema, shared types, event schema |

**Node properties:**
- `id`: kebab-case unique key
- `gist`: 1 sentence max describing purpose
- For modules: add domain area and layer (api/domain/infra) in notes
- For entries: add protocol (http/cli/cron/event) in notes

## Edge Types

| Edge | From → To | Meaning |
|------|-----------|---------|
| `contains` | artifact → module | This file/dir implements this module |
| `exposes` | module → contract | Module provides this interface |
| `consumes` | module → contract | Module depends on this interface |
| `persists` | module → resource | Module reads/writes this resource |
| `serves` | module → entry | Module handles this entry point |
| `calls` | module → module | Direct dependency (prefer contract-mediated) |
| `configures` | artifact → module/resource | Config affects behavior |

**Edge properties:**
- `notes`: what mediates the relationship, criticality (core/supporting/optional)

## Extraction Process

### Step 1: Check Progress
```
kg_progress_get(task_id="extract")
```
Resume from where you left off, or start fresh.

### Step 2: Survey

Quick scan to understand project structure:
```
Glob("**/package.json")    or    Glob("**/pyproject.toml")
Glob("src/**")
Glob("**/README*")
```

Identify: entry points, main directories, config files, key abstractions.

### Step 3: Map Modules

For each cohesive unit found:
```
kg_put_node(
  level="project",
  id="auth-module",
  gist="JWT-based auth with refresh tokens, middleware pattern",
  touches=["src/auth/"],
  notes=["layer: api+domain", "stateless"]
)
```

**Guidelines:**
- 5-20 modules for a typical project (not hundreds)
- Each module should be a logical boundary, not a file
- Include domain hint in ID: `payment-processor` not `processor`

### Step 4: Map Relationships

Connect modules to each other and to resources/entries:
```
kg_put_edge(level="project", from="auth-module", to="user-db",
            rel="persists", notes=["reads user credentials, writes tokens"])

kg_put_edge(level="project", from="src/config.yaml", to="auth-module",
            rel="configures", notes=["JWT secret, token TTL"])
```

**Prefer edges over new nodes.** If two modules interact, that's an edge — don't create a node for the interaction.

### Step 5: Update Progress
```
kg_progress_set(task_id="extract", state={
  "modules_mapped": ["auth", "api", "data-layer"],
  "directories_surveyed": ["src/", "config/"],
  "last_updated": "2025-01-15"
})
```

## Guidelines

- **Sparse is better:** 20 well-connected nodes > 50 isolated ones
- **Prefer edges:** Relationships are more valuable than descriptions
- **One-sentence gists:** If your gist needs a paragraph, you're over-describing
- **Don't duplicate docs:** If README explains it well, just reference with `touches`
- **Incremental:** Don't try to map everything at once. Map what you explore.
- **Physical + Logical:** Use `artifact` nodes to bridge filesystem to logical structure

## When to Extract

**Good times:**
- First session in a new project (bootstrap foundational nodes)
- After major refactoring (update stale architecture map)
- Spare capacity at end of session
- User explicitly asks to map the codebase

**Bad times:**
- Mid-task (map only what's relevant to current work)
- Small/simple projects (overhead exceeds value)
- Graph near token limit

## Example: Mapping a FastAPI Service

```
# Modules
kg_put_node(level="project", id="api-layer", gist="FastAPI routes, request validation, response formatting")
kg_put_node(level="project", id="domain-logic", gist="Business rules, orchestration, no framework deps")
kg_put_node(level="project", id="data-layer", gist="SQLAlchemy models + async session management")

# Resources
kg_put_node(level="project", id="postgres-db", gist="Primary datastore, schema managed by Alembic")
kg_put_node(level="project", id="redis-cache", gist="Session store + rate limiting")

# Key edges
kg_put_edge(level="project", from="api-layer", to="domain-logic", rel="calls")
kg_put_edge(level="project", from="domain-logic", to="data-layer", rel="calls")
kg_put_edge(level="project", from="data-layer", to="postgres-db", rel="persists")
kg_put_edge(level="project", from="api-layer", to="redis-cache", rel="persists", notes=["rate limiting"])
kg_put_edge(level="project", from="src/config.py", to="data-layer", rel="configures")
```
