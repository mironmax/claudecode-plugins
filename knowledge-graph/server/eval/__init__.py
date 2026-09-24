"""Retrieval evaluation harness — replay logged sessions, score retrieval.

Read-only on everything it is pointed at: the recall decision log
(recall.jsonl), the endorsement log (useful.jsonl) and the graph files under
a storage root, including their git history. Nothing here starts the server
or touches a graph the server could hold in memory.

    cd knowledge-graph/server && ./venv/bin/python -m eval --root ~/.knowledge-graph

Modules:
  data      — log loading, time windows, graphs as of a timestamp (git)
  variants  — the ranking-variant interface; baseline = production code
  replay    — per-session replay, seen-set reconstruction, consistency check
  report    — descriptive statistics and text rendering

What the numbers mean, and do not mean, is in ARCHITECTURE.md
("Retrieval evaluation harness").
"""
