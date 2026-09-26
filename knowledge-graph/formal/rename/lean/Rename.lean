/-!
Model of `MultiProjectGraphStore.rename_node` (store.py:937-1028) and
`rewrite_edge_refs_on_disk` (core/persistence.py:~155-205).

Graphs: U (user, always loaded), P1, P2 (projects; each loaded or only on disk).
Ids: `a` (old) and `b` (new). Every graph holds one edge to `a` and one to `b`.
Edge endpoints are bare ids. Resolution (store.py:1755-1768, _clean_orphaned_edges
doctrine "cross-level edges live in PROJECT graphs and point up to user nodes"):
  in a project graph: local node if present, else the user node, else dangling
  (dangling edges are deleted on the next load);
  in U: a U node, else dangling.
Property: every edge that resolved to some node before the rename resolves to
the same node after it (the renamed node now answering to `b`).
-/

inductive G where | U | P1 | P2 deriving DecidableEq, Repr
inductive Id' where | a | b deriving DecidableEq, Repr

structure Gr where
  hasA : Bool
  hasB : Bool
  edges : List Id'   -- edge targets
  loaded : Bool
  deriving DecidableEq, Repr

def Gr.has (g : Gr) : Id' → Bool | .a => g.hasA | .b => g.hasB

structure W where
  u : Gr
  p1 : Gr
  p2 : Gr
  deriving DecidableEq, Repr

def W.get (w : W) : G → Gr | .U => w.u | .P1 => w.p1 | .P2 => w.p2
def W.put (w : W) (k : G) (g : Gr) : W :=
  match k with | .U => { w with u := g } | .P1 => { w with p1 := g } | .P2 => { w with p2 := g }

/-- Node identity an edge in graph `k` resolves to. -/
def resolve (w : W) (k : G) (x : Id') : Option (G × Id') :=
  if (w.get k).has x then some (k, x)
  else if k != .U && w.u.has x then some (.U, x) else none

def retarget (g : Gr) : Gr := { g with edges := g.edges.map fun e => if e == .a then .b else e }

/-- rename_node(a -> b) on graph `gk`, as the code does it. `fixed` adds a precheck. -/
def rename (fixed : Bool) (w : W) (gk : G) : Option W := Id.run do
  let g := w.get gk
  if !g.has .a || g.has .b then return none           -- store.py:966-973
  let mut w := w.put gk { g with hasA := false, hasB := true }
  let others := [G.U, .P1, .P2].filter (· != gk)
  if fixed then
    -- fix: refuse when some other graph would re-point (or drop) an edge
    for k in others do
      let o := w.get k
      if !o.has .a && o.edges.contains .a && o.has .b then return none
  -- store.py:985-992 loaded graphs (the renamed graph itself included)
  w := w.put gk (retarget (w.get gk))
  for k in others do
    let o := w.get k
    if fixed && gk != .U then continue                 -- fix: project ids are not visible elsewhere
    if o.loaded then
      if o.has .a then continue                        -- "carries its own node by the old name"
      w := w.put k (retarget o)
    else if k != .U then
      -- _sweep_disk_rename -> rewrite_edge_refs_on_disk
      if o.has .a then continue                        -- skip:local-node
      if o.edges.contains .a && o.has .b then continue -- skip:collision
      w := w.put k (retarget o)
  return some w

def expected (gk : G) (r : Option (G × Id')) : Option (G × Id') :=
  if r == some (gk, .a) then some (gk, .b) else r

/-- All (graph, edge index) whose resolved target changed wrongly. -/
def broken (w w' : W) (gk : G) : List String :=
  [G.U, .P1, .P2].flatMap fun k =>
    ((w.get k).edges.zip (w'.get k).edges).filterMap fun (e, e') =>
      let before := resolve w k e
      let after := resolve w' k e'
      if before.isSome && after != expected gk before then
        some s!"edge in {repr k} to {repr e}: resolved {repr before}, after rename {repr after}"
      else none

def bools := [false, true]
def graphs (isU : Bool) : List Gr :=
  bools.flatMap fun a => bools.flatMap fun b => (if isU then [true] else bools).map fun l =>
    { hasA := a, hasB := b, edges := [.a, .b], loaded := l }

def worlds : List W :=
  (graphs true).flatMap fun u => (graphs false).flatMap fun p1 => (graphs false).map fun p2 =>
    { u, p1, p2 }

def check (fixed : Bool) : IO Unit := do
  let mut total := 0
  let mut bad := 0
  let mut shown : List String := []
  for w in worlds do
    for gk in [G.U, .P1] do
      match rename fixed w gk with
      | none => pure ()
      | some w' =>
        total := total + 1
        let b := broken w w' gk
        if !b.isEmpty then
          bad := bad + 1
          let key := s!"{repr gk}|{b}"
          if shown.length < 40 && !(shown.any (· == key)) then
            shown := shown ++ [key]
            IO.println s!"✗ rename a→b in {repr gk}; U={repr (w.u.hasA, w.u.hasB)} P1={repr (w.p1.hasA, w.p1.hasB, w.p1.loaded)} P2={repr (w.p2.hasA, w.p2.hasB, w.p2.loaded)}"
            for s in b do IO.println s!"      {s}"
  IO.println s!"fixed={fixed}: {bad} of {total} accepted renames break an edge (exhaustive over {worlds.length} worlds × 2 levels)\n"

def main : IO Unit := do check false; check true
