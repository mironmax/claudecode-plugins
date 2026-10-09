import Std
/-!
Model of the budget notices (0.11.0): `budget.notice` and `reading_for` in
knowledge-graph/server/mcp_http/budget.py, `note_budget_level` and the
session persistence in mcp_http/session_manager.py.

One session, one quota window kind (the five-hour and weekly windows are
independent and identical in the code: a loop over THRESHOLDS, budget.py:92).
Two hooks of that session race: the prompt hook (rest.py:294 via
_with_budget, antigravity.py:273) and the tool hook (rest.py:367 via
_with_budget). An invocation reads the gauge (budget.py:88, reading_for)
and notes-and-emits (budget.py:98, note_budget_level, which checks,
records and saves under the manager's RLock, session_manager.py:652). Both
run in one synchronous handler on the event loop (no await between them),
so an invocation is one step; `splitRead` makes them two, to show what
that atomicity buys.

Environment: usage rises (level 0 -> 1 -> 2: below warn, warn, stop); the
window resets (a new resets_at, level 0); the gauge source catches up with
the present (a status-line frame, a Codex token_count event, or the
Antigravity cache refresh, budget.py:33-52); the server restarts (the
in-memory record is replaced by sessions.json, _load_sessions).

A window is identified by its resets_at, as the code does (`heard_resets !=
resets_at`, session_manager.py:661). A reading of a window whose reset has
passed is zeroed for Claude Code (budget.py:66-70) and inside codex_limits /
antigravity_limits at read time, but the Antigravity path returns the
cached reading as it was cached (budget.py:61-62): `zeroAgy` is that fix.

Variants: `atomicNote=false` splits note_budget_level's check from its
record (what the lock prevents); `splitRead=true` lets a reset or the
other hook land between a hook's read and its note; `saveOk=false` makes
save_sessions fail (it logs and goes on, session_manager.py:747).
-/

structure Reading where
  wid : Nat     -- which window (resets_at)
  lvl : Nat     -- 0, 1 warn, 2 stop
  deriving DecidableEq, Repr, Hashable, BEq

inductive PC where | idle | read | checked deriving DecidableEq, Repr, Hashable, BEq

structure Hook where
  pc : PC := .idle
  r : Reading := ⟨0, 0⟩
  left : Nat           -- invocations still to come
  deriving DecidableEq, Repr, Hashable, BEq

structure S where
  w : Nat := 0                   -- current window
  u : Nat := 0                   -- true level in it
  src : Reading := ⟨0, 0⟩        -- what the gauge source says
  heard : Reading := ⟨0, 0⟩      -- budget_levels[window] = [level, resets_at], in memory
  disk : Reading := ⟨0, 0⟩       -- the same in sessions.json
  restarts : Nat := 0
  said : List (Reading × Nat) := []   -- (what was said, current window when said)
  hooks : List Hook
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  windows : Nat := 2          -- resets allowed
  maxRestarts : Nat := 1
  agy : Bool := false         -- the reading comes from the Antigravity cache
  zeroAgy : Bool := false     -- fix: zero a passed window on the cached reading too
  atomicNote : Bool := true
  splitRead : Bool := false   -- read and note as separate steps (not the code: one handler)
  saveOk : Bool := true

def S.set (s : S) (i : Nat) (h : Hook) : S := { s with hooks := s.hooks.set i h }

/-- reading_for: a reading of a window that has already reset reads empty,
except on the Antigravity cache path (unless fixed). -/
def readingFor (c : Cfg) (s : S) : Reading :=
  if s.src.wid < s.w && (!c.agy || c.zeroAgy) then { s.src with lvl := 0 } else s.src

/-- note_budget_level's decision: is this level new for this window? -/
def isNew (heard r : Reading) : Bool :=
  let h := if heard.wid != r.wid then 0 else heard.lvl
  r.lvl > h

/-- note_budget_level and the emit, on reading `r`. -/
def noteStep (c : Cfg) (s : S) (i : Nat) (h : Hook) (r : Reading) (name : String) :
    List (String × S) :=
  if r.lvl == 0 then [(s!"{name}: below warn, silent", s.set i { h with pc := .idle })] else
  if !isNew s.heard r then [(s!"{name}: already said", s.set i { h with pc := .idle })] else
  if c.atomicNote then
    let s' := { s with heard := r, disk := if c.saveOk then r else s.disk,
                       said := s.said ++ [(r, s.w)] }
    [(s!"{name}: note_budget_level -> new; SAYS level {r.lvl} of window {r.wid}",
      s'.set i { h with pc := .idle })]
  else [(s!"{name}: check -> new", s.set i { h with pc := .checked, r })]

def hookStep (c : Cfg) (s : S) (i : Nat) (h : Hook) : List (String × S) :=
  let name := if i == 0 then "prompt hook" else "tool hook"
  match h.pc with
  | .idle =>
      if h.left == 0 then [] else
      let r := readingFor c s
      let h := { h with left := h.left - 1 }
      if c.splitRead then
        [(s!"{name}: reads gauge (window {r.wid}, level {r.lvl})", s.set i { h with pc := .read, r })]
      else
        (noteStep c s i h r name).map fun (l, s') => (s!"{name}: reads (window {r.wid}, level {r.lvl}); " ++ l.drop (name.length + 2), s')
  | .read => noteStep c s i h h.r name
  | .checked =>
      let s' := { s with heard := h.r, disk := if c.saveOk then h.r else s.disk,
                         said := s.said ++ [(h.r, s.w)] }
      [(s!"{name}: records; SAYS level {h.r.lvl} of window {h.r.wid}", s'.set i { h with pc := .idle })]

def envSteps (c : Cfg) (s : S) : List (String × S) :=
  (if s.u < 2 then [(s!"usage rises to level {s.u + 1}", { s with u := s.u + 1 })] else []) ++
  (if s.w < c.windows then [(s!"window {s.w} resets", { s with w := s.w + 1, u := 0 })] else []) ++
  (if s.src != ⟨s.w, s.u⟩ then
     [(s!"gauge source catches up (window {s.w}, level {s.u})", { s with src := ⟨s.w, s.u⟩ })]
   else []) ++
  (if s.restarts < c.maxRestarts then
     [("server restarts: the record is reloaded from sessions.json",
       { s with heard := s.disk, restarts := s.restarts + 1,
                hooks := s.hooks.map fun h => { h with pc := .idle } })]
   else [])

def next (c : Cfg) (s : S) : List (String × S) :=
  ((List.range s.hooks.length).flatMap fun i =>
    match s.hooks[i]? with
    | some h => hookStep c s i h
    | none => []) ++ envSteps c s

/-! Properties -/
def B1 (s : S) : Bool :=   -- once per level per window
  let rs := s.said.map (·.1)
  rs.eraseDups.length == rs.length
def B2 (s : S) : Bool :=   -- never a notice about a window that has already reset
  s.said.all fun (r, now) => r.wid == now

partial def bfs (c : Cfg) (bad : S → Bool) (init : S) : Option (List String) × Nat := Id.run do
  let mut seen : Std.HashSet S := {}
  seen := seen.insert init
  let mut frontier : Array (S × List String) := #[(init, [])]
  let mut n := 0
  while !frontier.isEmpty do
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

def report (name : String) (r : Option (List String) × Nat) : IO Unit := do
  match r with
  | (some tr, n) =>
      IO.println s!"✗ {name}: COUNTEREXAMPLE ({n} states explored)"
      for l in tr do IO.println s!"    {l}"
  | (none, n) => IO.println s!"✓ {name}: no counterexample ({n} states, exhaustive)"

def run (title : String) (c : Cfg) (calls : Nat) : IO Unit := do
  IO.println s!"--- {title}: hooks=2 x {calls} calls, windows≤{c.windows + 1}, restarts≤{c.maxRestarts}, agy={c.agy} zeroAgy={c.zeroAgy} atomicNote={c.atomicNote} splitRead={c.splitRead} saveOk={c.saveOk}"
  let i : S := { hooks := [{ left := calls }, { left := calls }] }
  report "B1 each level said at most once per window (across restarts)" (bfs c (fun s => !B1 s) i)
  report "B2 no notice about a window that has already reset" (bfs c (fun s => !B2 s) i)
  report "sanity (expect ✗): both levels said in two windows"
    (bfs c (fun s => s.said.length ≥ 4) i)

def main : IO Unit := do
  run "Claude Code / Codex (reading zeroed after a reset)" {} 3
  run "Antigravity cache, as shipped" { agy := true } 3
  run "Antigravity cache, zeroAgy fix" { agy := true, zeroAgy := true } 3
  run "note split into check, then record (no lock)" { atomicNote := false } 3
  run "read and note as separate steps (handlers interleaving; not the code)" { splitRead := true } 3
  run "save_sessions fails" { saveOk := false } 3
