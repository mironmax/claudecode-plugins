import Std
/-!
Usefulness accounting and the maintenance-session rules (0.13.0 / 0.14.0),
after knowledge-graph/server/mcp_http/store.py (line numbers at ecc3dc1),
core/recurrence.py, core/scorer.py:96-110 and mcp_http/session_manager.py.

Every store method below runs under `store.lock` (an RLock), so each one is one
atomic step; interleaving concurrent sessions is interleaving these steps.

Actors: work sessions A (author of lesson L) and B, and a pass M that declares
itself maintenance (kg_read maintenance=true, mcp_streamable_server.py:501).
Nodes: lesson L (id "a", renamable once to "b") and node N (id "n").
The vote ledger `liked_ids` is keyed by node id, as in the code.
Caps are scaled: hard cap HARD=2 (code 10), guidance GUIDE=1 (5), pass total
PASS=4 (15), per node PERNODE=3 (3). Two later cases k1, k2 can be edged
instance-of to L. Up to two server restarts or crashes. Graph write-through is assumed
to succeed (its failure is F2's concern).

The full product of every step is too large for the interpreter, so three
scopes each interleave the steps that can touch their properties: the ledger
(endorsements, credits, a case added, rename, saver, restart, crash), reads
and writes of L, and recurrence (edges, loads, renames). The flag step and
restarts/crashes are in every scope.

`fixed` adds the proposed fixes: the vote ledger follows a rename; a maintenance
rename keeps the version's activity time; the maintenance flag is kept on the
session record; a maintenance write keeps the lesson's author for the note
credit; the session file is saved whenever a ledger changes.
-/

def HARD := 2
def GUIDE := 1
def PERNODE := 3
def PASS := 4
def MAXRESTARTS := 2

structure Sess where
  liked : Nat        -- liked_ids, bitmask over ids: 1 "a", 2 "b", 4 "n"
  credits : Nat      -- session["maintenance_credits"]
  inSet : Bool       -- store._maintenance_sessions (process memory only)
  flagRec : Bool     -- fixed: session["maintenance"] on the record
  savedLiked : Nat   -- sessions.json as last saved
  savedCredits : Nat
  savedRec : Bool
  declared : Bool    -- ghost: the session declared itself maintenance
  deriving BEq, Hashable, Repr

structure S where
  a : Sess
  b : Sess
  m : Sess
  renamed : Bool
  archived : Bool
  used : Bool        -- L's version activity time (used_ts, else ts) is its own, not 0
  writtenBy : Nat    -- L._written.by: 0 A, 1 B, 2 M
  authorF : Nat      -- fixed: L._written.author, kept by maintenance writes
  author : Nat       -- ghost: last writer that is not a declared pass
  edged : Nat        -- instance-of edges from case k1 (1), k2 (2) to L
  credited : Nat     -- L._recurrence_ts
  recStamps : Nat    -- ghost: recurrence stamps on L
  evAL : Nat         -- ghost: accepted endorsement/credit events, session×node
  evAN : Nat
  evBL : Nat
  evML : Nat
  evMN : Nat
  stAL : Nat         -- ghost: _useful_ts stamps from A on L, from M on L and N
  stML : Nat
  stMN : Nat
  restarts : Nat
  maintRead : Bool   -- a declared pass's read stamped or promoted
  maintNote : Bool   -- a declared pass earned a note credit
  selfNote : Bool    -- a note credit went to the lesson's own author
  viaWrong : Bool    -- a declared pass's accepted credit logged as an endorsement
  actLost : Bool     -- a declared pass's step dropped L's activity time
  mRead : Bool       -- M had a read take effect
  lateFlag : Bool    -- observation: flagged after a read of its own took effect
  overGuide : Bool   -- observation: an endorsement accepted past the guidance
  maintWrPromote : Bool -- observation: a declared pass's write unarchived L
  deriving BEq, Hashable, Repr

def pc (x : Nat) : Nat := (x &&& 1) + ((x >>> 1) &&& 1) + ((x >>> 2) &&& 1)
def nameL (s : S) : Nat := if s.renamed then 2 else 1
def sn : Nat → String | 0 => "A" | 1 => "B" | _ => "M"
def gs (s : S) : Nat → Sess | 0 => s.a | 1 => s.b | _ => s.m
def ss (s : S) (i : Nat) (x : Sess) : S :=
  match i with | 0 => { s with a := x } | 1 => { s with b := x } | _ => { s with m := x }
def sat (n k : Nat) : Nat := min (n + k) 6   -- counters saturate past every bound checked

/-- One accepted event of session i on node x (0 L, 1 N) carrying k stamps. -/
def count (s : S) (i x k : Nat) : S :=
  match i, x with
  | 0, 0 => { s with evAL := sat s.evAL 1, stAL := sat s.stAL k }
  | 0, _ => { s with evAN := sat s.evAN 1 }
  | 1, _ => { s with evBL := sat s.evBL 1 }
  | _, 0 => { s with evML := sat s.evML 1, stML := sat s.stML k }
  | _, _ => { s with evMN := sat s.evMN 1, stMN := sat s.stMN k }

def saveOne (x : Sess) : Sess := { x with savedLiked := x.liked, savedCredits := x.credits, savedRec := x.flagRec }
-- session_manager.save_sessions writes every session (session_manager.py:733)
def saveAll (s : S) : S := { s with a := saveOne s.a, b := saveOne s.b, m := saveOne s.m }

/-- Collapse fields no later step can read, so equal futures share one state. -/
def canon (fx : Bool) (s : S) : S :=
  let dead (x : Sess) : Sess := { x with savedLiked := 0, savedCredits := 0, savedRec := false }
  let s := if s.restarts ≥ MAXRESTARTS then { s with a := dead s.a, b := dead s.b, m := dead s.m } else s
  if fx then { s with writtenBy := 0 } else { s with authorF := 0 }

-- core/recurrence.py:26-58 credit_edge / reconcile; store.py:1037-1050 on every load
def reconcile (s : S) : S :=
  [1, 2].foldl (fun s k =>
    if s.edged &&& k != 0 && s.credited &&& k == 0 then
      { s with credited := s.credited ||| k, recStamps := s.recStamps + 1 }
    else s) s

/-- mark_useful (store.py:449-599), one id per call. -/
def like (fx : Bool) (s : S) (i x c : Nat) : Option (String × S) :=
  let me := gs s i
  let nm := if x == 0 then nameL s else 4
  let maint := me.inSet                                         -- :495
  if maint && !(1 ≤ c && c ≤ PERNODE) then none                 -- :532
  else if !maint && c != 1 then none                            -- :534
  else if me.liked &&& nm != 0 then none                        -- :545 duplicate
  else if maint && me.credits + c > PASS then none              -- :549 pass total
  else if !maint && pc me.liked ≥ HARD then none                -- :556 hard cap
  else
    let me' := { me with liked := me.liked ||| nm, credits := me.credits + (if maint then c else 0) }
    let s1 := count (ss s i me') i x (if maint then c else 1)  -- :570
    let s2 := { s1 with viaWrong := s.viaWrong || (me.declared && !maint),   -- :511 via
                        overGuide := s.overGuide || (!maint && pc me'.liked > GUIDE) }
    some (s!"{sn i} kg_useful {if x == 0 then (if s.renamed then "b" else "a") else "n"}"
          ++ (if maint then s!" credits={c} (logged via maintenance)" else " (logged as an endorsement)"),
          if fx then saveAll s2 else s2)

/-- put_node on L (store.py:674-779); `note` adds a case. Note credit :727-731, :781-806. -/
def write (fx : Bool) (s : S) (i : Nat) (note : Bool) : String × S :=
  let me := gs s i
  let nm := nameL s
  let lessonBy := if fx then s.authorF else s.writtenBy
  let credit := note && lessonBy != i && !me.inSet && me.liked &&& nm == 0 && pc me.liked < HARD
  let s1 := if credit then
      { count (ss s i { me with liked := me.liked ||| nm }) i 0 1 with
        maintNote := s.maintNote || me.declared, selfNote := s.selfNote || s.author == i }
    else s
  let s2 := { s1 with writtenBy := i,                                -- :742
                      used := if me.inSet then s.used else true,      -- _bump_version :321
                      authorF := if me.inSet then s.authorF else i,
                      author := if me.declared then s.author else i,
                      archived := false,                              -- :746 unarchive
                      maintWrPromote := s.maintWrPromote || (me.declared && s.archived) }
  (s!"{sn i} put_node L" ++ (if note then " adding a case" else " rewording")
     ++ (if credit then " -> note credit" else ""),
   if credit && fx then saveAll s2 else s2)

/-- rename_node a -> b (store.py:1115-1210). -/
def rename (fx : Bool) (s : S) (i : Nat) : S :=
  let me := gs s i
  -- :1179-1183 pops the old version entry, then _bump_version runs on the new key,
  -- so a maintenance session's used_ts is read from an empty entry: 0.
  let used' := if me.inSet then (if fx then s.used else false) else true
  -- :1195 session_manager.rename_node_ref (:448) rewrites seen/preloaded/promoted, not liked_ids
  let mv (x : Sess) : Sess := if fx && x.liked &&& 1 != 0 then { x with liked := (x.liked - 1) ||| 2 } else x
  let s1 := { s with renamed := true, used := used',
                     actLost := s.actLost || (me.declared && s.used && !used') }
  if fx then saveAll { s1 with a := mv s1.a, b := mv s1.b, m := mv s1.m } else s1

/-- Which steps an exploration interleaves (the full product is too large to
enumerate in the interpreter; each scope keeps every step that can touch its
properties). -/
structure Scope where
  name : String
  ledger : Bool      -- endorsements and credits (A, M), crash, saver tick
  rw : Bool          -- reads and writes of L by A, B, M
  recur : Bool       -- instance-of edges and graph loads

def next (fx : Bool) (sc : Scope) (s : S) : List (String × S) := Id.run do
  let mut out : List (String × S) := []
  -- M declares itself a pass: kg_read(maintenance=true) -> store.mark_maintenance (:1725)
  if !s.m.declared then
    let s1 := { s with m := { s.m with inSet := true, declared := true, flagRec := fx },
                       lateFlag := s.mRead }
    out := ("M kg_read(maintenance=true)", if fx then saveAll s1 else s1) :: out
  if sc.rw then
    -- full node reads of L: read_node -> _record_read (:1467-1490)
    for i in [0, 2] do
      let me := gs s i
      if !me.inSet then
        let s1 := { s with archived := false, mRead := s.mRead || i == 2,
                           maintRead := s.maintRead || me.declared }
        if s1 != s then
          out := (s!"{sn i} kg_read L (stamps its read time{if s.archived then ", promotes" else ""})", s1) :: out
  if sc.ledger then
    for i in [0, 2] do
      for x in [0, 1] do
        for c in [1, 2, 3] do
          if let some r := like fx s i x c then out := r :: out
  -- writes to L; the ledger scope keeps B's write and A's case (a note credit uses the ledger)
  let writes := if sc.rw then [(0, true), (1, true), (2, false), (2, true)]
                else if sc.ledger then [(0, true), (1, false)] else []
  for (i, note) in writes do
    out := write fx s i note :: out
  if !s.renamed then
    for i in (if sc.rw || sc.recur then [0, 2] else [2]) do
      out := (s!"{sn i} kg_rename_node a -> b", rename fx s i) :: out
  if sc.recur then
    -- instance-of edges from later cases: put_edge credits at once (:983-987, :1018-1035);
    -- an edge arriving any other way (merge, editor, hand edit) waits for a load
    for k in [1, 2] do
      if s.edged &&& k == 0 then
        let s1 := { s with edged := s.edged ||| k }
        let s2 := if s.credited &&& k == 0 then
            { s1 with credited := s.credited ||| k, recStamps := s.recStamps + 1 } else s1
        out := (s!"put_edge case{k} -instance-of-> L", s2) ::
               (s!"edge case{k} -> L written outside put_edge", s1) :: out
    let r := reconcile s
    if r != s then out := ("graph reload (reconcile)", r) :: out
  if sc.ledger then
    let sv := saveAll s
    if sv != s then out := ("saver tick: sessions.json written", sv) :: out
  if s.restarts < MAXRESTARTS then
    -- graceful: shutdown saves sessions (:2043-2058); the new store's set starts empty (:128)
    let up (x : Sess) : Sess := { x with inSet := fx && x.flagRec }
    let g := saveAll s
    out := ("server restart", reconcile { g with a := up g.a, b := up g.b, m := up g.m,
                                                  restarts := s.restarts + 1 }) :: out
    do
      let back (x : Sess) : Sess :=
        { x with liked := x.savedLiked, credits := x.savedCredits, flagRec := x.savedRec,
                 inSet := fx && x.savedRec }
      out := ("server crash, then start (sessions.json as last saved)",
              reconcile { s with a := back s.a, b := back s.b, m := back s.m,
                                 restarts := s.restarts + 1 }) :: out
  return out.reverse.map fun (l, s') => (l, canon fx s')

/-- One exhaustive BFS; for each property, the shortest trace to a state violating it. -/
partial def explore (fx : Bool) (sc : Scope) (props : Array (S → Bool)) (init : S) :
    Array (Option (List String)) × Nat := Id.run do
  let mut found : Array (Option (List String)) := props.map (fun _ => none)
  let mut seen : Std.HashSet S := ({} : Std.HashSet S).insert init
  let mut frontier : Array (S × List String) := #[(init, [])]
  let mut n := 0
  while !frontier.isEmpty do
    let mut nextF := #[]
    for (s, tr) in frontier do
      n := n + 1
      for i in [0:props.size] do
        if found[i]!.isNone && props[i]! s then found := found.set! i (some tr.reverse)
      if found.all (·.isSome) then return (found, n)   -- every property has its trace
      for (l, s') in next fx sc s do
        if !seen.contains s' then
          seen := seen.insert s'
          nextF := nextF.push (s', l :: tr)
    frontier := nextF
  return (found, n)

def blank : Sess := { liked := 0, credits := 0, inSet := false, flagRec := false,
                      savedLiked := 0, savedCredits := 0, savedRec := false, declared := false }
def init : S :=
  { a := blank, b := blank, m := blank, renamed := false, archived := true, used := true,
    writtenBy := 0, authorF := 0, author := 0, edged := 0, credited := 0, recStamps := 0,
    evAL := 0, evAN := 0, evBL := 0, evML := 0, evMN := 0, stAL := 0, stML := 0, stMN := 0,
    restarts := 0, maintRead := false, maintNote := false, selfNote := false, viaWrong := false,
    actLost := false, mRead := false, lateFlag := false, overGuide := false, maintWrPromote := false }

/-- (property, the scopes that check it) -/
def props : Array (String × (S → Bool)) := #[
  ("P1 one vote (or one credit) per node per session",
    fun s => s.evAL > 1 || s.evAN > 1 || s.evBL > 1 || s.evML > 1 || s.evMN > 1),
  ("P2 caps: a work session ≤ HARD votes; a pass ≤ PERNODE per node and ≤ PASS in all",
    fun s => s.evAL + s.evAN > HARD ||
             (s.m.declared && (s.stML > PERNODE || s.stMN > PERNODE || s.stML + s.stMN > PASS))),
  ("P3 reconciliation is idempotent: one stamp per credited case, only for edged cases",
    fun s => s.recStamps != pc s.credited || s.credited &&& s.edged != s.credited),
  ("P4 a declared pass's reads never stamp recency or promote", (·.maintRead)),
  ("P5 a pass never earns a note credit, and no note credit goes to the lesson's author",
    fun s => s.maintNote || s.selfNote),
  ("P6 no step of a declared pass (write, rename) drops L's activity time", (·.actLost)),
  ("P7 a declared pass's credits are logged via maintenance", (·.viaWrong)),
  ("P1' the same, without a rename",
    fun s => !s.renamed && (s.evAL > 1 || s.evAN > 1 || s.evBL > 1 || s.evML > 1 || s.evMN > 1)),
  ("sanity (expect ✗): L holds an endorsement and a pass credit of 2+, and is renamed",
    fun s => s.stAL ≥ 1 && s.stML ≥ 2 && s.renamed),
  ("sanity (expect ✗): L holds two repeats, renamed, after a restart or crash",
    fun s => s.recStamps ≥ 2 && s.renamed && s.restarts ≥ 1),
  ("sanity (expect ✗): B's case on A's lesson is a note credit", fun s => s.evBL ≥ 1),
  ("observe (expect ✗): the guidance is soft, an endorsement past it is accepted", (·.overGuide)),
  ("observe (expect ✗): a session flagged after its own read keeps that read's effects", (·.lateFlag)),
  ("observe (expect ✗): a pass's write unarchives the node it edits", (·.maintWrPromote))]

def scopes : List (Scope × List Nat) := [
  ({ name := "ledger: A and M endorse/credit L and N; B rewords L, A adds a case; M renames; saver; restart or crash",
     ledger := true, rw := false, recur := false }, [0, 7, 1, 6, 8, 11]),
  ({ name := "reads and writes: A, B, M read/write L; renames; restart or crash",
     ledger := false, rw := true, recur := false }, [3, 4, 5, 10, 12, 13]),
  ({ name := "recurrence: edges via put_edge or outside it, loads, renames; restart or crash",
     ledger := false, rw := false, recur := true }, [2, 9])]

def runAll (fx : Bool) : IO Unit := do
  IO.println s!"===== fixed={fx}  (HARD={HARD} GUIDE={GUIDE} PERNODE={PERNODE} PASS={PASS}, ≤{MAXRESTARTS} restarts or crashes)"
  for (sc, ids) in scopes do
    IO.println s!"--- scope {sc.name}"
    let (found, n) := explore fx sc (ids.toArray.map fun i => props[i]!.2) init
    for (i, k) in ids.zipIdx.map (fun (i, k) => (i, k)) do
      match found[k]! with
      | some tr => do
          IO.println s!"✗ {props[i]!.1}: COUNTEREXAMPLE"
          for l in tr do IO.println s!"    {l}"
      | none => IO.println s!"✓ {props[i]!.1}: no counterexample ({n} states, exhaustive)"

def main : IO Unit := do runAll false; runAll true
