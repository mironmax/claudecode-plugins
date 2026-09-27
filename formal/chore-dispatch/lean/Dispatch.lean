import Std
/-!
Model of `maybe_dispatch` / `_spawn` in
knowledge-graph/server/mcp_http/chore_dispatch.py.

Each dispatcher thread is one call of `maybe_dispatch` (rest.py:251 starts one
thread per prompt). Steps are the atomic regions between shared-state accesses.
Abstracted: gauge / target / tier logic (treated as "passes"), day rollover,
the pass tier, logging. Time is discrete ticks.
-/

inductive PC where
  | start     -- before `_running.is_set()` fast gate            (src: chore_dispatch.py:575)
  | gated     -- passed fast gate, about to take `now` + read state (src: :578-579)
  | checked   -- snapshot passed interval/cap gates, heading to lock (src: :580-678)
  | spawning  -- inside/after lock section, about to Popen        (src: :680-695)
  | done
  deriving DecidableEq, Repr, Hashable, BEq

structure Th where
  pc : PC := .start
  now : Nat := 0
  snapTs : Nat := 0
  snapCnt : Nat := 0
  deriving DecidableEq, Repr, Hashable, BEq

structure S where
  clock : Nat
  fileTs : Nat          -- chore_state.json "last_ts"
  fileCnt : Nat         -- chore_state.json "count"
  running : Bool        -- `_running` Event
  procs : Nat           -- live chore processes
  log : List Nat        -- `now` of every dispatch that took the lock (ground truth)
  ths : List Th
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  interval : Nat   -- min_interval_s
  maxDay : Nat     -- max_per_day
  maxClock : Nat
  fixed : Bool     -- re-check against fresh state inside the lock

def S.set (s : S) (i : Nat) (t : Th) : S := { s with ths := s.ths.set i t }

/-- One thread's atomic step. -/
def thStep (c : Cfg) (s : S) (i : Nat) (t : Th) : List (String × S) :=
  match t.pc with
  | .start =>
      -- src :575  if _running.is_set(): return
      if s.running then [(s!"T{i} fast-gate: running, return", s.set i {t with pc := .done})]
      else [(s!"T{i} fast-gate: pass", s.set i {t with pc := .gated})]
  | .gated =>
      -- src :578-579 now = time.time(); state = _read_state(); then gates :580 and :624
      let t' := {t with now := s.clock, snapTs := s.fileTs, snapCnt := s.fileCnt}
      if s.clock - s.fileTs < c.interval || s.fileCnt ≥ c.maxDay then
        [(s!"T{i} read state (ts={s.fileTs},cnt={s.fileCnt}) at t={s.clock}: gated out",
          s.set i {t' with pc := .done})]
      else
        [(s!"T{i} read state (ts={s.fileTs},cnt={s.fileCnt}) at t={s.clock}: eligible",
          s.set i {t' with pc := .checked})]
  | .checked =>
      -- src :680-691  with _lock: if running: return; set; state[...] = ...; write
      let blocked :=
        s.running || (c.fixed && (t.now - s.fileTs < c.interval || s.fileCnt ≥ c.maxDay))
      if blocked then [(s!"T{i} lock: refused", s.set i {t with pc := .done})]
      else
        let cnt := if c.fixed then s.fileCnt else t.snapCnt
        let s' := { s with running := true, fileTs := t.now, fileCnt := cnt + 1,
                           log := s.log ++ [t.now] }
        [(s!"T{i} lock: DISPATCH (writes ts={t.now}, cnt={cnt+1})",
          s'.set i {t with pc := .spawning})]
  | .spawning =>
      -- src :386-395 Popen fails -> _running.clear();  else watcher thread
      [ (s!"T{i} spawn FAILED -> running cleared",
          { s with running := false }.set i {t with pc := .done}),
        (s!"T{i} spawn ok", { s with procs := s.procs + 1 }.set i {t with pc := .done}) ]
  | .done => []

def next (c : Cfg) (s : S) : List (String × S) :=
  let thMoves := (List.range s.ths.length).flatMap fun i =>
    match s.ths[i]? with
    | some t => thStep c s i t
    | none => []
  let env :=
    (if s.procs > 0 then   -- src :405-413 watcher: communicate() returns, finally clear
      [("a chore process exits -> running cleared",
        { s with procs := s.procs - 1, running := false })] else []) ++
    (if s.clock < c.maxClock then [("tick", { s with clock := s.clock + 1 })] else [])
  thMoves ++ env

/-! Properties -/
def pairs : List Nat → List (Nat × Nat)
  | [] => []
  | x :: xs => xs.map (fun y => (x, y)) ++ pairs xs

def tooClose (c : Cfg) (s : S) : Bool :=
  (pairs s.log).any fun (a, b) => (if a ≤ b then b - a else a - b) < c.interval

def P1_oneProc (s : S) : Bool := s.procs ≤ 1
def P2_interval (c : Cfg) (s : S) : Bool := !tooClose c s
def P3_cap (c : Cfg) (s : S) : Bool := s.log.length ≤ c.maxDay
def P4_count (s : S) : Bool := s.fileCnt == s.log.length

/-! Bounded BFS returning the shortest labeled trace to a bad state. -/
partial def bfs (c : Cfg) (bad : S → Bool) (init : S) (limit : Nat := 2000000) :
    Option (List String) × Nat := Id.run do
  let mut seen : Std.HashSet S := {}
  seen := seen.insert init
  let mut frontier : Array (S × List String) := #[(init, [])]
  let mut n := 0
  while !frontier.isEmpty && n < limit do
    let mut nextF := #[]
    for (s, tr) in frontier do
      n := n + 1
      if bad s then return (some tr.reverse, n)
      for (lbl, s') in next c s do
        if !seen.contains s' then
          seen := seen.insert s'
          nextF := nextF.push (s', lbl :: tr)
    frontier := nextF
  return (none, n)

def init (k : Nat) (clock : Nat) : S :=
  { clock, fileTs := 0, fileCnt := 0, running := false, procs := 0, log := [],
    ths := List.replicate k {} }

def report (name : String) (r : Option (List String) × Nat) : IO Unit := do
  match r with
  | (some tr, n) =>
      IO.println s!"✗ {name}: COUNTEREXAMPLE ({n} states explored)"
      for l in tr do IO.println s!"    {l}"
  | (none, n) => IO.println s!"✓ {name}: no counterexample ({n} states, exhaustive)"

def runAll (c : Cfg) (k : Nat) : IO Unit := do
  IO.println s!"--- threads={k} interval={c.interval} maxDay={c.maxDay} ticks≤{c.maxClock} fixed={c.fixed}"
  let i := init k 5
  report "P1 at most one chore process" (bfs c (fun s => !P1_oneProc s) i)
  report "P2 dispatches ≥ interval apart" (bfs c (fun s => !P2_interval c s) i)
  report "P3 daily cap" (bfs c (fun s => !P3_cap c s) i)
  report "P4 stored count = dispatches" (bfs c (fun s => !P4_count s) i)
  -- non-vacuity: two legitimate dispatches must be reachable
  report "sanity (expect ✗): two dispatches reachable" (bfs c (fun s => s.log.length ≥ 2) i)

def main : IO Unit := do
  runAll { interval := 2, maxDay := 1, maxClock := 9, fixed := false } 2
  runAll { interval := 2, maxDay := 3, maxClock := 9, fixed := false } 3
  runAll { interval := 2, maxDay := 1, maxClock := 9, fixed := true } 2
  runAll { interval := 2, maxDay := 3, maxClock := 9, fixed := true } 3
