import Std
/-!
Model of the combined chore + pass dispatcher as of 0.11.x:
`maybe_dispatch`, `_pick_target`, `_spawn` and `_runner` in
knowledge-graph/server/mcp_http/chore_dispatch.py, the Antigravity wrapper
mcp_http/agy_chore.py, and the status-line gauge writer cli/gauge.py.
Dispatch.lean (F1, 0.9.44 chore tier only) is kept as it was.

Part 1 is a BFS over prompt threads (rest.py:325 and antigravity.py:197 start
one `maybe_dispatch` thread per prompt), the processes they start, and the
environment: clock ticks, process exits and timeouts, config edits
(chores.json is re-read on mtime change, :104), a disk fault on the state
file, status-line frames that carry one window only, live-context reads.
Steps are the atomic regions between shared-state accesses. Abstracted:
gauge thresholds and weekly pace (a fresh reading passes them), debt and
selection inside a graph (pick_chore is checked against the real code by
repro/check_rules.py), day rollover (all ticks fall in one day), logging.
The decision's three reads (state :909, gauge :919, live context :827) are
merged into one step; the interleavings that matter are between that
snapshot and the lock at :1014.

Part 2 enumerates runner selection (`_runner`, :461-473).

Fix flags, each a candidate change checked here before it was written:
  fixLock  — in the lock, re-read the config and the clock: refuse when
             switched off, when the runner changed, when the graph is on
             cooldown, or when the gauge reading is now too old
  fixWrite — refuse when chore_state.json could not be written (:508)
  fixGauge — judge a Claude reading by its per-window *_seen_at stamps,
             not the file's updated_at (cli/gauge.py:31-37)
  fixCmd   — build the command inside _spawn's try (:700)
  fixAgy   — a timeout kills the agy child too (agy_chore.py:76-78 starts it
             in a session of its own, out of reach of the server's killpg)
-/

inductive Tier where | chore | pass deriving DecidableEq, Repr, Hashable, BEq

inductive PC where
  | start     -- :902-906 cfg = _config(); enabled? _running.is_set()?
  | gated     -- :908-1012 now, state snapshot, gauge, live context, target, caps
  | checked   -- :1014-1035 with _lock: re-check, set _running, write state
  | spawning  -- :1037-1042 log, _spawn (:700 command, :705 Popen)
  | done
  deriving DecidableEq, Repr, Hashable, BEq

structure Th where
  pc : PC := .start
  now : Nat := 0
  agy : Bool := false       -- runner chosen from the cfg read at :902 (:918)
  tier : Tier := .chore
  graph : Nat := 0
  obsCheck : Nat := 0       -- the stamp the freshness gate judged (:571)
  obsTrue : Nat := 0        -- when the oldest window value was really observed
  held : Bool := false      -- id x in the live-context snapshot (:827, :851)
  deriving DecidableEq, Repr, Hashable, BEq

inductive PK where
  | claude    -- claude -p / codex exec: one process group (start_new_session, :708)
  | wrapper   -- agy_chore.py, the process the server started and watches
  | child     -- agy itself, started by the wrapper with start_new_session (agy_chore.py:76-78)
  | orphan    -- an agy child whose wrapper is gone
  deriving DecidableEq, Repr, Hashable, BEq

structure Proc where
  kind : PK
  tier : Tier
  graph : Nat
  held : Bool          -- x was in the LIVE CONTEXT list it was given
  renamed : Bool := false
  deriving DecidableEq, Repr, Hashable, BEq

structure Disp where
  ts : Nat
  tier : Tier
  graph : Nat
  enabled : Bool       -- config at the moment of dispatch
  gaugeAge : Nat       -- clock - obsTrue at the moment of dispatch
  runnerOk : Bool      -- runs on the runner configured at the moment of dispatch
  deriving DecidableEq, Repr, Hashable, BEq

structure S where
  clock : Nat
  fileTs : Nat := 0            -- chore_state.json last_ts
  fileCnt : Nat := 0           -- count
  filePass : Nat := 0          -- pass_count
  fileG : List Nat             -- graphs[g]: last chore on g
  passDue : List Bool          -- days since the last stamped pass ≥ interval (:842-844)
  running : Bool := false
  procs : List Proc := []
  log : List Disp := []
  enabled : Bool := true       -- chores.json "enabled"
  cfgAgy : Bool := false       -- chores.json "runner" is antigravity
  switched : Bool := false     -- the runner edit is one-shot
  diskBad : Bool := false      -- chore_state.json can no longer be written
  seen5 : Nat                  -- five_hour_seen_at
  seen7 : Nat                  -- seven_day_seen_at
  updated : Nat                -- updated_at
  liveOk : Bool := true        -- recently_seen_ids readable
  heldX : Bool := false        -- a live session holds id x
  renamedHeld : Bool := false  -- x was renamed while a session held it
  ths : List Th
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  interval : Nat := 2
  cooldown : Nat := 4
  maxDay : Nat := 3
  passMax : Nat := 1
  maxAge : Nat := 2
  maxClock : Nat := 9
  graphs : Nat := 2
  passDue0 : Bool := true    -- g0 starts due a full pass
  -- environment features
  fAgy : Bool := false       -- runner antigravity from the start
  fSwitch : Bool := false    -- the user edits "runner" mid-flight
  fDisable : Bool := false   -- the user switches chores off mid-flight
  fDisk : Bool := false      -- the state file becomes unwritable
  fFrames : Bool := false    -- status-line frames, some with the 7d window only
  fCmdFail : Bool := false   -- building the runner's command raises
  fLive : Bool := false      -- live context unreadable at times; a session reads x
  -- fixes
  fixLock : Bool := false
  fixWrite : Bool := false
  fixGauge : Bool := false
  fixCmd : Bool := false
  fixAgy : Bool := false

def S.set (s : S) (i : Nat) (t : Th) : S := { s with ths := s.ths.set i t }
def gAt (l : List Nat) (g : Nat) : Nat := l.getD g 0
def bAt (l : List Bool) (g : Nat) : Bool := l.getD g false

/-- The decision on the snapshot: (tier, graph) choices. Pass first (:833-852):
any due graph makes it a pass; otherwise a chore on a graph off cooldown
(:854-858). Which graph among several is left open (debt decides it). -/
def targets (c : Cfg) (s : S) (now : Nat) : List (Tier × Nat) :=
  let gs := List.range c.graphs
  let due := gs.filter (bAt s.passDue ·)
  if !due.isEmpty then due.map (Tier.pass, ·)
  else (gs.filter fun g => now - gAt s.fileG g ≥ c.cooldown).map (Tier.chore, ·)

def thStep (c : Cfg) (s : S) (i : Nat) (t : Th) : List (String × S) :=
  match t.pc with
  | .start =>
      -- :902-906
      if !s.enabled then [(s!"T{i} disabled, return", s.set i {t with pc := .done})]
      else if s.running then [(s!"T{i} fast gate: running, return", s.set i {t with pc := .done})]
      else [(s!"T{i} fast gate: pass (runner {if s.cfgAgy then "agy" else "claude"})",
             s.set i {t with pc := .gated, agy := s.cfgAgy})]
  | .gated =>
      let now := s.clock
      let stop (why : String) := [(s!"T{i} t={now}: {why}", s.set i {t with pc := .done})]
      -- :909-911 snapshot interval
      if now - s.fileTs < c.interval then stop "too soon" else
      -- :919-922 gauge freshness; ClaudeRunner reads cli/gauge.py's file
      let obsTrue := min s.seen5 s.seen7
      let obsCheck := if c.fixGauge then obsTrue else s.updated
      if now - obsCheck > c.maxAge then stop "skip: gauge stale" else
      -- :827-829 live context unknown -> refuse
      if !s.liveOk then stop "skip: live context unknown" else
      let opts := targets c s now
      if opts.isEmpty then stop "skip: no target" else
      opts.filterMap fun (tier, g) =>
        -- :939-957 caps on the snapshot
        let capped : Bool := match tier with
          | .pass => s.filePass ≥ c.passMax
          | .chore => s.fileCnt ≥ c.maxDay
        if capped then none
        else some (s!"T{i} t={now}: decide {repr tier} on g{g} (gauge seen {obsTrue}, judged {obsCheck}{if s.heldX then ", x held" else ""})",
          s.set i {t with pc := .checked, now, tier, graph := g, obsCheck, obsTrue, held := s.heldX})
  | .checked =>
      -- :1014-1035 with _lock
      let now := if c.fixLock then s.clock else t.now
      let refuse (why : String) := [(s!"T{i} lock: refused ({why})", s.set i {t with pc := .done})]
      if s.running then refuse "running" else
      if now - s.fileTs < c.interval then refuse "a concurrent dispatch won" else
      let capped : Bool := match t.tier with
        | .pass => s.filePass ≥ c.passMax
        | .chore => s.fileCnt ≥ c.maxDay
      if capped then refuse "cap" else
      if c.fixLock && !s.enabled then refuse "switched off" else
      if c.fixLock && s.cfgAgy != t.agy then refuse "runner changed" else
      if c.fixLock && t.tier == .chore && now - gAt s.fileG t.graph < c.cooldown then refuse "graph on cooldown" else
      if c.fixLock && now - t.obsCheck > c.maxAge then refuse "gauge went stale" else
      if c.fixWrite && s.diskBad then refuse "state write failed" else
      let s' : S := if s.diskBad then s else   -- _write_state swallows the error (:515)
        match t.tier with
        | .pass => { s with fileTs := now, filePass := s.filePass + 1 }
        | .chore => { s with fileTs := now, fileCnt := s.fileCnt + 1,
                             fileG := s.fileG.set t.graph now }
      let d : Disp := { ts := now, tier := t.tier, graph := t.graph, enabled := s.enabled,
                        gaugeAge := s.clock - t.obsTrue, runnerOk := s.cfgAgy == t.agy }
      [(s!"T{i} lock: DISPATCH {repr t.tier} on g{t.graph} at t={now}{if s.diskBad then " (state write fails silently)" else ""}",
        ({ s' with running := true, log := s.log ++ [d] }).set i {t with pc := .spawning})]
  | .spawning =>
      let fin (s : S) := s.set i {t with pc := .done}
      let ok : List Proc :=
        if t.agy then [{ kind := .wrapper, tier := t.tier, graph := t.graph, held := t.held },
                       { kind := .child, tier := t.tier, graph := t.graph, held := t.held }]
        else [{ kind := .claude, tier := t.tier, graph := t.graph, held := t.held }]
      (if c.fCmdFail then
        -- :700 runner.command() raises before the try: _running stays set
        [(s!"T{i} building the command raises{if c.fixCmd then " -> running cleared" else ""}",
          fin { s with running := if c.fixCmd then false else s.running })] else []) ++
      [ (s!"T{i} Popen fails -> running cleared", fin { s with running := false }),   -- :710-714
        (s!"T{i} spawned {if t.agy then "agy wrapper + agy" else "agent"}", fin { s with procs := s.procs ++ ok }) ]
  | .done => []

def removeAt (l : List α) (i : Nat) : List α := l.eraseIdx i

/-- Process and watcher steps (:716-738). -/
def procSteps (c : Cfg) (s : S) : List (String × S) :=
  (List.range s.procs.length).flatMap fun i =>
    match s.procs[i]? with
    | none => []
    | some p =>
      let stamp (_ : S) := if p.tier == .pass then [true, false] else [false]
      let doStamp (st : Bool) (s : S) :=
        if st then { s with passDue := s.passDue.set p.graph false } else s
      let rename : List (String × S) :=
        -- the agent renames x; it was told not to if x was held at dispatch
        if c.fLive && !p.renamed && !p.held && p.kind != .wrapper then
          [(s!"{repr p.kind} renames x{if s.heldX then " (x is held now)" else ""}",
            { s with procs := s.procs.set i { p with renamed := true },
                     renamedHeld := s.renamedHeld || s.heldX })]
        else []
      match p.kind with
      | .claude =>
          rename ++ (stamp s).map fun st =>
            (s!"agent exits{if st then " (pass stamped)" else ""} / or times out -> killpg; running cleared",
             doStamp st { s with procs := removeAt s.procs i, running := false })
      | .wrapper =>
          -- the child is the next entry (spawned together)
          let finish := (stamp s).map fun st =>
            (s!"agy finishes, wrapper exits{if st then " (pass stamped)" else ""}; running cleared",
             doStamp st { s with procs := removeAt (removeAt s.procs (i+1)) i, running := false })
          let timeout :=
            if c.fixAgy then
              (s!"timeout: killpg(wrapper) -> wrapper kills agy first; running cleared",
               { s with procs := removeAt (removeAt s.procs (i+1)) i, running := false })
            else
              (s!"timeout: killpg(wrapper) -> wrapper dies, agy survives in its own session; running cleared",
               { s with procs := (removeAt s.procs i).modify i (fun ch => { ch with kind := .orphan }),
                        running := false })
          finish ++ [timeout]
      | .child => rename
      | .orphan =>
          rename ++ [(s!"orphaned agy finishes", { s with procs := removeAt s.procs i })]

def envSteps (c : Cfg) (s : S) : List (String × S) :=
  (if s.clock < c.maxClock then [("tick", { s with clock := s.clock + 1 })] else []) ++
  (if c.fDisable && s.enabled then [("user switches chores off", { s with enabled := false })] else []) ++
  (if c.fSwitch && !s.switched then [("user changes the runner",
      { s with cfgAgy := !s.cfgAgy, switched := true })] else []) ++
  (if c.fDisk && !s.diskBad then [("chore_state.json becomes unwritable", { s with diskBad := true })] else []) ++
  (if c.fFrames && (s.seen5 != s.clock || s.seen7 != s.clock) then
      [("status line: frame with both windows", { s with seen5 := s.clock, seen7 := s.clock, updated := s.clock })]
   else []) ++
  (if c.fFrames && s.seen7 != s.clock then
      [("status line: frame with the 7d window only (5h value carried)", { s with seen7 := s.clock, updated := s.clock })]
   else []) ++
  (if c.fLive then [("live context " ++ (if s.liveOk then "becomes unreadable" else "readable again"),
                     { s with liveOk := !s.liveOk })] else []) ++
  (if c.fLive && !s.heldX then [("a session reads x", { s with heldX := true })] else [])

def next (c : Cfg) (s : S) : List (String × S) :=
  let th := (List.range s.ths.length).flatMap fun i =>
    match s.ths[i]? with
    | some t => thStep c s i t
    | none => []
  th ++ procSteps c s ++ envSteps c s

/-! Properties -/
def pairs : List α → List (α × α)
  | [] => []
  | x :: xs => xs.map (fun y => (x, y)) ++ pairs xs

def dist (a b : Nat) : Nat := if a ≤ b then b - a else a - b
def chores (s : S) := s.log.filter (·.tier == .chore)
def passes (s : S) := s.log.filter (·.tier == .pass)
def runs (s : S) : Nat := (s.procs.filter (·.kind != .child)).length

def P1 (_ : Cfg) (s : S) : Bool := runs s ≤ 1
def P2 (c : Cfg) (s : S) : Bool := (pairs s.log).all fun (a, b) => dist a.ts b.ts ≥ c.interval
def P3 (c : Cfg) (s : S) : Bool := (chores s).length ≤ c.maxDay && (passes s).length ≤ c.passMax
def P4 (_ : Cfg) (s : S) : Bool := s.fileCnt == (chores s).length && s.filePass == (passes s).length
def P5 (c : Cfg) (s : S) : Bool :=
  (pairs (chores s)).all fun (a, b) => a.graph != b.graph || dist a.ts b.ts ≥ c.cooldown
def P6 (_ : Cfg) (s : S) : Bool := s.log.all (·.enabled)
def P7 (c : Cfg) (s : S) : Bool := s.log.all (·.gaugeAge ≤ c.maxAge)
def P8 (_ : Cfg) (s : S) : Bool := s.log.all (·.runnerOk)
def P9 (_ : Cfg) (s : S) : Bool :=
  !s.running || s.procs.any (·.kind != .orphan) || s.ths.any (·.pc == .spawning)
def L1 (_ : Cfg) (s : S) : Bool := !s.renamedHeld

partial def bfs (c : Cfg) (bad : S → Bool) (init : S) (limit : Nat := 3000000) :
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

def init (c : Cfg) (k : Nat) : S :=
  { clock := 5, fileG := List.replicate c.graphs 0,
    passDue := (List.range c.graphs).map (fun g => c.passDue0 && g == 0),
    cfgAgy := c.fAgy, seen5 := 5, seen7 := 5, updated := 5,
    ths := List.replicate k {} }

def report (name : String) (r : Option (List String) × Nat) : IO Unit := do
  match r with
  | (some tr, n) =>
      IO.println s!"✗ {name}: COUNTEREXAMPLE ({n} states explored)"
      for l in tr do IO.println s!"    {l}"
  | (none, n) => IO.println s!"✓ {name}: no counterexample ({n} states, exhaustive)"

def props : List (String × (Cfg → S → Bool)) :=
  [ ("P1 at most one maintenance run alive (both tiers, agy child included)", P1),
    ("P2 dispatches ≥ min_interval apart (both tiers)", P2),
    ("P3 daily caps (chores, passes)", P3),
    ("P4 stored counts = dispatches (count, pass_count)", P4),
    ("P5 per-graph chore cooldown", P5),
    ("P6 no dispatch while switched off", P6),
    ("P7 every dispatch gated on a gauge observed ≤ max age ago", P7),
    ("P8 every dispatch runs on the runner configured at that moment", P8),
    ("P9 _running set ⇒ a run or a dispatcher in flight (no wedge)", P9) ]

def runRow (title : String) (c : Cfg) (k : Nat) (only : List Nat := []) (showLimit := false)
    (together := false) : IO Unit := do
  IO.println s!"--- {title}: threads={k} graphs={c.graphs} interval={c.interval} cooldown={c.cooldown} maxDay={c.maxDay} passMax={c.passMax} maxAge={c.maxAge} ticks≤{c.maxClock}"
  IO.println s!"    fixes: lock={c.fixLock} write={c.fixWrite} gauge={c.fixGauge} cmd={c.fixCmd} agy={c.fixAgy}"
  let i := init c k
  let sel := if only.isEmpty then props else
    (props.zip (List.range props.length)).filterMap fun (p, j) => if only.contains (j+1) then some p else none
  if together then
    report "P1-P9 together" (bfs c (fun s => !sel.all (fun (_, p) => p c s)) i)
  else
    for (name, p) in sel do
      report name (bfs c (fun s => !p c s) i)
  if showLimit then
    report "L1 (known limit, expect ✗) no rename of an id a session holds at rename time"
      (bfs c (fun s => !L1 c s) i)
  if c.passDue0 then
    report "sanity (expect ✗): a pass and a chore both dispatched"
      (bfs c (fun s => (passes s).length ≥ 1 && (chores s).length ≥ 1) i)
  else
    report "sanity (expect ✗): two dispatches"
      (bfs c (fun s => s.log.length ≥ 2) i)

/-! Part 2: runner selection (`_runner`, chore_dispatch.py:461-473). -/

inductive Bin where | unset | good | typo deriving DecidableEq, Repr

/-- binary(): an explicit *_bin wins even when wrong (:194-196, :324-326, :419-421). -/
def binary (bin : Bin) (installed : Bool) : Bool :=
  match bin with | .unset => installed | .good => true | .typo => false

def names : List String := ["claude", "codex", "antigravity"]

/-- choice: none = "auto". -/
def select (fixed : Bool) (choice : Option Nat) (bins : List Bin) (inst : List Bool) : Nat :=
  match choice with
  | some r => r
  | none =>
    let idx := List.range 3
    let pinned := idx.find? fun r => bins.getD r .unset != .unset
    let found := idx.find? fun r => bins.getD r .unset != .unset || binary (bins.getD r .unset) (inst.getD r false)
    if fixed then (pinned.orElse fun _ => idx.find? fun r => binary (bins.getD r .unset) (inst.getD r false)).getD 0
    else found.getD 0

def runnerEnum (fixed : Bool) : IO Unit := do
  let bins := [Bin.unset, .good, .typo]
  let mut total := 0
  let mut bad := 0
  let mut first : Option String := none
  for choice in [none, some 0, some 1, some 2] do
    for b2 in bins do for b1 in bins do for b0 in bins do
      for i0 in [true, false] do for i1 in [false, true] do for i2 in [false, true] do
        total := total + 1
        let bs := [b0, b1, b2]
        let inst := [i0, i1, i2]
        let sel := select fixed choice bs inst
        let pinned := (List.range 3).filter fun r => bs.getD r .unset != .unset
        -- R1: under "auto", a single configured binary names the runner, and the
        -- run spends no other subscription (it runs only if that binary exists)
        if choice.isNone && pinned.length == 1 && sel != pinned.getD 0 0 &&
            binary (bs.getD sel .unset) (inst.getD sel false) then
          bad := bad + 1
          if first.isNone then
            first := some s!"auto, claude_bin={repr b0} codex_bin={repr b1} antigravity_bin={repr b2}, installed={inst}: runs {names.getD sel "?"}, pinned {names.getD (pinned.getD 0 0) "?"}"
  let tag := if fixed then "fixed=true " else "fixed=false"
  match first with
  | some f => IO.println s!"✗ R1 {tag} a configured *_bin pins its runner under auto: {bad} of {total} configs run another subscription; first: {f}"
  | none => IO.println s!"✓ R1 {tag} a configured *_bin pins its runner under auto: 0 of {total} configs (exhaustive)"

def main (args : List String) : IO Unit := do
  let all := args.isEmpty
  let want (k : String) := all || args.contains k
  let base : Cfg := {}
  let fixedAll (c : Cfg) : Cfg :=
    { c with fixLock := true, fixWrite := true, fixGauge := true, fixCmd := true, fixAgy := true }
  if want "runner" then
    IO.println "=== Part 2: runner selection (enumeration) ==="
    runnerEnum false
    runnerEnum true
  if want "base" then
    runRow "base: chore + pass tiers, claude runner" base 2
    runRow "base" (fixedAll base) 2
    runRow "chores only (no graph due a pass)" { base with passDue0 := false } 2 [2, 3, 4, 5]
    runRow "chores only" (fixedAll { base with passDue0 := false }) 2 [2, 3, 4, 5]
  if want "agy" then
    runRow "antigravity runner with timeouts" { base with fAgy := true, passDue0 := false } 2 [1, 9]
    runRow "antigravity runner with timeouts" (fixedAll { base with fAgy := true, passDue0 := false }) 2 [1, 9]
  if want "disk" then
    runRow "state file becomes unwritable" { base with fDisk := true } 2 [2, 3, 4]
    runRow "state file becomes unwritable" (fixedAll { base with fDisk := true }) 2 [2, 3, 4]
  if want "frames" then
    runRow "status-line frames carrying one window" { base with fFrames := true, graphs := 1 } 2 [7]
    runRow "status-line frames carrying one window" (fixedAll { base with fFrames := true, graphs := 1 }) 2 [7]
    runRow "frames, fixGauge only (no lock re-check)" { base with fFrames := true, graphs := 1, fixGauge := true } 2 [7]
    runRow "frames, fixLock only (judged by updated_at)" { base with fFrames := true, graphs := 1, fixLock := true } 2 [7]
  if want "config" then
    runRow "config edits mid-flight" { base with fDisable := true, fSwitch := true, graphs := 1 } 2 [6, 8]
    runRow "config edits mid-flight" (fixedAll { base with fDisable := true, fSwitch := true, graphs := 1 }) 2 [6, 8]
  if want "cmd" then
    runRow "runner command raises" { base with fCmdFail := true, graphs := 1 } 2 [9]
    runRow "runner command raises" (fixedAll { base with fCmdFail := true, graphs := 1 }) 2 [9]
  if want "live" then
    runRow "live context: unreadable at times, a session reads x" { base with fLive := true } 2 [1, 2, 3, 4] (showLimit := true)
    runRow "live context" (fixedAll { base with fLive := true }) 2 [1, 2, 3, 4] (showLimit := true)
  if want "all" then
    -- one search for the conjunction of P1-P9 (the row is large; each
    -- property separately would repeat the same exhaustive search nine times)
    let every : Cfg := { base with fAgy := true, fDisk := true, fFrames := true, fDisable := true, fSwitch := true, fCmdFail := true, graphs := 1, maxClock := 8 }
    runRow "every feature at once" (fixedAll every) 2 (together := true)
