# Roadmap

The polish phase below made the memory measurably better before it reached
further; it is complete, and v0.10.0 closes it together with the first second
harness, Codex CLI.

## Where the plugin stands

The server (`knowledge-graph/server/`) is harness-neutral: graphs, scoring,
archival, search, maintenance selection. What differs per harness lives in one
module (`mcp_http/harness.py`) and in a runner per harness for maintenance.
Claude Code and Codex CLI run the same plugin against one local server.
Recent releases made the system observable: recall decisions (`recall.jsonl`), maintenance chores
(`chores.jsonl`) and endorsements with the route by which each node reached
the session (`useful.jsonl`) are all logged.

## What we are optimising

A memory that makes the model better at the work, measured rather than
assumed. Three variables matter, and the 2026 literature on agent memory
separates them cleanly:

1. **Granularity.** Principle-level knowledge transfers and stays durable;
   instance-level traces transfer poorly and can hurt. Nodes should state
   lessons that remain true outside the session that produced them. The
   episode itself belongs in session notes, which a node can point to.
2. **Update regime.** Rewriting the same content over and over degrades it.
   Maintenance should lift instances into principles once, and should not
   keep re-polishing what is already general.
3. **Timing.** Experience helps most when it arrives at the decision point it
   governs, selectively, not all at once at the start of a session.

## Design stance on anchors

A node's `touches` anchor it to the world: files, line ranges, URLs, session
notes. Edges relate memory units to each other. The two stay distinct in
storage. What changes is traversal: every artifact a node touches becomes a
reachable point, so that opening a file can bring back what the graph knows
about it.

## Work items

Each item in `tasks/` is a self-contained brief, written to be picked up by
an agent working only from this repository.

| # | Item | Why | Status |
|---|---|---|---|
| 01 | [File-anchored recall](tasks/01-file-anchored-recall.md) | Timing: surface nodes when the file they describe is opened or edited | shipped, 0.9.41 |
| 02 | [Retrieval evaluation harness](tasks/02-retrieval-eval-harness.md) | Measure every retrieval change against logged reality | shipped, 0.9.40 |
| 03 | [Anchor repair and lift maintenance](tasks/03-anchor-and-lift-maintenance.md) | Granularity and decay: keep anchors valid, lift instances to principles | shipped, 0.9.42 |
| 04 | [Research cards](tasks/04-research-cards.md) | Read the literature against this system's actual conditions | done: `docs/research/` |
| 05 | [Harness instrument review](tasks/05-harness-instrument-review.md) | Know what supporting a second harness costs, and where core ends | done: `docs/harnesses/`; Codex part shipped, 0.10.0 |
| 06 | [Continuous integration](tasks/06-continuous-integration.md) | Every change shows whether it breaks the suite before it is folded | shipped, 0.9.45 |
| 07 | [Lean refactor findings](tasks/07-lean-refactor.md) | Same behavior in less code: one helper per job, tables over branches, clear module seams | open |

A formal pass over the server's concurrent and stateful parts (`formal/`,
Lean models plus reproductions against the real code) found ten issues;
five were fixed in 0.9.44. Open: F5–F8 there, and a lost update when two
agents edit the same node from stale reads.

Also in this phase, done interactively because they are judgement calls:
capture guidance that tests granularity and writes notes as "when this
bites"; shipping an updated recommended output style; verifying which hook
events can carry context at the moment a tool is about to run.

## Later

- **Concurrency on node writes**: optimistic concurrency on `kg_put_node`,
  modelled and reproduced first, like the formal findings.
- **A with/without benchmark** on hard tasks, using only cross-project
  principles, to test whether a well-kept memory multiplies what the model
  can do. Held until its design is agreed.
- **More harnesses**: Cursor and Antigravity each have a gap no adapter
  closes (see `docs/harnesses/instrument-matrix.md`).
- **Desktop apps**: Claude Desktop chat gets the tools but no hooks; the
  Codex desktop app is untested.
