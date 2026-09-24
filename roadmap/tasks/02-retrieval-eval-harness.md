# 02 — Retrieval evaluation harness

## Goal

A command that replays logged sessions against a graph and scores how well
retrieval did, so every change to ranking, thresholds or channels can be
judged against real use instead of impressions. A change is kept when it does
not score worse — the same keep-only-if-better gate that recursive
self-improvement work relies on.

## Inputs (all already produced by the server)

- `recall.jsonl` — one record per recall decision, silences included:
  `reason` (`injected`, `no_hits`, `all_seen`, ...), `kg_session`,
  `project`, `terms` (the matched prompt terms, not the prompt), `threshold`,
  and `hits` / `best` with `id`, `level`, `score`, `seen`, `title_match`,
  `matched_terms`. Writer: `server/mcp_http/ambient.py:log_recall`.
- `useful.jsonl` — one record per endorsed id: `kg_session`, `id`, `level`,
  `via` (first route by which the node reached the session: `preload`,
  `full_read`, `ambient`, `search`, `read`, `unknown`, or null), `surfaced`,
  `promoted`, `archived`, `refused`. Writer: `store.py:mark_useful`.
- Graph files: `user.json` and `projects/<name>/graph.json` under the storage
  root (`core/constants.py:get_storage_root`). The storage root is a git
  repository, so a graph as of any timestamp can be read from history.

## Ground truth, honestly stated

An endorsement is the only explicit label: the agent says a node mattered.
Endorsed nodes that were dug up (`surfaced: false`) are the misses retrieval
should have prevented. Nodes injected and never endorsed are weak negatives,
not proof of noise, because endorsement is sparse. The harness must report
what it measures in these terms and never call a proxy by a stronger name.

## What to build

1. A module plus CLI under `knowledge-graph/server/eval/`, read-only on all
   inputs. It takes the storage root (or explicit files) and a time window.
2. Descriptive report: fire rate, payload size, distribution of `via` for
   endorsed nodes, share of endorsements that were dug up, injected-then-
   endorsed rate, per project.
3. Replay: for each logged prompt, re-run the ranking on its `terms` against
   the graph as it was at that time (git history when available, the current
   graph otherwise, flagged), under a named variant. Report, per variant, how
   many dug-up endorsements the variant would have surfaced earlier in the
   same session, and how many extra injections it would have made. The
   current production ranking is the baseline variant.
4. A variant interface small enough that a new ranking idea is a few lines
   (a function from terms, graph, seen-set to ranked ids).
5. Text output by default, `--json` for machine use.

## Constraints

- Never write to the storage root, never import anything that starts the
  server, never mutate graphs in memory that the server could share.
- Reuse the production search code for the baseline rather than a copy, so the
  baseline cannot drift from what runs.
- Tests on a small synthetic fixture (a few nodes, a handful of log lines)
  checked into the tests folder. The fixture proves the arithmetic, not
  retrieval quality: say so in the test docstring.
- Self-contained `server/tests/test_v09NN.py`, no pytest.

## Done when

- The report runs on the fixture and on any real storage root without error.
- The baseline variant reproduces the logged injection decisions for records
  whose graph state is known (a consistency check the harness prints).
- Suite green; a short section in `knowledge-graph/ARCHITECTURE.md` or the
  README says what the harness measures and what it cannot.
