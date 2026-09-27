import Std
/-!
Model of two sessions editing one node (F11), after
knowledge-graph/server/mcp_http/store.py `put_node` and the seen-marking in
mcp_streamable_server.py / mcp_http/ambient.py / file_recall.py / rest.py.

A node's content is a set of "additions" (bitmask). Each agent session s
reads the node (a view), then writes back what it saw plus its own addition,
bit s — the read-modify-write an agent does when it adds a note: put_node
replaces `notes` wholesale with the list the agent sends.

Every action is atomic under `store.lock`; the clock ticks once per action,
so timestamps are distinct and ordered.

Modes:
  guard    — put_node refuses a write when the node's content was last
             written by ANOTHER session after this session's last view of it,
             and shows the current content (which counts as a view).
  split    — a view is two steps, as a render followed by mark_seen is:
             the snapshot, then recording the view time. `stampBefore`
             records the time taken BEFORE the snapshot; otherwise the time
             at marking, which is later.
-/

structure Sess where
  base : Nat              -- content this session last saw
  seenAt : Option Nat     -- recorded view time
  snapT : Option Nat      -- split view in progress: time the snapshot was taken
  done : Bool             -- its addition was acknowledged as written
  deriving DecidableEq, Repr, Hashable, BEq

structure S where
  t : Nat
  content : Nat
  wTs : Nat               -- content-write stamp: time
  wBy : Nat               -- content-write stamp: session (0 = none yet)
  a : Sess
  b : Sess
  committed : Nat         -- additions put_node acknowledged
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  maxT : Nat
  guard : Bool
  split : Bool
  stampBefore : Bool

def bit (s : Nat) : Nat := if s == 1 then 1 else 2

def get (st : S) (s : Nat) : Sess := if s == 1 then st.a else st.b
def set (st : S) (s : Nat) (x : Sess) : S := if s == 1 then { st with a := x } else { st with b := x }

def stale (st : S) (s : Nat) (x : Sess) : Bool :=
  st.wBy != 0 && st.wBy != s &&
  (match x.seenAt with
   | some v => decide (v < st.wTs)
   | none => false)       -- never saw it: a blind write, allowed as today

def acts (c : Cfg) (st : S) (s : Nat) : List (String × S) :=
  let x := get st s
  let t := st.t
  let st1 := { st with t := t + 1 }
  if x.done then [] else
  let view :=
    if !c.split then
      [(s!"s{s} views node (content {st.content})",
        set st1 s { x with base := st.content, seenAt := some t })]
    else match x.snapT with
      | none => [(s!"s{s} renders node (snapshot of content {st.content})",
                  set st1 s { x with base := st.content, snapT := some t })]
      | some ts => [(s!"s{s} marks it seen (at {if c.stampBefore then ts else t})",
                     set st1 s { x with seenAt := some (if c.stampBefore then ts else t),
                                        snapT := none })]
  let write :=
    if x.seenAt.isNone || x.snapT.isSome then [] else
    let new := x.base ||| bit s
    if c.guard && stale st s x then
      [(s!"s{s} put_node REFUSED: changed by s{st.wBy} at {st.wTs} after its view",
        set st1 s { x with base := st.content, seenAt := some t })]
    else
      [(s!"s{s} put_node writes {new} (its view {x.base} + own addition)",
        let st2 := { st1 with content := new, wTs := t, wBy := s,
                              committed := st.committed ||| bit s }
        set st2 s { x with base := new, seenAt := some t, done := true })]
  view ++ write

def next (c : Cfg) (st : S) : List (String × S) :=
  if st.t ≥ c.maxT then [] else acts c st 1 ++ acts c st 2

def lostUpdate (st : S) : Bool := (st.committed &&& (3 - st.content)) != 0
def bothDone (st : S) : Bool := st.a.done && st.b.done

partial def bfs (c : Cfg) (bad : S → Bool) (init : S) : Option (List String) × Nat := Id.run do
  let mut seen : Std.HashSet S := ({} : Std.HashSet S).insert init
  let mut frontier : Array (S × List String) := #[(init, [])]
  let mut n := 0
  while !frontier.isEmpty do
    let mut nextF := #[]
    for (s, tr) in frontier do
      n := n + 1
      if bad s then return (some tr.reverse, n)
      for (l, s') in next c s do
        if !seen.contains s' then
          seen := seen.insert s'
          nextF := nextF.push (s', l :: tr)
    frontier := nextF
  return (none, n)

def report (name : String) (r : Option (List String) × Nat) : IO Unit :=
  match r with
  | (some tr, n) => do
      IO.println s!"✗ {name}: COUNTEREXAMPLE ({n} states)"
      for l in tr do IO.println s!"    {l}"
  | (none, n) => IO.println s!"✓ {name}: no counterexample ({n} states, exhaustive)"

def blank : Sess := { base := 0, seenAt := none, snapT := none, done := false }
def init : S := { t := 1, content := 0, wTs := 0, wBy := 0, a := blank, b := blank, committed := 0 }

def runAll (c : Cfg) : IO Unit := do
  IO.println s!"--- maxT={c.maxT} guard={c.guard} split={c.split} stampBefore={c.stampBefore}"
  report "W1 no acknowledged addition is lost" (bfs c lostUpdate init)
  report "sanity (expect ✗): both sessions get their addition in" (bfs c bothDone init)

def main : IO Unit := do
  -- today: no guard
  runAll { maxT := 12, guard := false, split := false, stampBefore := false }
  -- guard, view recorded atomically
  runAll { maxT := 12, guard := true, split := false, stampBefore := false }
  -- guard, but the view time is taken when mark_seen runs, after the render
  runAll { maxT := 12, guard := true, split := true, stampBefore := false }
  -- guard, view time taken before the render
  runAll { maxT := 12, guard := true, split := true, stampBefore := true }
