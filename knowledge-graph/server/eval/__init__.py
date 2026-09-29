"""Retrieval evaluation harness — replay logged sessions, score retrieval.

Read-only on everything it is pointed at: the recall decision log
(recall.jsonl), the endorsement log (useful.jsonl) and the graph files under
a storage root, including their git history. Nothing here starts the server
or touches a graph the server could hold in memory.

    cd knowledge-graph/server && ./venv/bin/python -m eval --root ~/.knowledge-graph

Add --transcripts to read private local transcripts and describe activity
after recall. Without it, no transcript inventory or read takes place.

Modules:
  data      — log loading, time windows, graphs as of a timestamp (git)
  variants  — the ranking-variant interface; baseline = production code
  replay    — per-session replay, seen-set reconstruction, consistency check
  report    — descriptive statistics and text rendering
  followthrough — bounded, per-level exposure cohorts and signal evidence
  transcripts   — exact-session Claude/Codex adapters (never execute JS)
  paths         — historical literal file requests without existence probes

What the numbers mean, and do not mean, is in ARCHITECTURE.md
("Retrieval evaluation harness").
"""
