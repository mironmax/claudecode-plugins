# Findings

Paths are relative to `knowledge-graph/server/`. Line numbers refer to commit
`f17349a`. Every "reproduced" finding has a script under `<concern>/repro/`
that fails against the unchanged code; its output is in `<concern>/evidence/`.

---

## F1 — Chore dispatch decides on an unlocked snapshot

**Status:** fixed in v0.9.44 as modelled: re-read, re-check, increment inside the lock.


**Where:** `mcp_http/chore_dispatch.py:579` (read state, no lock),
`:580-678` (interval, cap and gauge gates on that snapshot), `:680-691` (the
lock re-checks only `_running`, then writes `snapshot + 1`).
`mcp_http/rest.py:251` starts one dispatch thread per prompt.

**Trace (Lean, 7 steps):**
1. T0 and T1 both pass the fast gate and read `{last_ts: 0, count: 0}`.
2. T0 takes the lock and dispatches, but its launch fails, so `_running` is
   cleared at `:391`.
3. T1 takes the lock, sees `_running` unset, and dispatches too. It writes
   `count = 0 + 1`.

**Broken:**
- the minimum interval between dispatches (45 min default);
- the daily cap (`max_per_day`);
- the stored count, which should equal the number of dispatches.

"At most one chore process at a time" holds in the model.

**Reproduced** with both ways the first chore can end:
- `spawn_fail`: `dispatch, spawn_failed, dispatch, spawn_failed`
- `quick_exit`: two real processes run.

In both, `count=1` after two dispatches.

**Needed window:** a second prompt must arrive between T1's state read and its
lock, while the first chore fails or exits quickly. That window covers the
gauge read, target selection (graph loads, debt) and `maintain_lessons`.

**Modelled fix:** inside the lock, re-read the state, re-check the interval
and cap, and increment the fresh count. With it, no violation is found within
≤3 threads and ≤9 ticks, and two properly spaced dispatches are still
reachable.

**Also seen:**
- `_running` means "a process is alive" but is also the only thing that
  serialises dispatch decisions; that mismatch is the root cause.
- `last_ts` can move backwards, because T1 writes its earlier `now`.
- The fast gate at `:575` looks redundant for safety, since the lock repeats
  the check (inference).

Files: `chore-dispatch/lean/Dispatch.lean`, `chore-dispatch/repro/repro_stale_state.py`.

## F2 — A failed write-through is recorded as saved

**Status:** fixed in v0.9.44: a failed save leaves the graph dirty.


**Where:** `mcp_http/store.py:385-390`. `_write_through` ignores
`_save_to_disk`'s `False` and sets `dirty = False`. `core/persistence.py:92-150`
returns `False` on any exception (disk full, EIO, permissions). `shutdown()` at
`store.py:1892` saves only dirty graphs.

**Effect:** the mutation exists only in memory, and `put_node` and the other
writers report success. Unless a later write-through of the same graph
succeeds (saves write the whole graph), the change is lost at shutdown.

**Reproduced** with an injected ENOSPC inside the real `GraphPersistence.save`:
the node is absent after a restart.

**Model:** the invariant is "not dirty ⇒ memory == disk". It breaks in
1 step, and holds once the fix keeps `dirty` set when the save fails.

## F3 — Forced reload discards unsaved memory

**Status:** policy in v0.9.44: disk wins, the discard is logged, and the editor and cross-site triggers are gone.


**Where:** `mcp_http/store.py:422` (`read_graphs(force_reload=True)` reloads
from disk and clears `dirty`), reached via
`GET /api/graph/read?reload=true` (`mcp_http/rest.py:80`). The kg-ops skill
documents it.

**Reproduced two ways:**
- after F2, the unsaved node disappears from memory too;
- with no failure at all, `read_node`'s `_last_read_ts` stamp
  (`store.py:1206`, dirty-only until the 30 s saver runs) is discarded.

**Model insight:** with both F2 and F3 fixed, the only remaining loss is a
disk that keeps failing through shutdown. So the achievable guarantee is
"lost only if every later save failed", not "never lost". A reload while dirty
needs a policy: flush first, or refuse.

## F4 — The saver thread can die for the rest of the process

**Status:** fixed in v0.9.44: session manager locked, saver ticks guarded.


**Where:** `mcp_http/session_manager.py` has no lock. `cleanup_expired`
(`:165`) iterates `_sessions` on the saver thread (`store.py:1868-1890`, under
`store.lock`). `register` (`:68`) adds to it from the event-loop thread, which
does not take `store.lock`. `_periodic_save` has no `try`, so
`RuntimeError: dictionary changed size during iteration` ends the thread.

**Effect, for the rest of the process:**
- no compaction or orphan pruning;
- no session persistence or expiry;
- dirty-only mutations (read stamps, prunes) wait for shutdown.

Nothing reports the failure beyond one traceback on stderr.

**Reproduced** (`sessions/repro/repro_saver_death.py`):
- `amplified`: 20k sessions and a 1e-5 s switch interval;
- `default`: 500 sessions and CPython's default interval.

Both use a 0.05 s saver period against back-to-back registrations. In
production the saver runs every 30 s and registrations are occasional, so
each hit is unlikely but permanent. The same unlocked iteration exists in
`recently_seen_ids` (`:213`, on the chore-dispatch thread) and
`save_sessions` (`:366`, which catches the error and silently skips the
save). See F10 for the consequence.

## F5 — Rename re-points or drops cross-level edges

**Where:** `mcp_http/store.py:937-1028` (loaded graphs), `:1060`
(`_sweep_disk_rename`), `core/persistence.py:153-205` (on-disk rewrite).

**Root:** edge endpoints are bare ids, resolved local-first and then against
the user graph (`_clean_orphaned_edges`, `store.py:1738`). The same id may
exist at both levels, and `put_node` does not prevent it.

**Model:** exhaustive over 256 configurations × 2 levels. 66 of 128 accepted
renames leave some edge pointing somewhere other than where it pointed before.

**Reproduced, all four variants** (`rename/repro/repro_rename.py`):

| | Rename | Setup | Result |
|---|---|---|---|
| R1 | user `a→b` | a **loaded** project owns a local `b` | its edge to `user:a` now hits the local `b`. The loaded path lacks the collision check the disk path has. |
| R2 | user `a→b` | the same project, **on disk only** | `skip:collision` leaves an edge to the vanished `a`; the next load garbage-collects it. It is reported only in `skipped_graphs`. |
| R3 | project `a→b` | the user graph also has `a` | the sweep rewrites *other* projects' edges that meant `user:a`. Project ids are not visible to other projects. |
| R4 | project `a→b` | the user graph owns `b` | the project's edges to `user:b` are captured by the renamed node. The collision check covers only the node's own graph. |

**Model catch:** the first candidate fix (a collision precheck plus no
cross-project rewrite) still failed R4 in the model. A correct fix must refuse
any rename where the new id already exists at the other level with edges that
reach it.

**Side observation:** `_clean_orphaned_edges` accepts an endpoint found in
*any* loaded graph, including other projects. Whether a dangling edge survives
a load therefore depends on which projects happen to be loaded; R3 shows this.

## F6 — The editor never receives project-level live updates

**Where:** `visual-editor/backend/server.py:344` and `app.js:121` connect
to `/ws` without a `session_id`. `mcp_http/rest.py:479` then registers a
session with `project_path=None`. `mcp_http/websocket.py:74` delivers
project-level changes only when `session_project == project_path`.

**Reproduced** via the real REST and WS routes (`websocket/repro/repro_ws.py`):
a Claude session's project write never reaches the editor, but the next
user-level write does.

**Also:** every editor (re)connect registers a session that lives 24 h, and
each registration `fsync`s all live sessions (`session_manager.py:68-95`).

## F7 — Compaction and refill churn

**Where:** `core/compactor.py:31` and `:132`. The refill docstring claims
no-thrash (`:146`); `store.py` `_maybe_compact` skips refill only on the
*same* tick.

**Randomized search over the real `Compactor`:** 20,000 random over-budget
graphs, ticked the way the saver ticks them. In 4,388 of them, a node archived
on tick *t* is promoted back on tick *t+1*. Every graph reaches a fixpoint, so
this is churn rather than thrash.

**Cause:** compaction archives in score order, re-measuring after each node,
so the last (possibly large) node overshoots below the 0.8 target. Refill then
uses the gap, and a just-archived small node often ranks top. The cost is an
extra write-through and a node that flickers for one tick. Low severity; an
efficiency and log-noise issue.

## F8 — A fork shares its live parent's KG session

**Where:** `mcp_http/rest.py:128-139`. Transcript recovery reuses the
parent's KG session and calls `bind_claude_sid(cand, child)`.
`session_manager.py:97` moves the binding to the child. The still-live
parent's hooks then fail `find_by_claude_sid` and fall back to
`find_by_project_path` (newest in project; `session_manager.py:285`,
`ambient.py:237-240`, `file_recall.py:377-379`). That fallback is the
cross-session poisoning the ambient comment says the binding fixed.

**Model:** exhaustive, ≤3 Claude sessions. Two properties both fail:
- D, dedup soundness: a gist suppressed as "seen" must really be in that
  Claude session's context;
- I, identity: a session's hooks resolve to the KG session it passes to its
  `kg_*` calls.

With fork events removed, both hold, so fork is the sole cause.

**Reproduced** via the real `/api/session_bootstrap` (`sessions/repro/repro_fork.py`):
- the fork reuses the parent's KG session;
- `find_by_claude_sid(parent)` returns `None`;
- the parent's next prompt hook resolves to another session and receives that
  session's full-read nudge.

This applies only while the parent stays alive after the fork. A plain resume
(the parent ends) is sound in the model.

## F9 — Cross-site GETs with side effects

**Status:** fixed in v0.9.44: `Sec-Fetch-Site: cross-site` or a non-local Origin gets a 403 (`security.request_refusal`).


**Where:** `mcp_http/security.py` guards Host (anti-rebinding) and WebSocket
Origin. Plain HTTP Origin is not checked, and a cross-site request carries a
legitimate `Host: 127.0.0.1:8765`.

**Reproduced** (`security/repro/repro_csrf.py`): any web page can fire
- `GET /api/graph/read?reload=true`, the F3 loss path;
- `GET /api/session_bootstrap`, which registers and `fsync`s a session.

All POST endpoints tested reject cross-site `text/plain` bodies with a 422, so
cross-site writes are blocked, but only because FastAPI refuses non-JSON
bodies. That is an implicit rather than a stated defence.

## F10 — The pass tier skips the live-context rename rule

**Status:** fixed in v0.9.44: the pass prompt lists the held ids; an unreadable live context refuses the dispatch.


**Where:** `core/chores.py:21` states "Never RENAME a node the user is looking
at". The chore tier enforces it through `pick_chore(in_context=_live_seen(...))`
(`mcp_http/chore_dispatch.py:553`). The pass tier (`_pick_target`,
`chore_dispatch.py:497-521`; `build_pass_prompt`, `core/chores.py:545`) gets
no in-context set, yet its prompt allows up to 5 renames plus merges, and
`chores/pass-settings.json` allows `kg_rename_node` and `kg_delete_node`.
Passes are dispatched when a prompt arrives, which is exactly when a session
is live.

**Also:** `_live_seen` (`chore_dispatch.py:353-364`) returns `set()` on any
exception. That fails *open*, against the module's "every gate fails closed",
and F4's unlocked iteration makes such an exception reachable.

**Found** by reading only; executing it end to end needs a live pass agent. The fix is covered by unit tests of the payload and prompt.

## F11 — A write built on a stale or partial view drops someone's work

**Status:** fixed in v0.10.1. Found after the first pass, when a Claude Code
and a Codex session worked one graph at the same time.

**Where:** `mcp_http/store.py` `put_node`. An agent edits a node by
read-modify-write: it sends back what it saw plus its change, and `notes` and
`touches` replace the stored lists wholesale. The version counter records the
last writer but was never checked, and it also bumps when a read promotes an
archived node, so it cannot tell a content change from a read.

**Two ways it loses work:**
- *Lost update:* sessions A and B read a node; A adds a note; B writes the
  list it read plus its own note, and A's acknowledged note is gone.
- *Blind replace:* a session that has seen only the gist (preload, full read,
  recall, search) sends notes; the stored notes it never read are gone. No
  second session is needed.

**Model** (`concurrent-writes/lean/Writes.lean`, two sessions, ≤12 ticks):
the lost update in 4 steps today. A guard that refuses a write when another
session changed the node after this session's last view is safe (423 states)
if the view is recorded atomically. Recorded the way a handler does it — the
render, then `mark_seen` — the guard still loses an update when the view time
is taken at marking (6 steps: the other write lands between the snapshot and
the mark). Taking the time before the render closes it (1,169 states). Both
sessions can still get their additions in.

**Reproduced** (`concurrent-writes/repro/repro_lost_update.py`): both ways,
through the real store.

**Fix:** a content stamp `_written` {ts, by}, set only when gist, notes or
touches change; per session, the time of the last view (`seen_at`) and of the
last full read (`read_at`), each taken before the content is read. `put_node`
refuses (`NodeConflictError`, with the node as it stands) when another session
changed the node after this session's last view, or when the write would
replace stored notes or touches this session has not read in their current
form. Own writes and the refusal itself count as full reads, so the merged
retry goes through. The visual editor's REST writes skip the check (a person
editing a screen that shows the whole node) but still stamp. `kg_sync` shows
truncated gists and does not count as a view. Tests:
`tests/test_concurrent_writes.py`.

**Not covered:** edges carry `notes` too and are replaced the same way; an
edge is rarely edited, so it is left for now.

---

## Checked and found sound

These were suspected, checked, and hold. They are listed so they need not be
re-checked.

- **At most one chore process at a time** (F1 model, ≤3 threads).
- **The file-recall throttle's check-then-record** (`file_recall.py:285-301`).
  It looks like F1, but every caller runs synchronously on the event-loop
  thread (`rest.py:278`) with no `await` in between, so it cannot interleave.
  `_throttle_lock` is currently redundant.
- **Ambient tool-event counters**: read-modify-write under `_events_lock`,
  and single-threaded anyway.
- **Node lifecycle flags**: every un-archive path clears or guards
  `_orphaned_ts`, so "orphaned but active" is unreachable. Checked by reading
  every writer.
- **The `TouchIndex` cache** (`file_recall.py:71`): every path that changes
  touches writes through or bumps `write_gen`, and a reload bumps it too, so a
  reused `id(graph)` cannot serve a stale table.
- **Shutdown ordering**: the store flushes before the final auto-commit, and
  both callers run in sequence on one thread.
- **Cross-site POSTs**: rejected (see F9).

## Optimisation candidates (inference, not measured)

- Every REST and MCP handler is `async` but calls blocking store methods
  (lock plus `fsync`) directly on the event loop. A saver pass holding
  `store.lock` (compaction of all graphs plus `fsync`) stalls every request,
  including hooks that budget one second.
- `register()` serialises and `fsync`s every live session on each registration.
- `file_recall._rank` scores the whole graph, under the lock, on every tool
  event.
- `tool_events.json` is read and written on every tool call, on the event loop.
- `ambient._target_covered` (`ambient.py:523`) uses a case-insensitive
  substring match, so a node touching `restore.py` "covers" `store.py` and
  suppresses a capture nudge.
- The auto-committer runs `git add -A` without coordinating with saves, so it
  can capture a half-written `*.tmp` or `.prev`. This is noise rather than
  corruption, since renames are atomic.
- Chore timeouts kill only the Claude pid. The process was started with
  `start_new_session`, so its children may survive.
