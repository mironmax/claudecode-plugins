import Std
/-!
Model of paged kg_read replies (0.14.0), after knowledge-graph/server/
mcp_http/paging.py, session_manager.py (set_pages, take_page, reset_context,
mark_seen, note_viewed) and store.py (put_node's F11 guard).

One session S reads two nodes, either with kg_read(ids=[n0, n1]) (each node
a block: a header line with the gist, then notes lines) or as part of a full
read (one gist line per node). The reply is cut into parts of `cap` lines
(paging.split packs whole lines; equal lines make that a fixed count).
Part 1 goes out with the reply; later parts with kg_read(more=true). Each
part carries its share of the read's effects (paging.assign):
  seen, seen_at, read_at (node read: mark_seen full=True, at=view time),
  the promotion (record_read), and, for the full read, the full-read flag
  (last part).
Where an id's effects go:
  current — the first part that shows its line (paging.py:51-81);
  fixed   — for a node block, the part holding the block's last line.
Other events: a compaction (reset_context: drops seen, the flag and pending
parts; keeps view times), another session T writing a node's notes, S
writing a node from what it holds (put_node, store.py:807-835: refused when
T wrote after S's last view, or when S has not read the stored notes in
their current form), the periodic save, and a server restart (the session
record falls back to its last save: set_pages/take_page save before the
part's effects are applied, paging.py:84-116, session_manager.py:572-597).
Every handler body is atomic (one event loop; the session lock).

Ghost state records what actually reached S. Properties:
  P1 seen ⊆ gists delivered since the last compaction; promoted ⊆ headers delivered
  P2 read_at(n) only for a node whose whole block reached S at that view time or later
  P3 the full-read flag only after the full read's last part reached S
  P4 an accepted write of S never drops notes S did not receive
-/

structure Shape where
  len0 : Nat   -- lines of node 0's block in a node read
  len1 : Nat
  cap : Nat    -- lines per part
  deriving Repr

structure Pg where
  full : Bool          -- full-graph read (else node read of both nodes)
  viewT : Nat
  next : Nat           -- index of the next part to deliver
  v0 : Nat             -- node versions rendered
  v1 : Nat
  deriving DecidableEq, Repr, Hashable, BEq

structure Sess where
  seen : Nat           -- bitmask
  seenAt0 : Nat        -- 0 = never; times start at 2
  seenAt1 : Nat
  readAt0 : Nat
  readAt1 : Nat
  fullRead : Bool
  pages : Option Pg
  deriving DecidableEq, Repr, Hashable, BEq

structure S where
  t : Nat
  ses : Sess
  saved : Sess
  ver0 : Nat
  ver1 : Nat
  wTs0 : Nat
  wTs1 : Nat
  wT0 : Bool           -- last content write by another session
  wT1 : Bool
  promoted : Nat       -- graph: nodes S's reads promoted
  -- ghost: what reached S
  ctxGist : Nat        -- gists delivered since the last compaction
  everHdr : Nat
  fullV0 : Nat         -- latest version whose whole block reached S
  fullV1 : Nat
  fullT0 : Nat         -- latest view time of a whole block that reached S
  fullT1 : Nat
  fullDone : Bool      -- a full read's last part reached S since the last compaction
  lost : Bool
  -- budgets
  reads : Nat
  tw : Nat
  puts : Nat
  comps : Nat
  rs : Nat
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  sh : Shape
  fixed : Bool

def bit (n : Nat) : Nat := if n == 0 then 1 else 2
def has (m n : Nat) : Bool := m &&& bit n != 0

def hdr (sh : Shape) (full : Bool) (n : Nat) : Nat :=
  if full then n else if n == 0 then 0 else sh.len0
def lst (sh : Shape) (full : Bool) (n : Nat) : Nat :=
  if full then n else if n == 0 then sh.len0 - 1 else sh.len0 + sh.len1 - 1
def nParts (sh : Shape) (full : Bool) : Nat :=
  let lines := if full then 2 else sh.len0 + sh.len1
  (lines + sh.cap - 1) / sh.cap

/-- paging.assign: the part that carries node n's effects. -/
def home (c : Cfg) (full : Bool) (n : Nat) : Nat :=
  if c.fixed && !full then lst c.sh full n / c.sh.cap else hdr c.sh full n / c.sh.cap

def setN (n : Nat) (a b v : Nat) : Nat × Nat := if n == 0 then (v, b) else (a, v)

/-- One part reaches S (paging._deliver → delivery.apply_effects). -/
def deliver (c : Cfg) (st : S) (pg : Pg) (p : Nat) : S := Id.run do
  let mut s := st
  for n in [0, 1] do
    let ver := if n == 0 then pg.v0 else pg.v1
    if hdr c.sh pg.full n / c.sh.cap == p then
      s := { s with ctxGist := s.ctxGist ||| bit n, everHdr := s.everHdr ||| bit n }
    if !pg.full && lst c.sh pg.full n / c.sh.cap == p then
      let (a, b) := setN n s.fullV0 s.fullV1 (max ver (if n == 0 then s.fullV0 else s.fullV1))
      let (x, y) := setN n s.fullT0 s.fullT1 (max pg.viewT (if n == 0 then s.fullT0 else s.fullT1))
      s := { s with fullV0 := a, fullV1 := b, fullT0 := x, fullT1 := y }
    if home c pg.full n == p then
      let e := s.ses
      let (sa, sb) := setN n e.seenAt0 e.seenAt1 (max pg.viewT (if n == 0 then e.seenAt0 else e.seenAt1))
      let e := { e with seen := e.seen ||| bit n, seenAt0 := sa, seenAt1 := sb }
      let e := if pg.full then e else
        let (ra, rb) := setN n e.readAt0 e.readAt1 (max pg.viewT (if n == 0 then e.readAt0 else e.readAt1))
        { e with readAt0 := ra, readAt1 := rb }
      s := { s with ses := e, promoted := if pg.full then s.promoted else s.promoted ||| bit n }
  if pg.full && p + 1 == nParts c.sh true then
    s := { s with ses := { s.ses with fullRead := true }, fullDone := true }
  return s

def acts (c : Cfg) (st : S) : List (String × S) := Id.run do
  let mut out : List (String × S) := []
  -- kg_read: render, set_pages (saved), deliver part 1. A new read replaces pending parts.
  if st.reads < 2 then
    for full in [false, true] do
      let k := nParts c.sh full
      let pg : Pg := { full, viewT := st.t, next := 1, v0 := st.ver0, v1 := st.ver1 }
      let ses := { st.ses with pages := if k > 1 then some pg else none }
      let s1 := { st with t := st.t + 1, reads := st.reads + 1, ses, saved := ses }
      out := out ++ [(s!"S kg_read({if full then "full graph" else "ids=[n0,n1]"}) at {st.t}: part 1 of {k} goes out",
                      deliver c s1 pg 0)]
  -- kg_read(more=true): take_page (saved), deliver
  match st.ses.pages with
  | some pg =>
      let k := nParts c.sh pg.full
      let ses := { st.ses with pages := if pg.next + 1 < k then some { pg with next := pg.next + 1 } else none }
      out := out ++ [(s!"S kg_read(more=true): part {pg.next + 1} of {k} goes out",
                      deliver c { st with ses, saved := ses } pg pg.next)]
  | none => pure ()
  -- compaction: reset_context (saved)
  if st.comps < 1 then
    let ses := { st.ses with seen := 0, fullRead := false, pages := none }
    out := out ++ [("compaction (reset_context)",
      { st with ses, saved := ses, ctxGist := 0, fullDone := false, comps := st.comps + 1 })]
  -- another session writes a node's notes
  if st.tw < 1 then
    for n in [0, 1] do
      let s1 := if n == 0 then { st with ver0 := st.ver0 + 1, wTs0 := st.t, wT0 := true }
                else { st with ver1 := st.ver1 + 1, wTs1 := st.t, wT1 := true }
      out := out ++ [(s!"T writes n{n}'s notes at {st.t}", { s1 with t := st.t + 1, tw := st.tw + 1 })]
  -- S writes a node's notes from what it holds (it has seen at least its header)
  if st.puts < 1 then
    for n in [0, 1] do
      if has st.everHdr n then
        let e := st.ses
        let (byT, wTs, seenAt, readAt, ver, fv) :=
          if n == 0 then (st.wT0, st.wTs0, e.seenAt0, e.readAt0, st.ver0, st.fullV0)
          else (st.wT1, st.wTs1, e.seenAt1, e.readAt1, st.ver1, st.fullV1)
        let stale := byT && seenAt != 0 && seenAt < wTs
        let unread := readAt == 0 || (byT && readAt < wTs)
        let t := st.t
        let view (s : S) (v : Nat) : S :=     -- a full view of n at t (refusal or own write)
          let (sa, sb) := setN n s.ses.seenAt0 s.ses.seenAt1 t
          let (ra, rb) := setN n s.ses.readAt0 s.ses.readAt1 t
          let (a, b) := setN n s.fullV0 s.fullV1 v
          let (x, y) := setN n s.fullT0 s.fullT1 t
          { s with ses := { s.ses with seenAt0 := sa, seenAt1 := sb, readAt0 := ra, readAt1 := rb },
                   fullV0 := a, fullV1 := b, fullT0 := x, fullT1 := y }
        let s0 := { st with t := t + 1, puts := st.puts + 1 }
        if stale || unread then
          out := out ++ [(s!"S put_node n{n} REFUSED ({if stale then "changed after its view" else "notes it has not read"})",
                          view s0 ver)]
        else
          let s1 := if n == 0 then { s0 with ver0 := ver + 1, wTs0 := t, wT0 := false }
                    else { s0 with ver1 := ver + 1, wTs1 := t, wT1 := false }
          let s1 := { s1 with lost := st.lost || fv != ver }
          out := out ++ [(s!"S put_node n{n} accepted (it holds n{n}'s notes in full at v{fv}; stored v{ver})",
                          view s1 (ver + 1))]
  -- periodic save; server restart (session record from its last save)
  if st.saved != st.ses then
    out := out ++ [("periodic save of sessions", { st with saved := st.ses })]
  if st.rs < 1 && st.saved != st.ses then
    out := out ++ [("server restart: session record from its last save",
                    { st with ses := st.saved, rs := st.rs + 1 })]
  return out

def seenOk (st : S) : Bool := st.ses.seen &&& (3 - st.ctxGist) == 0 && st.promoted &&& (3 - st.everHdr) == 0
def readOk (st : S) : Bool := st.ses.readAt0 ≤ st.fullT0 && st.ses.readAt1 ≤ st.fullT1
def flagOk (st : S) : Bool := !st.ses.fullRead || st.fullDone

/-- Exhaustive BFS; for each property, the shortest trace to a state breaking it. -/
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

def blank : Sess := { seen := 0, seenAt0 := 0, seenAt1 := 0, readAt0 := 0, readAt1 := 0,
                      fullRead := false, pages := none }
-- Both nodes exist, last written by another session at time 1.
def init : S :=
  { t := 2, ses := blank, saved := blank, ver0 := 1, ver1 := 1, wTs0 := 1, wTs1 := 1,
    wT0 := true, wT1 := true, promoted := 0, ctxGist := 0, everHdr := 0, fullV0 := 0,
    fullV1 := 0, fullT0 := 0, fullT1 := 0, fullDone := false, lost := false,
    reads := 0, tw := 0, puts := 0, comps := 0, rs := 0 }

def shapes : List Shape := Id.run do
  let mut out := []
  for a in [1, 2, 3] do
    for b in [1, 2, 3] do
      for c in [1, 2, 3] do
        out := out ++ [{ len0 := a, len1 := b, cap := c }]
  return out

def props : List (String × (S → Bool)) :=
  [("P1 seen ⊆ gists delivered; promoted ⊆ headers delivered", fun s => !seenOk s),
   ("P2 read_at only after the node's whole block reached S", fun s => !readOk s),
   ("P3 full-read flag only after the read's last part", fun s => !flagOk s),
   ("P4 an accepted write never drops notes S did not receive", fun s => s.lost),
   ("sanity (expect ✗): a full read completes and a write of S goes through",
    fun s => s.ses.fullRead && (!s.wT0 || !s.wT1))]

def runAll (fixed : Bool) : IO Unit := do
  IO.println s!"--- fixed={fixed}  (≤2 reads, ≤1 compaction, ≤1 foreign write, ≤1 write by S, ≤1 restart)"
  let mut total := 0
  let mut fails : Array Nat := props.toArray.map (fun _ => 0)
  let mut best : Array (Option (Shape × List String)) := props.toArray.map (fun _ => none)
  for sh in shapes do
    let (found, n) := bfs { sh, fixed } (props.map (·.2)) init
    total := total + n
    for i in [0:props.length] do
      if let some tr := found[i]! then
        fails := fails.set! i (fails[i]! + 1)
        match best[i]! with
        | none => best := best.set! i (some (sh, tr))
        | some (_, tr0) => if tr.length < tr0.length then best := best.set! i (some (sh, tr))
  for i in [0:props.length] do
    let name := (props.getD i ("", fun _ => false)).1
    match best[i]! with
    | none => IO.println s!"✓ {name}: no counterexample ({total} states over {shapes.length} reply shapes, exhaustive)"
    | some (sh, tr) =>
        IO.println s!"✗ {name}: COUNTEREXAMPLE in {fails[i]!} of {shapes.length} reply shapes; shortest (n0 {sh.len0} lines, n1 {sh.len1} lines, {sh.cap} lines a part):"
        for l in tr do IO.println s!"    {l}"

def main : IO Unit := do
  runAll false
  runAll true
