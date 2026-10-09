import Std
/-!
Cross-session awareness: the push of other sessions' writes (`foreign.notice`,
0.13.0) and `kg_sync` (0.10.0), seen from ONE observing session S.
Paths are relative to knowledge-graph/server/; line numbers at 1d03171.

Actors: S (observer, project P), F (regular, project P), G (regular, project Q)
and M (a maintenance pass in P; its flag survives a restart since F29,
store.py:129 + session_manager.py:290-301). Nodes: 0 lives in P's project
graph, 1 in the user graph. Both exist before S starts, written by F.
Each node carries
  w     = _written {ts, by}           put_node, stamped only when the content
                                      changed (store.py:733-736, 763-766)
  v     = _versions[node:id] {ts, session}  _bump_version (store.py:312-325)
  arch  = _archived

Atomicity. Every MCP tool handler (`call_tool`, mcp_streamable_server.py:452-823)
and every REST hook route (rest.py:282-379) is an `async def` with no `await`
in its body, on uvicorn's single event loop, so each handler runs to the end
before another starts. The saver thread's compaction (archival) takes
store.lock and may fall anywhere; it bumps no version.

Steps (each one atomic; the clock ticks once per step)
  write a i    put_node with new content (store.py:692-803): w := v := (t, a),
               unarchive (:769-772). S's write first passes the stale-view guard
               (_refuse_stale_write, store.py:831-860): refused when S saw the
               node before another session's last write; the refusal shows the
               node in full and counts as a view (mcp_streamable_server.py:629,
               640-643). A blind write (S never saw the node) is allowed, as F11
               decided for a gist-only put; it supersedes the earlier write.
               S's own write marks it seen at w.ts (store.py:786).
  bump a i     a version bump without a content write: rename_node
               (store.py:1205-1212; _written rides along with the node; seen
               times follow the id, session_manager.py:462-494, so identity =
               node), or a put_node re-sending the stored content (changed=False,
               store.py:733-736; the bump at :778 is unconditional).
  promote a i  another session reads an archived node (_record_read,
               store.py:1498-1526): unarchive, v := (t, a), w unchanged. A
               maintenance session's read has no effect (:1507), so not M.
  view i       S reads node i (kg_read ids, a search hit, the preload): seen_at
               := t, a time taken before the render (mcp_streamable_server.py:
               518, 604; session_manager.py:391-402); S's read of an archived
               node promotes it with v := (t, S).
  archive i    compaction (core/compactor.py:82-83): _archived, no version bump.
  sync         kg_sync (mcp_streamable_server.py:757-787): get_sync_diff from
               get_sync_ts (store.py:1558-1604: every node with v.ts > since and
               v.session != S) is listed; mark_synced(at = a time taken before
               the diff) (session_manager.py:585-590).
  notice       foreign.notice (foreign.py:21-64), on a hook reply (rest.py:282-
               306) or a kg_search / kg_put_node reply (mcp_streamable_server.py:
               440-450, 624, 666): claim_push_window (session_manager.py:562-575:
               since := max(start, synced, pushed); pushed := now) ->
               get_sync_diff(since) -> skip archived, seen_at >= w.ts, a
               maintenance writer (store.is_maintenance, store.py:1764-1766),
               and at user level a writer from another project (foreign.py:67-72)
               -> newest K by w.ts shown -> mark_seen(shown, at=time.time()).
  drop         (claim mode) the same notice on a hook reply that never reaches
               the model: kg-remind.sh:29 and kg-tool-event.sh:21 give the
               round trip `curl --max-time 1` and print nothing on a timeout,
               while the server finishes the handler and applies its marks.
  Antigravity  (deferred mode) the reply goes through a DeferredView
               (antigravity.py:59-79, delivery.py:25-60): the notice claims
               nothing (foreign.py:28-34, claim=False) and records
               mark_pushed(now) and mark_seen(at) as effects; kg_sync records
               mark_synced. A reply of at most INLINE_BYTES commits at once
               (antigravity.py:64-66); a larger one is queued (:72-79,
               delivery.py:68-82) and commits when a hook delivers it and the
               ack arrives (delivery.py:133-150); a refused one (no hooks yet,
               or the queue full, :67-75) changes nothing. One queued reply
               at a time here (QMAX = 1; the code allows 64).

Fix flags (the code at 1d03171 is all false)
  fixWindow  a pushed node must have been WRITTEN inside the window (w.ts >
             since), not merely had its version bumped there.
  fixMask    a node whose last content write is another session's since the
             watermark is listed even when S's own rename or re-put bumped
             its version after that write (get_sync_diff).
  fixPending (Antigravity) a notice counts the push, sync and seen marks of
             replies already queued for delivery as made: a queued reply is
             never dropped, only delivered or replayed (delivery.py:124-131).

Ghost state: pend[i] = the latest qualifying foreign write to node i (by F;
user level only from S's project) not yet delivered to S by a push, a sync
listing, a view or a refusal; shown[i] = the w.ts last delivered by a push;
viol = contract bits seen on delivered pushes.

Timestamps are compared only by order and equality, so a state is kept in
canonical form: every stamp renamed to its rank among the stamps held (0 stays
0). The clock is implicit (a step's time is one past the largest stamp held)
and the reachable space is finite: the search below is exhaustive over every
interleaving of the listed steps, with no depth bound.
-/

def S_ : Nat := 0
def F_ : Nat := 1
def G_ : Nat := 2
def M_ : Nat := 3
def actorName (a : Nat) : String :=
  if a == 0 then "S" else if a == 1 then "F" else if a == 2 then "G" else "M"
def inP (a : Nat) : Bool := a != G_            -- S, F and M work in project P
def lvl (i : Nat) : String := if i == 0 then "project" else "user"
/-- G works in project Q: it reaches only the user graph. -/
def reaches (a i : Nat) : Bool := !(a == G_ && i == 0)

structure Node where
  wTs : Nat
  wBy : Nat
  vTs : Nat
  vBy : Nat
  arch : Bool
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

/-- A reply in Antigravity's queue: a notice (`sync = false`) or a kg_sync
listing, with the marks it commits on delivery and what it shows. -/
structure Item where
  sync : Bool
  at_ : Nat                    -- mark_pushed / mark_synced / mark_seen time
  shown : List (Nat × Nat)     -- (node, w.ts as rendered)
  bad : Nat                    -- contract bits of the rendered push
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

structure St where
  start : Nat
  ns : List Node
  seen : List (Option Nat)      -- S's seen_at
  synced : Nat                  -- last_synced_ts (start_ts when absent)
  pushed : Nat                  -- pushed_ts
  queue : List Item
  pend : List (Option Nat)      -- ghost
  shown : List (Option Nat)     -- ghost: last pushed w.ts
  viol : Nat                    -- ghost
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

structure Cfg where
  k : Nat := 1                  -- MAX_PUSHED (3 in the code; 1 here so 2 nodes overflow)
  writers : List Nat := [S_, F_, G_, M_]
  bumpers : List Nat := [S_, F_, G_, M_]
  promoters : List Nat := [F_, G_]
  archive : Bool := true
  agy : Bool := false           -- Antigravity's deferred view
  drop : Bool := false          -- a hook reply lost after the server answered
  qmax : Nat := 1
  nodes : List Nat := [0, 1]    -- the nodes the steps touch (a scope may freeze one)
  views : Bool := true
  fixWindow : Bool := false
  fixMask : Bool := false
  fixPending : Bool := false

-- contract bits
def vOwn : Nat := 1
def vMaint : Nat := 2
def vArch : Nat := 4
def vProj : Nat := 8
def vOld : Nat := 16
def vTwice : Nat := 32

def omax (o : Option Nat) : Nat := o.getD 0

/-- get_sync_diff(S, since) (store.py:1571-1572), node part. -/
def listed (c : Cfg) (st : St) (since : Nat) (i : Nat) : Bool :=
  let n := st.ns[i]!
  (n.vTs > since && n.vBy != S_)
  || (c.fixMask && n.wTs > since && n.wBy != S_)

/-- foreign.py:36-55: what one notice with window start `since` shows. -/
def render (c : Cfg) (st : St) (since : Nat) : List (Nat × Nat) × Nat :=
  let ok (i : Nat) : Bool :=
    let n := st.ns[i]!
    listed c st since i && !n.arch
    && !(omax st.seen[i]! ≥ n.wTs)
    && n.wBy != M_
    && (i == 0 || inP n.wBy)
    && (!c.fixWindow || n.wTs > since)
  let cs := [0, 1].filter ok
  -- newest written first (foreign.py:53)
  let sorted := if cs.length == 2 && st.ns[1]!.wTs > st.ns[0]!.wTs then [1, 0] else cs
  let shown := sorted.take c.k
  let bad := shown.foldl (fun acc i =>
    let n := st.ns[i]!
    acc ||| (if n.wBy == S_ then vOwn else 0)
        ||| (if n.wBy == M_ then vMaint else 0)
        ||| (if n.arch then vArch else 0)
        ||| (if i == 1 && !inP n.wBy then vProj else 0)
        ||| (if n.wTs ≤ since then vOld else 0)) 0
  (shown.map fun i => (i, st.ns[i]!.wTs), bad)

/-- S receives node i's content as of write stamp w. -/
def deliver (st : St) (i w : Nat) : St :=
  match st.pend[i]! with
  | some p => if p ≤ w then { st with pend := st.pend.set i none } else st
  | none => st

def deliverPush (st : St) (shown : List (Nat × Nat)) (bad : Nat) : St :=
  shown.foldl (fun s (i, w) =>
    let twice := if s.shown[i]! == some w then vTwice else 0
    deliver { s with viol := s.viol ||| twice, shown := s.shown.set i (some w) } i w)
    { st with viol := st.viol ||| bad }

def deliverSync (st : St) (shown : List (Nat × Nat)) : St :=
  shown.foldl (fun s (i, w) => deliver s i w) st

def markSeen (st : St) (ids : List Nat) (at_ : Nat) : St :=
  ids.foldl (fun s i => { s with seen := s.seen.set i (some (max (omax s.seen[i]!) at_)) }) st

def stamps (st : St) : List Nat :=
  [st.start, st.synced, st.pushed]
  ++ st.ns.foldr (fun n acc => n.wTs :: n.vTs :: acc) []
  ++ (st.seen ++ st.pend ++ st.shown).filterMap id
  ++ st.queue.foldr (fun it acc => it.at_ :: it.shown.map Prod.snd ++ acc) []

def maxStamp (st : St) : Nat := (stamps st).foldl max 0

def insSorted (x : Nat) : List Nat → List Nat
  | [] => [x]
  | y :: ys => if x == y then y :: ys else if x < y then x :: y :: ys else y :: insSorted x ys

def canon (st : St) : St :=
  let ranks := (stamps st).foldl (fun acc x => insSorted x acc) [0]
  let r (x : Nat) : Nat := ranks.idxOf x
  let ro (o : Option Nat) : Option Nat := o.map r
  { st with start := r st.start, synced := r st.synced, pushed := r st.pushed,
            ns := st.ns.map fun n => { n with wTs := r n.wTs, vTs := r n.vTs },
            seen := st.seen.map ro, pend := st.pend.map ro, shown := st.shown.map ro,
            queue := st.queue.map fun it =>
              { it with at_ := r it.at_, shown := it.shown.map fun (i, w) => (i, r w) } }

/-- A write S should learn of: by a regular session, user level only from P. -/
def qualifying (a i : Nat) : Bool := a == F_ || (a == G_ && i == 0)

def ids (l : List (Nat × Nat)) : String := toString (l.map fun (i, _) => s!"n{i}")

def next (c : Cfg) (st : St) : List (String × St) :=
  let t := maxStamp st + 1
  let idx := c.nodes
  let writes := c.writers.foldr (fun a acc => idx.foldr (fun i acc2 =>
      if !reaches a i then acc2 else
      let n := st.ns[i]!
      let fresh := { n with wTs := t, wBy := a, vTs := t, vBy := a, arch := false }
      if a == S_ then
        match st.seen[i]! with
        | some s =>
          if s < n.wTs && n.wBy != S_ then
            (s!"S put_node {lvl i} n{i} REFUSED (stale view; the refusal shows it)",
              deliver { st with seen := st.seen.set i (some t) } i n.wTs) :: acc2
          else
            (s!"S writes {lvl i} n{i}",
              { st with ns := st.ns.set i fresh, seen := st.seen.set i (some t),
                        pend := st.pend.set i none }) :: acc2
        | none =>
            (s!"S writes {lvl i} n{i} blind (never saw it; allowed, F11)",
              { st with ns := st.ns.set i fresh, seen := st.seen.set i (some t),
                        pend := st.pend.set i none }) :: acc2
      else
        (s!"{actorName a} writes {lvl i} n{i}",
          { st with ns := st.ns.set i fresh,
                    pend := if qualifying a i then st.pend.set i (some t) else st.pend }) :: acc2)
      acc) []
  let bumps := c.bumpers.foldr (fun a acc => idx.foldr (fun i acc2 =>
      if !reaches a i then acc2 else
      let n := st.ns[i]!
      let what := if a == S_ then "renames" else "renames or re-puts unchanged"
      (s!"{actorName a} {what} n{i} (version bumped, _written kept)",
        { st with ns := st.ns.set i { n with vTs := t, vBy := a } }) :: acc2) acc) []
  let promotes := c.promoters.foldr (fun a acc => idx.foldr (fun i acc2 =>
      let n := st.ns[i]!
      if !reaches a i || !n.arch then acc2 else
      (s!"{actorName a} reads archived n{i} (promoted; version bumped)",
        { st with ns := st.ns.set i { n with vTs := t, vBy := a, arch := false } }) :: acc2) acc) []
  let views := if !c.views then [] else idx.map fun i =>
      let n := st.ns[i]!
      let n' := if n.arch then { n with vTs := t, vBy := S_, arch := false } else n
      (s!"S reads n{i}", deliver { st with ns := st.ns.set i n', seen := st.seen.set i (some t) } i n.wTs)
  let archives := if !c.archive then [] else idx.filterMap fun i =>
      let n := st.ns[i]!
      if n.arch then none else
      some (s!"compaction archives n{i}", { st with ns := st.ns.set i { n with arch := true } })
  let syncRep := ([0, 1].filter fun i => listed c st st.synced i).map fun i => (i, st.ns[i]!.wTs)
  let since0 := max st.start (max st.synced st.pushed)
  -- fixPending: a notice counts the marks of replies already queued as made.
  let since := if c.agy && c.fixPending then st.queue.foldl (fun m it => max m it.at_) since0 else since0
  let rst := if c.agy && c.fixPending then
      st.queue.foldl (fun s it => if it.sync then s else markSeen s (it.shown.map Prod.fst) it.at_) st
    else st
  let (pshown, pbad) := render c rst since
  let pids := pshown.map Prod.fst
  let notices : List (String × St) :=
    if !c.agy then
      [(s!"S hook or tool reply: push window ({since},{t}] shows {ids pshown}",
          deliverPush (markSeen { st with pushed := t } pids t) pshown pbad),
       (s!"S kg_sync lists {ids syncRep}", deliverSync { st with synced := t } syncRep)]
      ++ (if c.drop then
        [(s!"S hook reply with window ({since},{t}] showing {ids pshown} LOST (curl timed out)",
          markSeen { st with pushed := t } pids t)] else [])
    else
      let inl := [(s!"S reply inline: push window ({since},{t}] shows {ids pshown}",
                    deliverPush (markSeen { st with pushed := max st.pushed t } pids t) pshown pbad),
                  (s!"S kg_sync inline lists {ids syncRep}",
                    deliverSync { st with synced := max st.synced t } syncRep)]
      let q := if st.queue.length ≥ c.qmax then [] else
        [(s!"S reply queued: push window ({since},{t}] carries {ids pshown}",
            { st with queue := st.queue ++ [{ sync := false, at_ := t, shown := pshown, bad := pbad }] }),
         (s!"S kg_sync queued, lists {ids syncRep}",
            { st with queue := st.queue ++ [{ sync := true, at_ := t, shown := syncRep, bad := 0 }] })]
      let d := match st.queue with
        | [] => []
        | it :: rest =>
          let st' := { st with queue := rest }
          if it.sync then
            [(s!"hook delivers queued kg_sync ({ids it.shown}); ack commits",
              deliverSync { st' with synced := max st.synced it.at_ } it.shown)]
          else
            [(s!"hook delivers queued notice ({ids it.shown}); ack commits",
              deliverPush (markSeen { st' with pushed := max st.pushed it.at_ } (it.shown.map Prod.fst) it.at_)
                it.shown it.bad)]
      inl ++ q ++ d
  (writes ++ bumps ++ promotes ++ views ++ archives ++ notices).map fun (l, s) => (l, canon s)

/-- X-cover: a qualifying write S never received that no kg_sync will list. -/
def lost (c : Cfg) (st : St) : Bool :=
  [0, 1].any fun i => st.pend[i]!.isSome && !listed c st st.synced i

/-- X-mark: seen_at claims S saw a qualifying write it never received (the push
filter and put_node's stale-view guard both trust seen_at). -/
def suppressed (st : St) : Bool :=
  [0, 1].any fun i =>
    match st.pend[i]! with
    | some p => omax st.seen[i]! ≥ p
    | none => false

/-- One breadth-first pass over the whole reachable space, checking every
property on every state: the first violation found for a property is a
shortest trace to it. -/
partial def bfs (c : Cfg) (props : Array (St → Bool)) (init : St) :
    IO (Array (Option (List String)) × Nat) := do
  let mut seen : Std.HashSet St := ({} : Std.HashSet St).insert init
  let mut frontier : Array (St × List String) := #[(init, [])]
  let mut found : Array (Option (List String)) := props.map fun _ => none
  let mut n := 0
  while !frontier.isEmpty do
    let mut nextF := #[]
    for (s, tr) in frontier do
      n := n + 1
      for k in [0:props.size] do
        if found[k]!.isNone && props[k]! s then
          found := found.set! k (some tr.reverse)
      for (l, s') in next c s do
        if !seen.contains s' then
          seen := seen.insert s'
          nextF := nextF.push (s', l :: tr)
    frontier := nextF
  return (found, n)

def node0 : Node := { wTs := 1, wBy := F_, vTs := 1, vBy := F_, arch := false }
def init : St :=
  canon { start := 2, ns := [node0, node0], seen := [none, none], synced := 2, pushed := 0,
          queue := [], pend := [none, none], shown := [none, none], viol := 0 }

def bit (b : Nat) (st : St) : Bool := st.viol &&& b != 0

def contract (c : Cfg) : List (String × (St → Bool)) :=
  [ ("X-own     never pushed back to its writer", bit vOwn),
    ("X-maint   never a maintenance session's write", bit vMaint),
    ("X-arch    never an archived node", bit vArch),
    ("X-proj    user level only from S's project", bit vProj),
    ("X-new     only a write made inside the window", bit vOld),
    ("X-once    no write pushed twice", bit vTwice),
    ("X-cover   every qualifying write is received or still listed by kg_sync", lost c),
    ("X-mark    seen_at never covers a write S did not receive", suppressed),
    ("sanity (expect ✗): a foreign write is pushed", fun st => st.shown.any Option.isSome) ]

def runAll (title : String) (c : Cfg) : IO Unit := do
  IO.println s!"--- {title} (K={c.k}, fixWindow={c.fixWindow}, fixMask={c.fixMask}{if c.agy then s!", fixPending={c.fixPending}" else ""})"
  let props := (contract c).toArray
  let (found, n) ← bfs c (props.map Prod.snd) init
  IO.println s!"    {n} states, exhaustive"
  for k in [0:props.size] do
    match found[k]! with
    | some tr => do
        IO.println s!"✗ {props[k]!.1}: COUNTEREXAMPLE ({tr.length} steps)"
        for l in tr do IO.println s!"    {l}"
    | none => IO.println s!"✓ {props[k]!.1}: no counterexample"
  (← IO.getStdout).flush

def main (args : List String) : IO Unit := do
  let only := args.head?
  let run (key title : String) (c : Cfg) : IO Unit :=
    if only.isNone || only == some key then runAll title c else pure ()
  IO.println "Bounds: one observer S; F, G, M and S itself act; one node per scope (two in"
  IO.println "the overflow scope); every interleaving of the scope's steps, searched to the"
  IO.println "end (canonical timestamps keep the space finite)."
  let fx : Cfg := { fixWindow := true, fixMask := true }
  -- Claude Code and Codex: hook replies and MCP tool replies claim at once.
  run "project" "project node, every step, every reply delivered" { nodes := [0] }
  run "project" "project node + fixWindow" { nodes := [0], fixWindow := true }
  run "project" "project node + fixWindow + fixMask" { fx with nodes := [0] }
  run "user" "user node, every step, every reply delivered" { nodes := [1] }
  run "user" "user node + fixWindow + fixMask" { fx with nodes := [1] }
  -- Two nodes, K=1: the overflow pointer and newest-first order.
  let ov : Cfg := { writers := [F_, G_], bumpers := [], promoters := [], archive := false }
  run "overflow" "two nodes, K=1, writes, reads, kg_sync and notices" ov
  run "overflow" "two nodes, K=1 + fixWindow + fixMask" { ov with fixWindow := true, fixMask := true }
  -- A hook reply the client gave up on (curl --max-time 1).
  run "drop" "project node + a hook reply lost after the server answered"
    { fx with nodes := [0], drop := true }
  -- Antigravity: marks deferred to delivery; inline, queued (QMAX=1) or refused.
  let ag : Cfg := { nodes := [0], agy := true, qmax := 1, writers := [S_, F_], bumpers := [F_],
                    promoters := [], archive := false }
  run "agy" "Antigravity deferred view, project node; S and F write, F renames" ag
  run "agy" "Antigravity deferred view + fixWindow + fixMask" { ag with fixWindow := true, fixMask := true }
  run "agy" "Antigravity deferred view + all fixes"
    { ag with fixWindow := true, fixMask := true, fixPending := true }
