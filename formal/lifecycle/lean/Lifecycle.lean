import Std
/-!
Model of the server lifecycle: `kg start` / `kg stop` / `kg restart` / `kg update`,
the SessionStart hook, the `kg mcp` shim, the systemd unit and a server on
another port. After knowledge-graph/cli/kg.py, cli/mcp_shim.py, cli/steps.py,
cli/doctor.py, hooks/kg-autostart.sh and server/mcp_streamable_server.py.

A pid is a slot (0..2). A slot whose process is dead can go to the next
process (pid reuse); `gen` says which process holds a pid. Each step is one
atomic region of the code. `lock` is start.lock and names its holder.
Simplification: a start's last /health probe and its breadcrumb write are one
step (between them the code only reads the log).

Fixes (`fixed`):
  1. the server removes its port's breadcrumb once it is serving (after the bind);
  2. a start that gives up while its process still runs keeps the pid file, and a
     start that finds the server running removes the breadcrumb;
  3. the pid file is trusted only for the process started when it was written
     (process start time vs the file's mtime), not for a later owner of the pid;
  4. kg stop and kg restart hold start.lock, as kg start does.
-/

inductive Stage where
  | free | loading | claimed | up | stopping | dead
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

structure Proc where
  stage : Stage := .free
  ours : Bool := true    -- our port, our storage (false: another port's server, own storage)
  ver : Nat := 0
  gen : Nat := 0
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

inductive Pc where
  | blocked | idle | ready | waiting (pid gen : Nat) | svcWait | done
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

structure St where
  kind : Nat             -- 0 the user's kg start / restart, 1 shim (kg mcp), 2 SessionStart hook
  pc : Pc
  res : Nat := 0         -- 1 reported success, 2 reported failure
  deriving DecidableEq, Repr, Hashable, BEq, Inhabited

inductive Upc where
  | idle | stopWait (pids : List Nat) | done
  deriving DecidableEq, Repr, Hashable, BEq

structure S where
  procs : List Proc
  pidFile : Option (Nat × Nat)   -- pid, and the process it was written for
  crumb : Bool
  last : Nat                     -- last completed start attempt: 0 none, 1 ok, 2 failed
  disturbed : Bool               -- a crash or a stop since then
  sts : List St
  lock : Option Nat              -- start.lock holder: a starter index, or 9 (kg stop)
  user : Upc
  diskVer : Nat
  unitActive : Bool
  unitMain : Option Nat
  unitFailed : Bool              -- Restart=on-failure respawn pending
  crashes : Nat
  otherDone : Bool
  spawns : Nat
  killedOther : Bool
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  svc : Bool := false            -- kg-memory.service enabled for this port
  lsof : Bool := true            -- lsof or fuser on PATH (port_owners works)
  fixed : Bool := false
  off : Nat := 0                 -- with `fixed`: leave fix number `off` out (0: all in)
  userMode : Nat := 0            -- 0 none, 1 kg stop, 2 kg update (= upgrade, then restart)
  maxCrash : Nat := 0
  other : Bool := false          -- a server on another port may start
  maxSpawns : Nat := 4

def fx (c : Cfg) (n : Nat) : Bool := c.fixed && c.off != n

def alive (p : Proc) : Bool := [Stage.loading, .claimed, .up, .stopping].contains p.stage
def pidsOf (s : S) : List Nat := List.range s.procs.length
def health (s : S) : Bool := s.procs.any (fun p => p.ours && p.stage == .up)
def setP (s : S) (i : Nat) (p : Proc) : S := { s with procs := s.procs.set i p }
def setSt (s : S) (i : Nat) (pc : Pc) : S := { s with sts := s.sts.set i { s.sts[i]! with pc := pc } }
def release (s : S) (i : Nat) : S := if s.lock == some i then { s with lock := none } else s
def userIdx (s : S) : Nat := (s.sts.findIdx? (fun t => t.kind == 0)).getD 9

-- kg.py:72-81 port_owners (lsof/fuser, SERVER_MARK): our servers listening on our port
def portOwners (c : Cfg) (s : S) : List Nat :=
  if c.lsof then (pidsOf s).filter (fun i => s.procs[i]!.ours && s.procs[i]!.stage == .up) else []

-- kg.py:84-93 running_pid: the pid file if alive with SERVER_MARK in its args, else a port owner
def runningPid (c : Cfg) (s : S) : Option Nat :=
  let fromFile := match s.pidFile with
    | some (p, g) => if alive s.procs[p]! && (!fx c 3 || s.procs[p]!.gen == g) then some p else none
    | none => none
  match fromFile with
  | some p => some p
  | none => (portOwners c s).head?

-- a new process at any free pid (kg.py:207 Popen, systemd ExecStart)
def spawn (c : Cfg) (s : S) (p : Proc) : List (Nat × S) :=
  if s.spawns ≥ c.maxSpawns then [] else
  (pidsOf s).filterMap fun i =>
    if alive s.procs[i]! then none
    else some (i, { setP s i { p with gen := s.spawns + 1 } with spawns := s.spawns + 1 })

-- os.kill(pid, SIGTERM): whatever holds that pid now (kg.py:179, :245)
def kill (s : S) (i : Nat) : S :=
  let p := s.procs[i]!
  if !alive p then s else
  let s := if p.ours then s else { s with killedOther := true }
  match p.stage with
  | .up => setP s i { p with stage := .stopping }     -- listener closed; flush + commit still to run
  | .stopping => s
  | _ => setP s i { p with stage := .dead }           -- before main() installs its handler

def dieFailed (s : S) (i : Nat) : S :=
  let s := setP s i { s.procs[i]! with stage := .dead }
  if s.unitMain == some i then { s with unitFailed := true } else s

def finish (s : S) (i : Nat) (ok : Bool) : S :=
  let s := release { s with sts := s.sts.set i { s.sts[i]! with pc := .done, res := if ok then 1 else 2 } } i
  if ok then { s with crumb := false, last := 1, disturbed := false }
  else { s with crumb := true, last := 2 }

def serverSteps (c : Cfg) (s : S) : List (String × S) :=
  (pidsOf s).foldr (fun i acc =>
    let p := s.procs[i]!
    let holders := (pidsOf s).filter (fun j => j != i && s.procs[j]!.ours &&
                     [Stage.claimed, .up, .stopping].contains s.procs[j]!.stage)
    let here : List (String × S) := if !p.ours then [] else match p.stage with
      -- mcp_streamable_server.py:868, :139-146 flock(storage, LOCK_EX|LOCK_NB) or exit 1
      | .loading => if holders.isEmpty then [(s!"server {i} locks the storage", setP s i { p with stage := .claimed })]
                    else [(s!"server {i} exits: another memory server already uses the storage", dieFailed s i)]
      -- :984-1009 uvicorn binds the port (uvicorn sets `started` after the bind)
      | .claimed => if health s then [(s!"server {i} exits: port in use", dieFailed s i)]
                    else [(s!"server {i} binds the port, answers /health" ++ (if fx c 1 then ", removes the breadcrumb" else ""),
                           let s1 := setP s i { p with stage := .up }
                           if fx c 1 then { s1 with crumb := false } else s1)]
      -- :944-958 graceful shutdown: store flush, final commit, exit 0
      | .stopping => [(s!"server {i} finishes its shutdown", setP s i { p with stage := .dead })]
      | .up => if s.crashes < c.maxCrash then
                 [(s!"server {i} crashes", { dieFailed s i with crashes := s.crashes + 1, disturbed := true })]
               else []
      | _ => []
    here ++ acc) []

def starterSteps (c : Cfg) (s : S) (i : Nat) : List (String × S) :=
  let t := s.sts[i]!
  let who := match t.kind with | 0 => "user" | 1 => "shim" | _ => "hook"
  let lockOk := s.lock == none || s.lock == some i
  match t.pc with
  | .blocked | .done => []
  | .idle =>
    -- mcp_shim.py:46-53 ensure_server; kg-autostart.sh:38-56
    if t.kind != 0 && health s then [(s!"{who}: server answers, nothing to start", setSt s i .done)]
    else if t.kind == 2 && s.crumb then [(s!"{who}: down + breadcrumb: reports it, does not start", setSt s i .done)]
    else [(s!"{who}: server down, runs kg start", setSt s i .ready)]
  | .ready =>
    if !lockOk then [] else
    if c.svc then
      -- kg.py:194-196 stop_strays, then systemctl start (kg.py:257-260 restart: systemctl restart)
      let s1 := ((portOwners c s).filter (fun j => s.unitMain != some j)).foldl kill { s with lock := some i }
      if s1.unitActive then [(s!"{who}: systemctl start (unit active)", setSt s1 i .svcWait)]
      else (spawn c s1 { stage := .loading, ver := s.diskVer }).map fun (j, s2) =>
        (s!"{who}: systemctl start -> unit spawns server {j}",
         setSt { s2 with unitActive := true, unitMain := some j, unitFailed := false } i .svcWait)
    else match runningPid c s with
    | some p =>   -- kg.py:197-200
      let s1 := release { s with sts := s.sts.set i { t with pc := .done, res := 1 }, last := 1, disturbed := false } i
      [(s!"{who}: kg start: already running (pid {p})" ++ (if fx c 2 then ", breadcrumb removed" else ""),
        if fx c 2 then { s1 with crumb := false } else s1)]
    | none =>
      if health s then   -- kg.py:201-205 port_free() is false
        [(s!"{who}: kg start: port held -> breadcrumb", finish s i false)]
      else (spawn c s { stage := .loading, ver := s.diskVer }).map fun (j, s2) =>
        (s!"{who}: kg start spawns server {j}, writes the pid file",   -- :206-210
         setSt { s2 with lock := some i, pidFile := some (j, s2.procs[j]!.gen) } i (.waiting j s2.procs[j]!.gen))
  | .waiting p g =>
    let mine := s.procs[p]!.gen == g && alive s.procs[p]!
    -- kg.py:213 wait(proc exited or /health answers, 15 s) and proc still running
    if mine && health s then [(s!"{who}: /health answers and its process runs: started", finish s i true)]
    else if !mine then
      [(s!"{who}: its server exited -> breadcrumb, pid file removed", { finish s i false with pidFile := none })]
    else  -- kg.py:217-219 15 s pass
      let s1 := finish s i false
      [(s!"{who}: 15 s without /health -> breadcrumb" ++ (if fx c 2 then " (server still starting: pid file kept)" else ", pid file removed; its server keeps running"),
        if fx c 2 then s1 else { s1 with pidFile := none })]
  | .svcWait =>   -- kg.py:163-169 (clear on success, record on failure)
    if health s then [(s!"{who}: /health answers -> breadcrumb cleared", finish s i true)]
    else [(s!"{who}: 20 s without /health -> breadcrumb", finish s i false)]

def userSteps (c : Cfg) (s : S) : List (String × S) :=
  let u := userIdx s
  let owner := if c.userMode == 2 then u else 9
  let afterStop (s : S) : S :=
    let s := { s with user := .done }
    if c.userMode == 2 then setSt s u .ready
    else if s.lock == some 9 then { s with lock := none } else s
  match s.user with
  | .done => []
  | .idle =>
    if c.userMode == 0 || (fx c 4 && s.lock != none) then [] else
    let tag := if c.userMode == 2 then "kg update: new version on disk; restart" else "kg stop"
    let s0 := { s with diskVer := if c.userMode == 2 then 1 else s.diskVer,
                       lock := if fx c 4 then some owner else s.lock }
    if c.svc && c.userMode == 2 then
      -- steps.py:307-310 Server.apply -> kg.py:257-260: stop_strays, systemctl restart
      let s1 := ((portOwners c s0).filter (fun j => s0.unitMain != some j)).foldl kill s0
      let s1 := match s1.unitMain with
        | some m => if alive s1.procs[m]! then setP s1 m { s1.procs[m]! with stage := .dead } else s1
        | none => s1
      (spawn c s1 { stage := .loading, ver := s1.diskVer }).map fun (j, s2) =>
        (s!"{tag}: systemctl restart -> unit spawns server {j}",
         setSt { s2 with user := .done, unitActive := true, unitMain := some j, unitFailed := false, disturbed := true } u .svcWait)
    else
      -- kg.py:235-245 (systemd: systemctl stop first, which waits for the exit)
      let s1 := if c.svc then
        match s0.unitMain with
        | some m => let s2 := { s0 with unitActive := false, unitFailed := false }
                    if alive s2.procs[m]! then setP s2 m { s2.procs[m]! with stage := .dead } else s2
        | none => { s0 with unitActive := false }
        else s0
      let pids := ((runningPid c s1).toList ++ portOwners c s1).eraseDups
      if pids.isEmpty then
        [(s!"{tag}: nothing running, pid file removed", afterStop { s1 with pidFile := none, disturbed := true })]
      else
        [(s!"{tag}: SIGTERM pids {pids}", { pids.foldl kill s1 with user := .stopWait pids, disturbed := true })]
  | .stopWait pids =>
    let s1 := { s with pidFile := none }   -- kg.py:251
    if pids.all (fun p => !alive s.procs[p]!) then
      [(s!"kg stop: all exited, pid file removed", afterStop s1)]
    else   -- kg.py:246-249 after 6 s, SIGKILL whatever still runs
      [(s!"kg stop: 6 s pass, SIGKILL, pid file removed",
        afterStop (pids.foldl (fun s p => if alive s.procs[p]! then setP s p { s.procs[p]! with stage := .dead } else s) s1))]

def envSteps (c : Cfg) (s : S) : List (String × S) :=
  let respawn := if c.svc && s.unitActive && s.unitFailed then
    (spawn c s { stage := .loading, ver := s.diskVer }).map fun (j, s2) =>
      (s!"systemd Restart=on-failure spawns server {j}", { s2 with unitMain := some j, unitFailed := false })
    else []
  let other := if c.other && !s.otherDone then
    (spawn c s { stage := .up, ours := false }).map fun (j, s2) =>
      (s!"a server on another port starts with pid {j}", { s2 with otherDone := true })
    else []
  respawn ++ other

def next (c : Cfg) (s : S) : List (String × S) :=
  serverSteps c s ++ ((List.range s.sts.length).flatMap (starterSteps c s)) ++ userSteps c s ++ envSteps c s

def quiet (c : Cfg) (s : S) : Bool :=
  s.sts.all (fun t => t.pc == .done) && (c.userMode == 0 || s.user == .done) &&
  s.procs.all (fun p => !([Stage.loading, .claimed, .stopping].contains p.stage)) &&
  !(c.svc && s.unitActive && s.unitFailed)

-- properties: true on a BAD state
def pStorage (s : S) : Bool := (s.procs.filter fun p => p.ours && [Stage.claimed, .up, .stopping].contains p.stage).length > 1
def pPort (s : S) : Bool := (s.procs.filter fun p => p.ours && p.stage == .up).length > 1
def pCrumb (s : S) : Bool := s.crumb && s.last == 1
def pStale (c : Cfg) (s : S) : Bool := quiet c s && s.crumb && health s
def pServes (c : Cfg) (s : S) : Bool := quiet c s && s.last == 1 && !s.disturbed && !health s
def pTracked (c : Cfg) (s : S) : Bool :=
  quiet c s && (pidsOf s).any (fun i => s.procs[i]!.ours && s.procs[i]!.stage == .up &&
    runningPid c s != some i && !(portOwners c s).contains i)
def pUpdate (c : Cfg) (s : S) : Bool :=
  c.userMode == 2 && quiet c s && s.sts.any (fun t => t.kind == 0 && t.res == 1) &&
  s.procs.any (fun p => p.ours && p.stage == .up && p.ver == 0)
def sanity (c : Cfg) (s : S) : Bool :=
  quiet c s && health s && s.sts.all (fun t => t.res != 2) && s.sts.any (fun t => t.res == 1)

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

def cold (kinds : List Nat) : S :=
  { procs := [{}, {}, {}], pidFile := none, crumb := false, last := 0, disturbed := false,
    sts := kinds.map fun k => { kind := k, pc := .idle }, lock := none, user := .idle, diskVer := 0,
    unitActive := false, unitMain := none, unitFailed := false, crashes := 0,
    otherDone := false, spawns := 0, killedOther := false }

-- our server (version 0) serves as pid 0, started by kg start or by the unit
def warm (c : Cfg) (kinds : List Nat) : S :=
  let s := cold kinds
  { s with procs := [{ stage := .up }, {}, {}], pidFile := some (0, 0),
           unitActive := c.svc, unitMain := if c.svc then some 0 else none,
           sts := s.sts.map fun t => if t.kind == 0 && c.userMode == 2 then { t with pc := .blocked } else t }

def runAll (title : String) (c : Cfg) (init : S) : IO Unit := do
  IO.println s!"--- {title}: svc={c.svc} lsof={c.lsof} fixed={c.fixed}{if c.fixed && c.off != 0 then s!" without fix {c.off}" else ""}"
  report "L1 at most one server holds the storage" (bfs c pStorage init)
  report "L2 at most one server on the port" (bfs c pPort init)
  report "L3 kg stop never signals another port's server" (bfs c (fun s => s.killedOther) init)
  report "L4 a breadcrumb only while the latest start attempt failed" (bfs c pCrumb init)
  report "L5 a start that reported success leaves a server answering" (bfs c (pServes c) init)
  report "L6 settled: no breadcrumb while the server answers" (bfs c (pStale c) init)
  report "L7 settled: kg can find (and stop) every server on its port" (bfs c (pTracked c) init)
  if c.userMode == 2 then
    report "L8 kg update reported success: only the new version serves" (bfs c (pUpdate c) init)
  report "sanity (expect ✗): settled and served, no start failed" (bfs c (sanity c) init)

def main : IO Unit := do
  -- today's code; all four fixes; then each fix left out, to show what it is for
  for (fixed, off) in [(false, 0), (true, 0), (true, 1), (true, 2), (true, 3), (true, 4)] do
    IO.println s!"===== fixed={fixed}{if off != 0 then s!" without fix {off}" else ""} (bounds: 3 pids, ≤4 spawns, ≤1 crash)"
    for lsof in [true, false] do
      runAll "cold: shim + hook + the user's kg start" { lsof, fixed, off } (cold [1, 2, 0])
    let c : Cfg := { fixed, off, userMode := 1, maxCrash := 1, other := true }
    runAll "warm: crash, a server on another port, kg stop, hook, kg start" c (warm c [2, 0])
    let c : Cfg := { fixed, off, userMode := 2 }
    runAll "warm: kg update while a shim starts the server" c (warm c [1, 0])
    let c : Cfg := { fixed, off, svc := true, maxCrash := 1 }
    runAll "systemd, cold: shim + hook, a crash, Restart=on-failure" c (cold [1, 2])
    let c : Cfg := { fixed, off, svc := true, userMode := 2 }
    runAll "systemd, warm: kg update while a shim starts the server" c (warm c [1, 0])
