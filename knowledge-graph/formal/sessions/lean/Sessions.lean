import Std
/-!
Model of KG-session identity across Claude Code session events.
Code: rest.py:93-186 (session_bootstrap), session_manager.py:97-120
(bind_claude_sid / find_by_claude_sid), :285-300 (find_by_project_path),
ambient.py:237-240 / file_recall.py:377-379 (hook resolution), all in ONE project.

Claude sessions c (≤3). Each has: alive, the KG sid it was told in context
(`told`: what its kg_* calls pass), and `ctx`: gists actually in its context.
KG sessions k: `claude` binding (Option c), `start` order, `seen` set.
Events:
  start c         SessionStart source=startup, fresh transcript -> register(bound to c)
  resume p -> c   parent dies; transcript copied (markers name p.told); new Claude sid c
  fork   p -> c   parent STAYS ALIVE; transcript copied; new Claude sid c
  hook c n        a prompt/tool hook for c shows gist n: resolves k by claude sid, else
                  newest-by-path; injects n only if n ∉ k.seen; marks n seen in k.
Properties (for every live c):
  D  dedup soundness: when a hook for c suppresses n as "seen", n really is in c's context
  I  identity: the KG session c's hooks resolve to is the one c passes to kg_* calls
-/

abbrev N := Nat  -- gists 0..1

structure C where
  alive : Bool
  told : Nat
  ctx : List N
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

structure K where
  claude : Option Nat
  seen : List N
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

structure S where
  cs : List C
  ks : List K     -- index = start order (newest = last)
  violD : Bool
  violI : Bool
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

def ins (x : N) (l : List N) := if l.contains x then l else l ++ [x]

/-- find_by_claude_sid, else find_by_project_path (newest start). -/
def resolve (s : S) (c : Nat) : Option Nat :=
  match (List.range s.ks.length).find? (fun k => s.ks[k]!.claude == some c) with
  | some k => some k
  | none => if s.ks.isEmpty then none else some (s.ks.length - 1)

/-- bind_claude_sid: clear any other binding of c, bind k to c. -/
def bind (s : S) (k : Nat) (c : Nat) : S :=
  let ks := s.ks.map fun kk => if kk.claude == some c then { kk with claude := none } else kk
  { s with ks := ks.set k { ks[k]! with claude := some c } }

def maxC := 3

def next (allowFork : Bool) (s : S) : List (String × S) :=
  let cid := s.cs.length
  let startMv := if cid < maxC then
      [(s!"c{cid} startup -> register k{s.ks.length}",
        { s with cs := s.cs ++ [{ alive := true, told := s.ks.length, ctx := [] }],
                 ks := s.ks ++ [{ claude := some cid, seen := [] }] })]
    else []
  let inherit := (List.range s.cs.length).flatMap fun p =>
    let pc := s.cs[p]!
    if !pc.alive || cid ≥ maxC then [] else
    -- bootstrap for the new sid c: find_by_claude_sid(c) misses (new sid);
    -- transcript markers name pc.told -> lookup + same project -> reuse + bind (rest.py:128-139)
    let mk (fork : Bool) :=
      let cs := s.cs.set p { pc with alive := fork } ++ [{ pc with alive := true }]
      bind { s with cs := cs } pc.told cid
    [(s!"c{p} resumed as c{cid} (parent dies) -> reuse k{pc.told}", mk false)] ++
    (if allowFork then
      [(s!"c{p} FORKED as c{cid} (parent lives) -> reuse k{pc.told}", mk true)] else [])
  let hooks := (List.range s.cs.length).flatMap fun c =>
    let cc := s.cs[c]!
    if !cc.alive then [] else
    match resolve s c with
    | none => []
    | some k => [0, 1].map fun n =>
        let kk := s.ks[k]!
        let suppressed := kk.seen.contains n
        let cc' := if suppressed then cc else { cc with ctx := ins n cc.ctx }
        let s' := { s with cs := s.cs.set c cc',
                           ks := s.ks.set k { kk with seen := ins n kk.seen },
                           violD := s.violD || (suppressed && !cc.ctx.contains n),
                           violI := s.violI || k != cc.told }
        (s!"hook for c{c} resolves k{k} (c{c} uses k{cc.told}) shows gist {n}" ++
          (if suppressed then " -> SUPPRESSED as seen" else " -> injected"), s')
  startMv ++ inherit ++ hooks

partial def bfs (allowFork : Bool) (bad : S → Bool) (init : S) : Option (List String) × Nat := Id.run do
  let mut seen : Std.HashSet S := ({} : Std.HashSet S).insert init
  let mut frontier : Array (S × List String) := #[(init, [])]
  let mut n := 0
  while !frontier.isEmpty do
    let mut nf := #[]
    for (s, tr) in frontier do
      n := n + 1
      if bad s then return (some tr.reverse, n)
      for (l, s') in next allowFork s do
        if !seen.contains s' then
          seen := seen.insert s'
          nf := nf.push (s', l :: tr)
    frontier := nf
  return (none, n)

def report (name : String) (r : Option (List String) × Nat) : IO Unit :=
  match r with
  | (some tr, n) => do
      IO.println s!"✗ {name}: COUNTEREXAMPLE ({n} states)"
      for l in tr do IO.println s!"    {l}"
  | (none, n) => IO.println s!"✓ {name}: none ({n} states, exhaustive)"

def init : S := { cs := [], ks := [], violD := false, violI := false }

def main : IO Unit := do
  IO.println "--- with fork events (code as is)"
  report "D dedup soundness" (bfs true (·.violD) init)
  report "I hook identity" (bfs true (·.violI) init)
  IO.println "--- same model, fork events removed (isolates the cause)"
  report "D dedup soundness" (bfs false (·.violD) init)
  report "I hook identity" (bfs false (·.violI) init)
