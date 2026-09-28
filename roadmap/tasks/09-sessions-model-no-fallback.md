# 09 — Sessions model: hook resolution without the project fallback

## Goal

Bring the Lean model in `formal/sessions/` in line with how hooks now find
their KG session, and check the two properties again under the new rule.

## Why

The model's `resolve` mirrors the old code: a hook finds its KG session by
the harness session id, and when that id is not bound, falls back to the
newest session in the project. That fallback is what made identity break
before the F8 fix. The server no longer has it (commit `ef38555`):

- A hook event that carries a harness session id resolves only to the KG
  session bound to that id, or to a KG session its transcript shows it used:
  the last KG session id our own renders left in the transcript, accepted
  only if that session is bound to no one and has the same scope. It then
  gets bound. Otherwise the event resolves to nothing, and nothing is
  injected.
- Only events without any id (hooks older than the binding) still fall back
  to the newest session in the project.

The model should show whether identity (I) and dedup soundness (D) now hold
without relying on the fork fix, and what the evidence route adds.

## Where things are

- `formal/sessions/lean/Sessions.lean` — the model; `resolve`, `bind`,
  `next` (events: startup, resume, fork, hook), `bfs`, `main`.
- `formal/sessions/repro/` — reproductions against the real code;
  `formal/README.md` explains the layout, bounds and how to run.
- `knowledge-graph/server/mcp_http/session_manager.py` —
  `resolve_hook_session`, `_transcript_kg_sid`, `scope_matches`.
- `knowledge-graph/server/mcp_http/rest.py` — `rest_session_bootstrap`
  (resume/fork recovery, unchanged apart from scope matching).
- `knowledge-graph/server/tests/test_user_only_scope.py` — the tests that
  pin the new behaviour.

## What to build

1. A flag for the resolution rule, so one run shows old and new side by
   side, as `fixFork` does today. New rule: bound id → that session;
   otherwise the evidence route (the KG id the Claude session was told,
   `told`, if that session is unbound); otherwise none.
2. One new event: a Claude session whose preload never happened registers a
   KG session through `kg_read` (unbound, `told` = the new session). This is
   the case the evidence route exists for.
3. Report D and I for every combination the flags allow, in `main`,
   and refresh `evidence/model.log`.
4. A reproduction in `repro/` against the real `HTTPSessionManager` for any
   counterexample found, printing `BUG`/`PASS` like the existing ones.

## Constraints

- Keep the model's existing bounds and style; cite the code lines each step
  mirrors.
- No change to server code. If the model finds a real defect, report it
  with its trace and reproduction instead of fixing it here.

## Done when

`lean --run formal/sessions/lean/Sessions.lean` prints the old-rule results
unchanged and the new-rule results for D and I, with and without forks and
with the kg_read-registered event; `formal/FINDINGS.md` gains a short entry
stating what holds under the new rule and within which bounds.
