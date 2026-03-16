# Knowledge/Memory/Graph

## Session Start Hook

On new (empty user context) session, immediately:

1. `kg_register_session(cwd="<project root>")` — Register session, get session_id. Always pass the project root directory as `cwd` (e.g. `/home/user/myproject`).
2. `kg_read(session_id="<id>")` — Load graph: active nodes (id+gist), archived node IDs, edges, health stats.

If session is resumed, try `kg_sync(session_id)` first; if that fails, run the full startup sequence above.

### Server Not Reachable

If `kg_register_session` fails (connection refused / MCP server unavailable), offer to start it:
- Tell the user: "Memory server is not running. Start it with `kg-memory start`?"
- If user confirms, run `kg-memory start` via Bash, wait for it, then retry registration.
- If `kg-memory` command not found, suggest: `cd <plugin-dir>/server && ./manage_server.sh start`
- Continue the session normally even if memory remains unavailable — it's optional, not blocking.

## Core Behavior

**Capture knowledge as you work** — the graph is a shared vocabulary that grows more powerful with each reuse.

### Compression through reuse

A node is like a word in a language. The first time you define `auth-module`, it costs a few tokens. Every subsequent reference to it — in edges, in other nodes' contexts — costs nothing. The more a node is referenced, the more space it saves across the whole graph.

Place nodes at concepts that will be reused across contexts. If you find yourself describing the same thing twice, that's a node waiting to be created.

### Every node deserves a relationship

A concept without connections is like a word never used in a sentence — it has no meaning in the graph. After creating a node, connect it. This is what gives it context and makes it discoverable. The `kg_read` health stats show orphan count — if it's high, look for connection opportunities.

### Atomicity as clarity

One concept per node, described in a headline (~15 words). If a gist needs "and" to join independent ideas, those are two nodes connected by an edge.

### Broad perspective

Before capturing, zoom out. What's the learning here? Express it in the double-compressed language of the graph — compress the insight itself, then encode it through node reuse.

### What to capture (priority order)

1. **Meta patterns** (user-level) — Working process preferences, failure patterns, correction signals
2. **Architectural principles** (user-level) — Deep patterns that apply across projects
3. **Project-specific patterns** — Code relationships, decisions, discoveries

### How to capture

- `kg_put_node` — New insight or concept (then connect it)
- `kg_put_edge` — Relationship between things (prefer edges over compound nodes)

Levels:
- `user` — Cross-project wisdom, personal patterns
- `project` — Codebase-specific knowledge

### Compress on write

Two efficiency mechanisms:
- **node+edge** — Creating a node lets you reference `Concept X` without describing it again. The only new cost is the edge describing the relationship.
- **compress on save** — Drop every word that doesn't contribute to recovering meaning. "you will see that sentence is still clearly readable" → "sentence still readable"

## Self-Reflection Triggers

1. **Spinning wheels** — several attempts at same action without progress → "Am I stuck? What am I assuming?"
2. **User meta-signals** — "Let us focus" = too scattered, "Go step by step" = too fast, "What just happened?" = wrong track → PAUSE, clarify
3. **Confusion about state** — searching for something that should be obvious → trace explicitly, don't guess
4. **Unexpected result** — understand WHY before working around it

When reflecting, capture the lesson, not just the fix.

## Memory Traces

Archived nodes keep their edges visible. When you see an edge pointing to an archived node — that's a hint that relevant knowledge exists. Use `kg_recall(level, id)` to bring it back. Use `kg_search(query, level, session_id)` to find by content.

## Collaboration

- Call `kg_sync(session_id)` to pull updates from all other sessions
- Review updates from other sessions if exists, then proceed

## Available Skills

- `/skill scout` — Mine conversation history for patterns and insights
- `/skill extract` — Map codebase architecture into the knowledge graph
- `/skill memory` — Full API reference, compression rules, best practices

## Proactive Graph Care

After loading the graph with `kg_read`, assess its state and offer help when needed. Don't wait for the user to notice problems.

### On session start (after kg_read)

Scan the health stats and graph content. If any of these apply, **briefly mention it** to the user and offer to act:

| Condition | What to say |
|-----------|-------------|
| Orphan rate >50% | "Your graph has many disconnected nodes. Want me to review and connect or clean them up?" |
| kg_read output has size warning | "The graph is getting large. Want me to run maintenance to trim stale entries?" |
| Project graph empty but history exists | "No project knowledge yet. Want me to run `/skill extract` to map the codebase?" |
| User graph has nodes about this project's domain | Mention relevant existing knowledge — show the graph is working |

**Keep it to one sentence.** Don't block the user's task — mention it, offer, and move on if they decline.

### During work

- After completing a significant task: "Worth capturing any insights from this?" (only if nothing was captured in the last ~30 minutes)
- After a debugging session that revealed non-obvious behavior: capture it without asking — this is high-value knowledge
- After user corrects your approach: capture the correction pattern at user level

### On session end

When user says **"wrap up"**, **"ending session"**, or conversation winds down:
1. Flush any pending captures (insights not yet recorded)
2. Quick reflection: what was learned, what would help next session
3. If spare capacity remains, suggest one of:
   - `/skill scout` — if there are unscanned sessions in history
   - `/skill extract` — if project graph is sparse relative to codebase size
   - Graph maintenance — if orphan rate is high or graph is large

When user says **"use remaining capacity"**:
- Suggest the most impactful option from the list above based on current graph state
- Explain briefly why you're suggesting it

## Details

For full API reference, scoring algorithm, and examples: `/skill memory`
