import Std
/-!
Model of Antigravity's deferred delivery (0.11.0), after knowledge-graph/
server/mcp_http/antigravity.py (call_with_delivery, handle_event),
delivery.py (DeferredView, enqueue, prepare, restart, acknowledge),
session_manager.py (queue_context, prepare_context, acknowledge_context,
reset_context), rest.py (/api/antigravity/ack), hooks/kg-agy.py (print, then
ack) and store.py put_node (the F11 guard).

One conversation S. Replies over 3,500 bytes are queued; a hook (PreInvocation)
first looks for a new CHECKPOINT (reset_context + restart: offsets to 0, the
outstanding packet dropped), queues a preload when the session has none
(first: ahead of everything unless a packet is out), then prepares a packet
of at most CAP chunks from the head of the queue (the outstanding one if any)
and prints it; the hook process then acks it, or the ack is lost or late.
Only the ack of the outstanding packet commits; an item's effects apply when
its last chunk is acked.

Items: P preload (1 chunk; shows a's gist when a ranks in the preload now);
R full read (2 chunks; shows a's gist, or a bare "(preloaded)" anchor when a
was in the committed preload at render; effects: seen, the full-read flag);
Q kg_search reply carrying the "other sessions wrote" notice for node X
(1 chunk; the notice marks X seen at its build time, foreign.py:56).
Other events: an inline kg_read of X (committed at once, as a reply ≤3,500
bytes is), another session T writing X, the graph changing so a preload
built now would (not) show a, S writing X from the newest version it
received (refused when T wrote after S's last view), a compaction (context
gone; detected at the next hook). A server restart needs no event of its own:
queue, outstanding packet and session record are saved at every queue,
prepare and ack (session_manager.py:562-625), so a restart only loses the
replies of hooks in flight, which "ack lost" covers; the next hook re-sends
the persisted packet. Queue bound: QMAX items (QUEUE_ITEMS / QUEUE_BYTES).

Variants: fix2 — the notice's seen mark goes through the deferred view;
fix3 — the full read marks seen only the gists it shows, not preloaded
anchors; fix4 — the preload is exempt from the queue bound.
-/

inductive Kind | P | R | Q
  deriving DecidableEq, Repr, Hashable, BEq

structure Item where
  uid : Nat
  kind : Kind
  len : Nat
  off : Nat
  bt : Nat          -- build (view) time
  xv : Nat          -- Q: version of X its notice shows
  showsA : Bool     -- P, R: a's gist is shown
  ctxEnd : Nat      -- ghost: chunks 0..ctxEnd-1 are in the current context
  deriving DecidableEq, Repr, Hashable, BEq

structure Pkt where
  id : Nat
  sel : List (Nat × Nat)   -- (item uid, chunk end)
  deriving DecidableEq, Repr, Hashable, BEq

structure S where
  t : Nat
  queue : List Item
  pkt : Option Pkt
  printed : List Nat        -- packets printed whose ack is still to come
  nextId : Nat
  -- session record
  seenA : Bool
  preloaded : Bool
  preA : Bool
  fullRead : Bool
  seenAtX : Nat
  -- graph
  xVer : Nat
  xWTs : Nat
  xByT : Bool
  aTop : Bool
  -- ghost
  cpPending : Bool
  ctxA : Bool
  fullDone : Bool
  knownX : Nat
  fresh : Bool              -- a checkpoint was handled; nothing printed since
  resetId : Nat
  applied : List Nat
  bad : Nat                 -- 1 refusal changed state, 2 effects applied twice, 4 replay before preload,
                            -- 8 pre-checkpoint ack accepted, 16 lost update, 32 order error, 64 item dropped
  -- budgets (the clock ticks only on events that take a time)
  nR : Nat
  nQ : Nat
  nK : Nat
  nT : Nat
  nShift : Nat
  nPut : Nat
  nComp : Nat
  hooks : Nat
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  fix2 : Bool
  fix3 : Bool
  fix4 : Bool

def CAP : Nat := 2
def QMAX : Nat := 2

/-- delivery.enqueue: refuse at the bound; `first` goes to the head unless a packet is out. -/
def enqueue (c : Cfg) (st : S) (it : Item) (first : Bool) : Option S :=
  if st.queue.length ≥ QMAX && !(first && c.fix4) then none
  else
    let q := if first && st.pkt.isNone then it :: st.queue else st.queue ++ [it]
    some { st with queue := q, nextId := st.nextId + 1 }

/-- delivery.prepare: the outstanding packet, or up to CAP chunks from the head. -/
def prepare (st : S) : S := Id.run do
  if st.pkt.isSome || st.queue.isEmpty then return st
  let mut room := CAP
  let mut sel : List (Nat × Nat) := []
  for it in st.queue do
    if room == 0 then break
    let take := min room (it.len - it.off)
    sel := sel ++ [(it.uid, it.off + take)]
    room := room - take
    if it.off + take < it.len then break
  return { st with pkt := some { id := st.nextId, sel }, nextId := st.nextId + 1 }

/-- The hook prints the packet: its chunks reach the model (ghost only). -/
def print (st : S) : S := Id.run do
  let some p := st.pkt | return st
  let mut s := st
  let mut q := []
  for it in s.queue do
    match p.sel.find? (·.1 == it.uid) with
    | none => q := q ++ [it]
    | some (_, e) =>
        let it := if it.off ≤ it.ctxEnd then { it with ctxEnd := max it.ctxEnd e } else it
        if it.kind == Kind.P && it.showsA then s := { s with ctxA := true }
        if it.kind == Kind.R && it.showsA && it.off == 0 then s := { s with ctxA := true }
        if it.kind == Kind.R && it.ctxEnd == it.len then s := { s with fullDone := true }
        if it.kind == Kind.Q then s := { s with knownX := max s.knownX it.xv }
        q := q ++ [it]
  let firstKind := (p.sel.head?.bind fun (u, _) => s.queue.find? (·.uid == u)).map (·.kind)
  let b := if s.fresh && firstKind != some Kind.P then s.bad ||| 4 else s.bad
  return { s with queue := q, fresh := false, bad := b, printed := (s.printed ++ [p.id]).take 2 }

/-- delivery.acknowledge + apply_effects. -/
def apply (c : Cfg) (st : S) (it : Item) : S :=
  let st := { st with applied := st.applied ++ [it.uid],
                      bad := if st.applied.contains it.uid then st.bad ||| 2 else st.bad }
  match it.kind with
  | Kind.P => { st with preloaded := true, preA := it.showsA, seenA := st.seenA || it.showsA }
  | Kind.R => { st with fullRead := true, seenA := st.seenA || (if c.fix3 then it.showsA else true) }
  | Kind.Q => if c.fix2 then { st with seenAtX := max st.seenAtX it.bt } else st

def ack (c : Cfg) (st : S) (id : Nat) : String × S := Id.run do
  let st := { st with printed := st.printed.erase id }
  match st.pkt with
  | some p =>
    if p.id != id then return (s!"ack of packet {id} refused (outstanding: {p.id})", st)
    let mut s := { st with bad := if id < st.resetId then st.bad ||| 8 else st.bad }
    for (u, e) in p.sel do
      match s.queue with
      | [] => s := { s with bad := s.bad ||| 32 }
      | it :: rest =>
        if it.uid != u then s := { s with bad := s.bad ||| 32 }
        else if e < it.len then s := { s with queue := { it with off := e } :: rest }
        else s := apply c { s with queue := rest } it
    return (s!"ack of packet {id} commits", { s with pkt := none })
  | none => return (s!"ack of packet {id} refused (none outstanding)", st)

def hook (c : Cfg) (st : S) : String × S := Id.run do
  let mut s := { st with hooks := st.hooks + 1 }
  let mut label := "hook"
  if s.cpPending then   -- antigravity.py:228-231, session_manager.reset_context, delivery.restart
    s := { s with seenA := false, preloaded := false, preA := false, fullRead := false,
                  queue := s.queue.map (fun it => { it with off := 0 }), pkt := none,
                  cpPending := false, fresh := true, resetId := s.nextId }
    label := label ++ ": new CHECKPOINT, context reset"
  if !s.preloaded && !(s.queue.any (·.kind == Kind.P)) then   -- antigravity.py:233-244
    let it : Item := { uid := s.nextId, kind := Kind.P, len := 1, off := 0, bt := 0, xv := 0,
                       showsA := s.aTop, ctxEnd := 0 }
    match enqueue c s it true with
    | some s' => s := s'; label := label ++ s!", preload queued (shows a: {it.showsA})"
    | none => label := label ++ ", preload REFUSED (queue full)"
  s := print (prepare s)
  let shown := match s.pkt with
    | some p => s!", prints packet {p.id}: {p.sel.map (fun (u, e) => s!"#{u} to chunk {e}")}"
    | none => ", nothing to print"
  return (label ++ shown, s)

def acts (c : Cfg) (st : S) : List (String × S) := Id.run do
  let mut out : List (String × S) := []
  let t := st.t
  let s1 := { st with t := t + 1 }
  if st.nR < 1 then   -- full read: R's anchors follow the committed preload
    let it : Item := { uid := st.nextId, kind := Kind.R, len := 2, off := 0, bt := 0, xv := 0,
                       showsA := !st.preA, ctxEnd := 0 }
    out := out ++ [match enqueue c { st with nR := 1 } it false with
      | some s => (s!"S kg_read(full) queued as R#{it.uid} (a {if it.showsA then "shown" else "as anchor"})", s)
      | none => ("S kg_read(full) refused: queue full", { st with nR := 1 })]
  if st.nQ < 1 && st.xByT && st.seenAtX < st.xWTs then   -- kg_search reply carrying the notice for X
    let it : Item := { uid := st.nextId, kind := Kind.Q, len := 1, off := 0, bt := t, xv := st.xVer,
                       showsA := false, ctxEnd := 0 }
    let pre := if c.fix2 then s1 else { s1 with seenAtX := max s1.seenAtX t }   -- foreign.py:56
    out := out ++ [match enqueue c { pre with nQ := 1 } it false with
      | some s => (s!"S kg_search queued as Q#{it.uid} (notice: X v{st.xVer})", s)
      | none => ("S kg_search refused: queue full",
                 { pre with nQ := 1, bad := if pre.seenAtX != st.seenAtX then pre.bad ||| 1 else pre.bad })]
  if st.nK < 1 then
    out := out ++ [(s!"S kg_read(id=X) inline: sees X v{st.xVer}",
      { s1 with nK := 1, knownX := max st.knownX st.xVer, seenAtX := max st.seenAtX t })]
  if st.nT < 1 then
    out := out ++ [(s!"T writes X (v{st.xVer + 1}) at {t}", { s1 with nT := 1, xVer := st.xVer + 1, xWTs := t, xByT := true })]
  if st.nShift < 1 then
    out := out ++ [(s!"graph changes: a preload built now {if st.aTop then "drops" else "shows"} a",
                    { st with nShift := 1, aTop := !st.aTop })]
  if st.nPut < 1 && st.knownX > 0 then   -- store.put_node, gist-only write of X
    if st.xByT && st.seenAtX != 0 && st.seenAtX < st.xWTs then
      out := out ++ [("S put_node X REFUSED (conflict reply shows X)",
        { s1 with nPut := 1, knownX := st.xVer, seenAtX := t })]
    else
      out := out ++ [(s!"S put_node X accepted, built on v{st.knownX} (stored v{st.xVer})",
        { s1 with nPut := 1, xVer := st.xVer + 1, xWTs := t, xByT := false, knownX := st.xVer + 1,
                  seenAtX := t,
                  -- the guard passed on a recorded view (a write with none is blind, allowed by F11)
                  bad := if st.seenAtX != 0 && st.knownX < st.xVer then st.bad ||| 16 else st.bad })]
  if st.hooks < 4 then
    out := out ++ [hook c st]
  for id in st.printed do
    out := out ++ [ack c st id, (s!"ack of packet {id} lost", { st with printed := st.printed.erase id })]
  if st.nComp < 1 then
    out := out ++ [("compaction (CHECKPOINT row written)",
      { st with nComp := 1, cpPending := true, ctxA := false, fullDone := false,
                queue := st.queue.map (fun it => { it with ctxEnd := 0 }) })]
  return out

partial def bfs (c : Cfg) (props : List (S → Bool)) (init : S) :
    Array (Option (List String)) × Nat := Id.run do
  let mut seen : Std.HashSet S := ({} : Std.HashSet S).insert init
  let mut frontier : Array (S × List String) := #[(init, [])]
  let mut found : Array (Option (List String)) := props.toArray.map (fun _ => none)
  let mut n := 0
  while !frontier.isEmpty do
    let mut nextF := #[]
    for (s, tr) in frontier do
      n := n + 1
      for i in [0:props.length] do
        if found[i]!.isNone && (props.getD i (fun _ => false)) s then
          found := found.set! i (some tr.reverse)
      for (l, s') in acts c s do
        if !seen.contains s' then
          seen := seen.insert s'
          nextF := nextF.push (s', l :: tr)
    frontier := nextF
  return (found, n)

def init : S :=
  { t := 2, queue := [], pkt := none, printed := [], nextId := 1,
    seenA := false, preloaded := false, preA := false, fullRead := false, seenAtX := 0,
    xVer := 1, xWTs := 1, xByT := true, aTop := true,
    cpPending := false, ctxA := false, fullDone := false, knownX := 0, fresh := false,
    resetId := 0, applied := [], bad := 0,
    nR := 0, nQ := 0, nK := 0, nT := 0, nShift := 0, nPut := 0, nComp := 0, hooks := 0 }

-- State properties are checked at hook boundaries: no compaction awaiting detection.
def settled (s : S) : Bool := !s.cpPending
def props : List (String × (S → Bool)) :=
  [("A1 seen(a) ⇒ a's gist is in the current context", fun s => settled s && s.seenA && !s.ctxA),
   ("A2 full-read flag ⇒ every chunk of the read reached the current context",
      fun s => settled s && s.fullRead && !s.fullDone),
   ("A3 a refused reply changes nothing", fun s => s.bad &&& 1 != 0),
   ("A4 no item's effects applied twice; acks match the queue head", fun s => s.bad &&& 34 != 0),
   ("A5 a recorded view never lets through a write built on a version S did not receive",
      fun s => s.bad &&& 16 != 0),
   ("A6 after a checkpoint the preload is the first thing delivered", fun s => s.bad &&& 4 != 0),
   ("A7 an ack of a packet made before a handled checkpoint is refused", fun s => s.bad &&& 8 != 0),
   ("sanity (expect ✗): R replayed after a checkpoint, complete, write of S accepted",
      fun s => s.nComp == 1 && settled s && s.fullRead && s.nPut == 1 && !s.xByT)]

def runAll (c : Cfg) : IO Unit := do
  IO.println s!"--- fix2={c.fix2} fix3={c.fix3} fix4={c.fix4}  (QMAX={QMAX} items, {CAP} chunks a packet, ≤4 hooks, one of each other event)"
  let (found, n) := bfs c (props.map (·.2)) init
  for i in [0:props.length] do
    let name := (props.getD i ("", fun _ => false)).1
    match found[i]! with
    | none => IO.println s!"✓ {name}: no counterexample ({n} states, exhaustive)"
    | some tr =>
        IO.println s!"✗ {name}: COUNTEREXAMPLE ({tr.length} steps)"
        for l in tr do IO.println s!"    {l}"

def main : IO Unit := do
  runAll { fix2 := false, fix3 := false, fix4 := false }   -- the code before the fixes
  runAll { fix2 := true, fix3 := false, fix4 := true }     -- X3 alone
  runAll { fix2 := true, fix3 := true, fix4 := false }     -- X4 alone
  runAll { fix2 := true, fix3 := true, fix4 := true }      -- all fixes
