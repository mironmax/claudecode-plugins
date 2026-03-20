---
name: kg-scout
description: Mine conversation history for patterns and insights worth preserving
---

# History Scout — Mining Past Sessions for Knowledge

## Overview

Scout extracts knowledge from Claude Code conversation history using a **tension-driven, tiered approach**: lightweight scanning first, deep investigation only when signals indicate value.

The goal is **not** to extract everything — it's to find patterns worth preserving while being economical with tokens.

**No special tools needed.** You read history files directly with Read/Bash. Progress persists via `kg_progress`.

## Prerequisites

Before scouting, ensure:
```
kg_read(cwd="<project root>")         # Load graph + get session_id
kg_progress(session_id, task_id="scout")  # Check where you left off
```

## Data Sources

### Tier 1: `~/.claude/history.jsonl` (Always scan first)
- Lightweight: timestamp, project path, first ~60 chars of each prompt
- Format: `{"display": "...", "project": "/path", "timestamp": ..., "sessionId": "..."}`
- Cost: Very low — just metadata
- Value: Shows patterns, repetitions, topics across all projects

### Tier 2: `~/.claude/projects/{encoded-path}/{session}.jsonl` (Selective)
- Full conversation transcripts with assistant responses, tool calls
- Cost: High — can be megabytes per session
- Value: Full context, decisions, rationale
- Path encoding: `/home/user/project` becomes `-home-user-project`

## Tension-Driven Investigation

Don't read full sessions blindly. Use history.jsonl to identify **tension signals**:

| Signal | What it looks like in history.jsonl | Action |
|--------|-------------------------------------|--------|
| Repetition | Same topic appears 3+ times | Deep-dive one session to capture pattern |
| Correction | "no I meant", "that's wrong", "actually" | Check session for preference/clarification |
| Decision | "let's use", "I chose", "going with" | Capture rationale |
| Frustration | "again", "still not", "keeps failing" | Find what was eventually solved |
| Meta | "always do", "never", "remember that" | Direct extraction candidate |

**No tension signal → skip deep investigation.**

## Workflow

### Step 1: Check Progress
```
kg_progress(session_id, task_id="scout")
```
If progress exists, continue from `last_ts`. If empty, start from beginning.

### Step 2: Scan history.jsonl

Read `~/.claude/history.jsonl` (or tail recent lines if very large). Group by:
- **Frequency:** Topics asked about repeatedly
- **Tension signals:** Lines matching signal patterns above
- **Recency:** Prioritize recent sessions

Present findings to user before deep-diving.

### Step 3: Selective Deep-Dive

For sessions with tension signals:
1. Build path: `~/.claude/projects/{encoded-project-path}/{sessionId}.jsonl`
2. Read selectively — focus on user messages and auto-summaries, skip tool results
3. Extract: decisions, corrections, preferences, non-obvious patterns

**Assistant message parsing:** Content is nested: `.message.content[] | select(.type == "text") | .text`

### Step 4: Extract Knowledge

Use standard memory tools:
```
kg_put_node(level="user", id="...", gist="...", notes=["mined from session {id}, {date}"])
kg_put_edge(level="project", from="...", to="...", rel="...")
```

Always:
- Check existing graph first (avoid duplicates)
- Prefer edges over new nodes
- Compress maximally
- Include provenance: `notes: ["mined from session {id}"]`

### Step 5: Mark Progress
```
kg_progress(session_id, task_id="scout", state={
  "last_ts": 1770000000,
  "sessions_reviewed": ["abc123", "def456"],
  "patterns_found": ["docker-networking", "pytest-fixtures"],
  "patterns_extracted": ["docker-networking"]
})
```

## Token Budget

| Activity | ~Tokens | Frequency |
|----------|---------|-----------|
| Scan history.jsonl (500 lines) | 2-3k | Once, then incremental |
| Review patterns, decide | ~500 | Per scan |
| Fetch one session (filtered) | 1-3k | Only for tension signals |
| Extract & create nodes | ~500 | Per session |

**Total productive scout: 5-10k tokens.** Compare: blindly reading 10 sessions = 50-100k tokens, mostly noise.

## When to Scout

**Good times:**
- End of session, spare capacity remaining
- Starting work on dormant project (recover context)
- After major milestone (consolidate learnings)
- User explicitly asks to mine history

**Bad times:**
- Mid-task (disrupts flow)
- Near rate limit (save capacity for real work)
- Graph near token limit (compaction will archive mined content)

## What to Extract

**User-level (cross-project):**
- Workflow preferences: "I always run tests before commit"
- Tool preferences: "I prefer pytest over unittest"
- Communication patterns: "when I say 'focus' I mean narrow scope"
- Recurring confusions: topic asked repeatedly → capture the resolution

**Project-level:**
- Architecture decisions with rationale
- Bug patterns and their fixes
- File relationships discovered during debugging
- Conventions established

**Skip:** Generic greetings, one-off questions, raw code without insight, session mechanics ("continue", "yes").

## Consistency Checks

Before creating any node/edge:
1. Does similar knowledge exist? Scan existing nodes for keyword overlap. Update, don't duplicate.
2. Was this session already in progress state? If yes, skip unless re-scanning.
3. Is the pattern still relevant? Old patterns (>6 months) may be outdated — mark with note.
