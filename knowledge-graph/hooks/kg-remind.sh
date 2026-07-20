#!/usr/bin/env bash
# KG ambient memory reminder — the attention mechanism. Skill instructions
# load once at session start and fade from attention as the context deepens;
# this hook re-injects one targeted nudge per prompt, near the end of context
# where attention is strongest. The pool is staged by session depth: early
# prompts point at recall (read before you work), mid-session at capture
# (write while it's fresh), deep sessions at maintenance and wrap-up (tend
# the garden, endorse what helped). Random pick within the stage prevents
# habituation to a fixed sequence.

# Session depth from transcript size (bytes), taken from the hook's stdin
# JSON. Rough but cheap: tool results dominate transcript growth, so file
# size tracks how much work has happened better than prompt count would.
# Unknown/missing transcript falls back to the mid pool.
STDIN_JSON=$(cat 2>/dev/null)

# Server-side deterministic pass (v0.9.24+): POST the whole hook payload to
# /api/prompt_context — the server answers with ready-to-print hook output
# when it has something deterministic to say (the full-read nudge while the
# loud kg_read is outstanding, or gists matching this prompt), and {} when
# the staged random pools below should speak. The bash side never parses.
HOST="${KG_HTTP_HOST:-127.0.0.1}"
PORT="${KG_HTTP_PORT:-8765}"
RESP=$(printf '%s' "$STDIN_JSON" | curl -sf --max-time 1 -X POST \
    -H 'Content-Type: application/json' --data-binary @- \
    "http://${HOST}:${PORT}/api/prompt_context" 2>/dev/null)
case "$RESP" in
    *hookSpecificOutput*) printf '%s' "$RESP"; exit 0 ;;
esac

# Legacy fallback for a pre-0.9.24 server (endpoint missing → empty RESP):
# query /api/session_state and compose the full-read nudge hook-side. A new
# server answering {} already handled this case — skip straight to the pools.
if [ -z "$RESP" ]; then
    CWD=$(printf '%s' "$STDIN_JSON" | sed -n 's/.*"cwd":"\([^"]*\)".*/\1/p')
    if [ -n "$CWD" ]; then
        STATE=$(curl -sf --max-time 1 --get "http://${HOST}:${PORT}/api/session_state" \
            --data-urlencode "project_path=${CWD}" 2>/dev/null)
        if printf '%s' "$STATE" | grep -q '"found":[[:space:]]*true' && \
           printf '%s' "$STATE" | grep -q '"full_read_done":[[:space:]]*false'; then
            printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}' \
                "KG preload is a PARTIAL view — the full graph is NOT in context yet. Call kg_read(session_id) once before substantive work; it renders everything the preload dropped without repeating it."
            exit 0
        fi
    fi
fi

DEPTH=mid
TRANSCRIPT=$(printf '%s' "$STDIN_JSON" | sed -n 's/.*"transcript_path":"\([^"]*\)".*/\1/p')
if [ -n "$TRANSCRIPT" ]; then
    if [ -f "$TRANSCRIPT" ]; then
        SIZE=$(stat -c%s "$TRANSCRIPT" 2>/dev/null || echo 0)
    else
        SIZE=0   # first prompt — transcript not written yet
    fi
    if [ "$SIZE" -lt 200000 ]; then
        DEPTH=early
    elif [ "$SIZE" -gt 2500000 ]; then
        DEPTH=deep
    fi
fi

early=(
  "KG memory active. No preload block in context and nothing read yet? Call kg_read(cwd) before any task work."
  "About to search files or web? Check KG first — kg_search may already have the answer."
  "Opening files? Check for component nodes in KG before reading — skip what's already mapped."
  "Check archived node IDs — any feel related to current work? kg_read(session_id, ids=[...]) to promote and use."
  "Dispatching a subagent? It gets NO KG preload — put the relevant gists or kg_* instructions in its prompt."
  "About to make an assumption? kg_search first. If missing, state it and capture it."
)

mid=(
  "KG capture pulse: did the last exchange reveal anything worth keeping? Write it before moving on."
  "Did user express a preference, style, or constraint? Capture it as a user-level node now."
  "User corrected your approach? Capture the signal you missed, not just the fix."
  "User just agreed on an approach? Capture the methodology as a node — decisions alone aren't enough."
  "Just resolved something that took 10+ minutes? Root cause node before moving on."
  "Explained something non-obvious? That explanation is a node. Write it before context scrolls away."
  "Did you discover how two parts of the codebase connect? That's an edge. Write it now."
  "About to make an assumption? kg_search first. If missing, state it and capture it."
  "Active memory session: write discoveries mid-conversation, not at task end. Cache is warm — cost is minimal."
  "Dispatching a subagent? It gets NO KG preload — put the relevant gists or kg_* instructions in its prompt."
  "About to search files or web? Check KG first — kg_search may already have the answer."
)

deep=(
  "Context window getting deep? Scan for anything unrecorded — this is the highest-value capture moment."
  "Check the DEBT lines from your last kg_read — HIGH on either graph? Spawn a maintenance subagent per the /kg-maintain skill (it gets no preload — the skill has the dispatch prompt)."
  "Any node gist gone stale or vague after using it? Sharpen it while context is still live."
  "Any edges missing between nodes you've used today? One edge makes both nodes far more durable."
  "Any architectural decision made this session? Node with rationale in notes — not just the conclusion."
  "KG is your twin across sessions — what would future-you wish was recorded from this conversation?"
  "Wrapping up? Look back at actual results: which nodes truly changed the outcome? kg_useful(session_id, ids=[...]) — up to 5, judged by experience, not promise."
  "KG capture pulse: did the last exchange reveal anything worth keeping? Write it before moving on."
)

case "$DEPTH" in
  early) msgs=("${early[@]}") ;;
  deep)  msgs=("${deep[@]}") ;;
  *)     msgs=("${mid[@]}") ;;
esac

idx=$(( RANDOM % ${#msgs[@]} ))
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}' "${msgs[$idx]}"
