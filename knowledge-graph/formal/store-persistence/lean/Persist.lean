import Std
/-!
Model of the memory↔disk protocol of one graph in
knowledge-graph/server/mcp_http/store.py (MultiProjectGraphStore).
Everything below runs under `self.lock`, so each action is atomic; the
interesting nondeterminism is whether `GraphPersistence.save` succeeds
(core/persistence.py:92-150 returns False on any exception: disk full,
permissions, EIO...). Content is abstracted to a version counter.
-/

structure S where
  mem : Nat        -- version of the in-memory graph
  disk : Nat       -- version persisted on disk
  dirty : Bool     -- self.dirty[graph_key]
  lost : Bool      -- some in-memory write was discarded without reaching disk
  down : Bool      -- shutdown() completed
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  maxMem : Nat
  fixWT : Bool       -- fix A: _write_through keeps dirty when save fails
  fixReload : Bool   -- fix B: force-reload refuses (or flushes first) when dirty

def save (s : S) (ok : Bool) : S := if ok then { s with disk := s.mem } else s

def next (c : Cfg) (s : S) : List (String × S) :=
  if s.down then [] else
  let oks := [true, false]
  let lbl (ok : Bool) := if ok then "ok" else "FAILS"
  let writes := if s.mem < c.maxMem then
      -- put_node/put_edge/delete_*/set_progress/mark_useful: mutate, dirty=True,
      -- _write_through -> _save_to_disk; dirty=False   (store.py:385-390, :693)
      oks.map (fun ok =>
        let s1 := save { s with mem := s.mem + 1, dirty := true } ok
        (s!"write-through (save {lbl ok})",
         { s1 with dirty := if c.fixWT then !ok && s1.mem != s1.disk else false }))
      -- read_node stamps _last_read_ts, dirty=True, no save (store.py:1205-1207);
      -- _prune_orphans likewise (store.py:1852-1854)
      ++ [("read_node / prune: mutate, mark dirty only", { s with mem := s.mem + 1, dirty := true })]
    else []
  let saver := if s.dirty then
      -- _periodic_save (store.py:1884-1886): save; dirty=False only on success
      oks.map fun ok =>
        let s1 := save s ok
        (s!"periodic saver (save {lbl ok})", { s1 with dirty := !ok })
    else []
  let reload :=
    -- read_graphs(force_reload=True) -> _load_user_graph (store.py:422-423, :225-240);
    -- exposed as GET /api/graph/read?reload=true (rest.py:80-83), documented in kg-ops skill
    if c.fixReload && s.dirty then [] else
    [("force reload from disk", { s with mem := s.disk, dirty := false,
                                          lost := s.lost || s.mem != s.disk })]
  let shut :=
    -- shutdown(): final save only for dirty graphs (store.py:1903-1906)
    oks.map fun ok =>
      let s1 := if s.dirty then save s ok else s
      (s!"shutdown (final save {if s.dirty then lbl ok else "skipped: not dirty"})",
       { s1 with down := true, lost := s1.lost || s1.mem != s1.disk })
  writes ++ saver ++ reload ++ shut

/-- The protocol invariant everything relies on: clean ⇒ memory is on disk. -/
def cleanMeansSaved (s : S) : Bool := s.dirty || s.mem == s.disk
def noLoss (s : S) : Bool := !s.lost

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

def init : S := { mem := 0, disk := 0, dirty := false, lost := false, down := false }

def runAll (c : Cfg) : IO Unit := do
  IO.println s!"--- maxMem={c.maxMem} fixWT={c.fixWT} fixReload={c.fixReload}"
  report "I1 not dirty ⇒ memory == disk" (bfs c (fun s => !cleanMeansSaved s) init)
  report "I2 no write lost (reload / shutdown)" (bfs c (fun s => !noLoss s) init)
  report "sanity (expect ✗): a durable write reaches disk"
    (bfs c (fun s => s.disk ≥ 1 && s.down) init)

def main : IO Unit := do
  runAll { maxMem := 3, fixWT := false, fixReload := false }
  runAll { maxMem := 3, fixWT := true,  fixReload := false }
  runAll { maxMem := 3, fixWT := true,  fixReload := true }
