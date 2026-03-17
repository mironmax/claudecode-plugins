# Capturing Knowledge

## The Art of Node Placement

A knowledge graph's power comes from compression through reuse. When you write `I → likes → pizza` and `Bob → likes → pizza`, the concept `pizza` exists once and is referenced twice. Every additional reference to `pizza` is essentially free — the node has already paid its token cost. This is why node placement matters: place nodes at concepts that will be referenced from multiple contexts.

Think of it like vocabulary. A word becomes useful when it appears in many sentences. A node becomes powerful when it participates in many edges.

### Capture immediately

Capture knowledge **when you discover it**, not at session end. The insight that took 20 minutes to discover takes 20 seconds to record — but only while the context is fresh.

### Should I capture this?

Three questions, in order:
1. **Recoverable from artifacts?** (code, docs, config) → Don't capture
2. **Required non-trivial effort to discover?** → If no, probably skip
3. **Would this help future sessions?** → If yes, capture it

### Capture triggers

| Trigger | What to capture | Level |
|---------|----------------|-------|
| 10+ min debugging | Root cause pattern, not just the fix | project |
| User corrected approach | Signal you missed, how to recognize it | user |
| Explaining same thing twice | The explanation as a reusable node | project |
| Undocumented dependency | The relationship as an edge | project |
| Architectural decision | Decision AND rationale | project |
| Pattern from another project | Generalized pattern | user |
| Got confused about something obvious | Why it's confusing | user |

## Compression Through Reuse

The graph's compression works like language. Consider this example:

**Without reuse** (3 separate nodes):
```
node: "api-auth-needs-session" — API auth requires active session
node: "websocket-auth-needs-session" — WebSocket auth requires active session
node: "cron-auth-needs-session" — Cron auth requires active session
```

**With reuse** (1 node + 3 edges):
```
node: "session-handler" — Session lifecycle manager, provides auth context
edge: api/auth.py --requires--> session-handler
edge: websocket/auth.py --requires--> session-handler
edge: cron/auth.py --requires--> session-handler
```

Same information, one-third the tokens, and `session-handler` is now a reusable concept. Future references cost zero.

## Choosing Node Granularity

A node should be **atomic** — one concept, one headline. If your gist uses "and" to join independent ideas, split into two nodes with an edge.

**Too broad:** "Config system loads env vars, validates them, and manages reload signals"
**Right granularity:** Three nodes — `config-loader`, `config-validator`, `config-reloader` — connected by edges.

**Too narrow:** Creating a node for every file or variable
**Right granularity:** Nodes for concepts, edges to specific files via `touches` or edge references.

The sweet spot: would you reference this concept from another context? If yes, it deserves a node.

## Edge-First Thinking

Before creating a node, ask: **"Can I express this as a relationship between existing things?"**

Edges encode meaning in structure. They connect to existing knowledge. They survive compaction better (connected nodes score higher).

**As a node:**
```
id: "api-auth-requires-session"
gist: "API auth system requires active session before validating tokens"
```

**Better as an edge:**
```
from: "api/auth.py" → to: "session-handler"
rel: "requires-active"
notes: ["token validation fails silently without session"]
```

Same information, but reuses existing references, makes the relationship explicit, and is discoverable from either end.

## Notes: Preserving the "Why"

The gist carries the compressed fact. Notes carry what the gist can't — rationale, constraints, cautionary context. This is especially valuable for:

- **Decisions:** `notes=["chose Redis over Postgres pub/sub because Postgres max_connections was already near limit"]`
- **Feedback from user:** `notes=["user: avoid mocking DB — prod migration failed when mocks diverged"]`
- **Non-obvious constraints:** `notes=["must init before auth.py or token validation silently skips"]`

Notes are excluded from `kg_read` (keeping active context lean) but retrieved on demand via `kg_recall`. This makes them ideal for context that matters when revisiting a decision but would bloat everyday reads.

**Recall notes when:** Making architectural decisions near a related node, debugging familiar-feeling failures, user asks "why did we do X?". The gist tells you what; the notes tell you why.

## Compression Techniques

### Remove filler
Bad: "I discovered that the configuration file needs to be loaded before the database module initializes"
Good: "config must load before db init; silent connection failure otherwise"

### Use references
Bad: "The user authentication module that handles login and token validation"
Good: `auth-module` (node ID)

This extends to external resources — point to where things live rather than paraphrasing them:
```
kg_put_edge(level="project", from="pipeline-bugs", to="Linear:INGEST",
            rel="tracked-in", notes=["all ingestion tickets filed here"])
```
Facts stay in the graph; the artifact stays authoritative.

### Encode as structure
Bad: Node "config-depends-on-env-vars" with gist explaining dependency
Good: Edge `config.py --reads--> .env` with notes for specifics

### Generalize after repetition
When you see the same pattern few times:
Bad: Three nodes: "config-init-bug", "auth-init-bug", "cache-init-bug"
Good: One node: `silent-init-order-deps` — "modules with implicit init order fail silently; seen in config, auth, cache"

### The headline test
A gist should read like a newspaper headline — informative but terse.
Bad: "During debugging I found the API was returning 500 errors because..."
Good: "API 500s: db pool exhausted under concurrent auth checks"

## API Usage

### Creating a node
```
kg_put_node(
  level="project",           # or "user"
  id="kebab-case-id",        # descriptive, unique within level
  gist="the insight itself", # terse but complete (~15 words)
  touches=["file.py"],       # optional: related artifacts
  notes=["caveat or context"] # optional: additional details
)
```

**Then connect it** — an unconnected node shows a tip reminding you to add an edge.

### Creating an edge
```
kg_put_edge(
  level="project",
  from="source-node-or-path",
  to="target-node-or-path",
  rel="relationship-type",   # depends-on, requires, implements, contradicts, etc.
  notes=["optional context"]
)
```

Direct artifact references work without wrapping in nodes:
```
kg_put_edge(level="project", from="src/api/auth.py", to="src/session/handler.py",
            rel="requires-init", notes=["auth.validate() assumes session.current exists"])
```

## What to Capture at Each Level

### User Level (cross-project wisdom)
- **User profile:** Who the user is — domain expertise, background, what they already know deeply vs what is new to them. Use this to calibrate explanation depth and anchor analogies to their existing knowledge rather than starting from scratch.
- Meta-cognitive patterns: "I tend to X when I should Y"
- Interaction signals: "When user says 'focus', I'm being too scattered"
- Architectural principles: "Agentic pipelines need explicit data contracts"
- Tool insights: "pytest fixtures > manual setup for stateful tests"
- Quality bar: Would this help in a completely different project?

### Project Level (codebase-specific)
- Architecture decisions and rationale
- Non-obvious dependencies between components
- Debugging discoveries: "X fails when Y because Z"
- Code conventions not in docs
- Quality bar: Would a new developer benefit from knowing this?
