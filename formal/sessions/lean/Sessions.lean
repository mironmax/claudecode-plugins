import Std
/-!
Model of KG-session identity across Claude Code session events.
Code: rest.py:93-186 (session_bootstrap), session_manager.py:97-120
(bind_claude_sid / find_by_claude_sid), :227-251 (resolve_hook_session,
the OLD/NEW split below), :285-300 (find_by_project_path),
ambient.py:237-240 / file_recall.py:377-379 (hook resolution),
mcp_streamable_server.py:401,429 (kg_read's own registration, no claude_sid),
all in ONE project.

Claude sessions c (≤3). Each has: alive, the KG sid it was told in context
(`told`: what its kg_* calls pass), and `ctx`: gists actually in its context.
KG sessions k: `claude` binding (Option c), `start` order, `seen` set.
Events:
  start c         SessionStart source=startup, fresh transcript -> register(bound to c)
  resume p -> c   parent dies; transcript copied (markers name p.told); new Claude sid c
  fork   p -> c   parent STAYS ALIVE; transcript copied; new Claude sid c
  kgRead c        preload never ran (no SessionStart binding): the first kg_* call is a
                  raw kg_read(cwd=...), which registers a session with NO claude_sid
                  (mcp_streamable_server.py:429) -> c is alive but told an UNBOUND k.
                  Replaces `start c` for this run (see `noPreload` below).
  hook c n        a prompt/tool hook for c shows gist n: resolved by `resolve` (OLD rule)
                  or `resolveNewRule` (NEW rule, chosen by the `newRule` flag); injects n
                  only if n ∉ k.seen; marks n seen in k.
Two resolution rules for the hook step (session_manager.py:227-251):
  OLD  (`resolve`, code before ef38555): bound id, else newest session in the
       project — regardless of whether this event carries an id at all.
  NEW  (`resolveNewRule`, ef38555): bound id; else the evidence route — the KG
       id c was told, used (and then bound) only if still unbound; else none.
       The id-less fallback to newest-by-path survives in the real code only
       for hooks that predate the binding entirely, which this model does not
       generate (every c here always carries an id) — so it does not appear
       as a branch of `resolveNewRule`.
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

/-- OLD rule: find_by_claude_sid, else find_by_project_path (newest start). -/
def resolve (s : S) (c : Nat) : Option Nat :=
  match (List.range s.ks.length).find? (fun k => s.ks[k]!.claude == some c) with
  | some k => some k
  | none => if s.ks.isEmpty then none else some (s.ks.length - 1)

/-- bind_claude_sid: clear any other binding of c, bind k to c. -/
def bind (s : S) (k : Nat) (c : Nat) : S :=
  let ks := s.ks.map fun kk => if kk.claude == some c then { kk with claude := none } else kk
  { s with ks := ks.set k { ks[k]! with claude := some c } }

/-- NEW rule, resolve_hook_session (session_manager.py:227-251) after ef38555:
bound id resolves to its own session only. Otherwise the evidence route
(:240-251): the KG id c was told (the last "Session: x" our own renders left
in its transcript) is used only if that session is still unbound — and using
it BINDS it (`bind_claude_sid`, :250), so every later hook for c finds it
directly. Bound to somebody else, or no candidate at all: resolves to
nothing, never to a guess. Returns the (possibly bind-mutated) state
alongside the result, since — unlike the OLD rule — resolving can itself
change `s`. -/
def resolveNewRule (s : S) (c : Nat) : Option Nat × S :=
  match (List.range s.ks.length).find? (fun k => s.ks[k]!.claude == some c) with
  | some k => (some k, s)
  | none =>
    let k := s.cs[c]!.told
    if s.ks[k]!.claude.isNone then (some k, bind s k c) else (none, s)

def maxC := 3

/-- `fixFork` is the F8 fix (session_manager.fork): when the recovered KG session is
still bound to another Claude session, the new one gets a CLONE (the parent's seen
state, appended as newest) bound to it and is told the clone's id by the
continuity note; the recovered session keeps its binding.
`noPreload` swaps every session's startup from the normal hook-bound register
(rest.py:128-139/145-146) for the kg_read-only path (mcp_streamable_server.py
:401,429): c starts alive but its told session is UNBOUND — the case the NEW
rule's evidence route exists for.
`newRule` picks `resolveNewRule` over `resolve` for the hook step. -/
def next (allowFork fixFork noPreload newRule : Bool) (s : S) : List (String × S) :=
  let cid := s.cs.length
  let startMv := if cid < maxC && !noPreload then
      [(s!"c{cid} startup -> register k{s.ks.length}",
        { s with cs := s.cs ++ [{ alive := true, told := s.ks.length, ctx := [] }],
                 ks := s.ks ++ [{ claude := some cid, seen := [] }] })]
    else []
  let startNoPreload := if cid < maxC && noPreload then
      [(s!"c{cid} startup, preload never ran -> kg_read registers k{s.ks.length} (unbound)",
        { s with cs := s.cs ++ [{ alive := true, told := s.ks.length, ctx := [] }],
                 ks := s.ks ++ [{ claude := none, seen := [] }] })]
    else []
  let inherit := (List.range s.cs.length).flatMap fun p =>
    let pc := s.cs[p]!
    if !pc.alive || cid ≥ maxC then [] else
    -- bootstrap for the new sid c: find_by_claude_sid(c) misses (new sid);
    -- transcript markers name pc.told -> lookup + same project -> reuse + bind (rest.py:128-139)
    let kp := s.ks[pc.told]!
    let clone := fixFork && kp.claude.isSome && kp.claude != some cid
    let mk (fork : Bool) :=
      let cs := s.cs.set p { pc with alive := fork }
      if clone then
        let k' := s.ks.length
        bind { s with cs := cs ++ [{ pc with alive := true, told := k' }],
                      ks := s.ks ++ [{ claude := none, seen := kp.seen }] } k' cid
      else
        bind { s with cs := cs ++ [{ pc with alive := true }] } pc.told cid
    let how := if clone then s!"clone k{pc.told} as k{s.ks.length}" else s!"reuse k{pc.told}"
    [(s!"c{p} resumed as c{cid} (parent dies) -> {how}", mk false)] ++
    (if allowFork then
      [(s!"c{p} FORKED as c{cid} (parent lives) -> {how}", mk true)] else [])
  let hooks := (List.range s.cs.length).flatMap fun c =>
    let cc := s.cs[c]!
    if !cc.alive then [] else
    let (resolved, s0) := if newRule then resolveNewRule s c else (resolve s c, s)
    match resolved with
    | none => []
    | some k =>
      let evidence := newRule && s.ks[k]!.claude.isNone
      [0, 1].map fun n =>
        let kk := s0.ks[k]!
        let suppressed := kk.seen.contains n
        let cc' := if suppressed then cc else { cc with ctx := ins n cc.ctx }
        let s' := { s0 with cs := s0.cs.set c cc',
                            ks := s0.ks.set k { kk with seen := ins n kk.seen },
                            violD := s0.violD || (suppressed && !cc.ctx.contains n),
                            violI := s0.violI || k != cc.told }
        (s!"hook for c{c} resolves k{k}" ++ (if evidence then " [evidence-bound]" else "") ++
          s!" (c{c} uses k{cc.told}) shows gist {n}" ++
          (if suppressed then " -> SUPPRESSED as seen" else " -> injected"), s')
  startMv ++ startNoPreload ++ inherit ++ hooks

partial def bfs (allowFork fixFork noPreload newRule : Bool) (bad : S → Bool) (init : S) :
    Option (List String) × Nat := Id.run do
  let mut seen : Std.HashSet S := ({} : Std.HashSet S).insert init
  let mut frontier : Array (S × List String) := #[(init, [])]
  let mut n := 0
  while !frontier.isEmpty do
    let mut nf := #[]
    for (s, tr) in frontier do
      n := n + 1
      if bad s then return (some tr.reverse, n)
      for (l, s') in next allowFork fixFork noPreload newRule s do
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
  IO.println "=== OLD RULE: resolve = bound, else newest-in-project (code before ef38555) ==="
  IO.println "--- with fork events (code before the F8 fix)"
  report "D dedup soundness" (bfs true false false false (·.violD) init)
  report "I hook identity" (bfs true false false false (·.violI) init)
  IO.println "--- same model, fork events removed (isolates the cause)"
  report "D dedup soundness" (bfs false false false false (·.violD) init)
  report "I hook identity" (bfs false false false false (·.violI) init)
  IO.println "--- with fork events, F8 fix (clone a still-bound session)"
  report "D dedup soundness" (bfs true true false false (·.violD) init)
  report "I hook identity" (bfs true true false false (·.violI) init)
  IO.println ""
  IO.println "=== NEW RULE: resolveNewRule = bound, else evidence-if-unbound, else none (ef38555) ==="
  IO.println "--- with forks, normal preload (current server: F8 fix + new rule)"
  report "D dedup soundness" (bfs true true false true (·.violD) init)
  report "I hook identity" (bfs true true false true (·.violI) init)
  IO.println "--- without forks, normal preload"
  report "D dedup soundness" (bfs false true false true (·.violD) init)
  report "I hook identity" (bfs false true false true (·.violI) init)
  IO.println "--- with forks, kg_read-registered event (preload never happened)"
  report "D dedup soundness" (bfs true true true true (·.violD) init)
  report "I hook identity" (bfs true true true true (·.violI) init)
  IO.println "--- without forks, kg_read-registered event"
  report "D dedup soundness" (bfs false true true true (·.violD) init)
  report "I hook identity" (bfs false true true true (·.violI) init)
