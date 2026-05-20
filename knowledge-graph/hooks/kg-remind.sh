#!/usr/bin/env bash
# KG ambient memory reminder — random selection from a pool of targeted prompts.
# Each targets a distinct behavior; random hops prevent habituation to sequence.

msgs=(
  "KG memory active. Not loaded yet this session? Call kg_read(cwd) before any task work."
  "KG capture pulse: did the last exchange reveal anything worth keeping? Write it before moving on."
  "About to search files or web? Check KG first — kg_search may already have the answer."
  "Opening files? Check for component nodes in KG before reading — skip what's already mapped."
  "Did user express a preference, style, or constraint? Capture it as a user-level node now."
  "Any node gist gone stale or vague after using it? Sharpen it while context is still live."
  "Active memory session: write discoveries mid-conversation, not at task end. Cache is warm — cost is minimal."
  "About to make an assumption? kg_search first. If missing, state it and capture it."
  "User just agreed on an approach? Capture the methodology as a node — decisions alone aren't enough."
  "Explained something non-obvious? That explanation is a node. Write it before context scrolls away."
  "Any edges missing between nodes you've used today? One edge makes both nodes far more durable."
  "Context window getting deep? Scan for anything unrecorded — this is the highest-value capture moment."
  "User corrected your approach? Capture the signal you missed, not just the fix."
  "Just resolved something that took 10+ minutes? Root cause node before moving on."
  "Any architectural decision made this session? Node with rationale in notes — not just the conclusion."
  "Check archived node IDs — any feel related to current work? kg_read(cwd, id) to promote and use."
  "Did you discover how two parts of the codebase connect? That's an edge. Write it now."
  "KG is your twin across sessions — what would future-you wish was recorded from this conversation?"
)

idx=$(( RANDOM % ${#msgs[@]} ))
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}' "${msgs[$idx]}"
