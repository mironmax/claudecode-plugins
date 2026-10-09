import Std
/-!
Model of the visual editor's live-update subscription (F6): one editor page,
its WebSocket connection, the server's broadcast tasks and the REST graph load.
Code (after the fix; paths under knowledge-graph/):
  server/mcp_http/store.py:706-784   put_node under store.lock, then _broadcast
  server/mcp_http/store.py:326-355   _broadcast tags the project root, create_task
  server/mcp_http/websocket.py:64-97 broadcast_to_project; :94 the per-send check
  server/mcp_http/websocket.py:22-43 connect/disconnect clear, subscribe binds
  server/mcp_http/rest.py:568-595    _ws_subscribe: bind, then reply {"subscribed", sub}
  server/mcp_http/store.py:388       read_graphs snapshot under store.lock
  visual-editor/frontend/static/js/app.js
     :162 onopen (subscribe), :179 onclose (load if awaiting), :198 subscribeCurrent,
     :215 loadSelectedGraph, :233 reply handler (load), :256 change filter,
     :1810 loadGraph (:1825 a stale response is dropped)

Atomic steps: a write (store.lock); a broadcast task's check and its send
(one step with the per-send check, two without it); the server handling one
subscribe (bind + reply, no await between); a REST snapshot; the page
handling one message, a selection, a reconnect, a render. Both WebSocket
directions are FIFO. A connection drop is also a server restart: the new
connection starts unsubscribed, queued messages and broadcast tasks are lost.

Designs of the page (`design`):
  0  code before the fix: never subscribes; loads on selection only
  1  naive: subscribes on open and on selection, loads immediately on
     selection, applies every project-level change while a project is shown
  2  the fix: as 1, but with a server that has answered a subscription on this
     connection (`live`) the selection loads when the reply to its subscription
     arrives; any reply to the latest request loads; a project change applies
     only if it names the project the latest reply confirmed; on close, a
     selection still waiting for its reply loads at once (`closeLoad`)
Server flags: `recheck` (subscription checked right before each send, the
fix; otherwise decided once, sent after an await), `newSrv0` (the server
understands subscribe; an older one ignores it and never replies),
`downgrade` (a restart may bring up an older server).

Properties:
  W  the page never applies a change for a project other than the one shown
  R  the server never sends a project change to a connection not subscribed
     to that project
  L  no lost update: once everything is delivered, a page that shows "Live"
     for the selected project shows its latest version
  F  fallback: once everything is delivered, the page shows the selected graph
-/

inductive Msg where
  | ack (seq : Nat) (p : Option Nat)   -- {"type": "subscribed", "sub", "project_path"}
  | upd (p : Nat)                      -- project-level change, tagged with its root
  deriving DecidableEq, Repr, Hashable, BEq

structure S where
  ver : List Nat                   -- per project: writes committed
  writes : Nat
  newSrv : Bool
  sub : Option Nat                 -- ConnectionManager.subscriptions[this connection]
  pend : List (Nat × Bool)         -- broadcast tasks for this connection: (project, decided)
  toS : List (Nat × Option Nat)    -- page -> server: subscribe(seq, project)
  toP : List Msg                   -- server -> page
  conn : Bool
  sel : Option Nat                 -- page: selected project
  seq : Nat
  acked : Bool
  root : Option Nat
  live : Bool
  awaiting : Bool
  fetch : Option (Nat × Option Nat) -- latest loadGraph: project, snapshot once taken
  view : Option (Nat × Nat)        -- rendered: project, version
  switches : Nat
  drops : Nat
  violW : Bool
  violR : Bool
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  design : Nat
  recheck : Bool
  closeLoad : Bool
  newSrv0 : Bool
  downgrade : Bool

def maxW := 2
def maxSw := 3
def maxDrop := 1
def projects := [0, 1]

def dropAt {α} (l : List α) (i : Nat) : List α := l.take i ++ l.drop (i + 1)

/-- Page sends subscribe for the current selection (subscribeCurrent). -/
def subscribe (c : Cfg) (s : S) : S :=
  if c.design == 0 then s else
  let s := { s with seq := s.seq + 1, acked := false, root := none }
  if s.conn then { s with toS := s.toS ++ [(s.seq, s.sel)] } else s

def load (s : S) : S := match s.sel with
  | some p => { s with fetch := some (p, none) }
  | none => s

def next (c : Cfg) (s : S) : List (String × S) :=
  let writes := if s.writes < maxW then projects.map fun p =>
      (s!"write project {p} (v{s.ver[p]! + 1})",
       { s with ver := s.ver.set p (s.ver[p]! + 1), writes := s.writes + 1,
                pend := if s.conn then s.pend ++ [(p, false)] else s.pend })
    else []
  let bcast := (List.range s.pend.length).flatMap fun i =>
    let (p, decided) := s.pend[i]!
    let want := s.sub == some p
    if c.recheck then
      [(s!"broadcast of project {p}: check + send" ++ (if want then "" else " (skipped)"),
        { s with pend := dropAt s.pend i, toP := if want then s.toP ++ [Msg.upd p] else s.toP })]
    else if !decided then
      [(s!"broadcast of project {p}: decides " ++ (if want then "to send" else "to skip"),
        { s with pend := if want then s.pend.set i (p, true) else dropAt s.pend i })]
    else
      [(s!"broadcast of project {p}: sends (after an await)",
        { s with pend := dropAt s.pend i, toP := s.toP ++ [Msg.upd p],
                 violR := s.violR || !want })]
  let srvRecv := match s.toS with
    | (q, p) :: rest =>
      if s.newSrv then [(s!"server binds subscribe #{q} to {repr p}, replies",
                         { s with toS := rest, sub := p, toP := s.toP ++ [Msg.ack q p] })]
      else [(s!"older server ignores subscribe #{q}", { s with toS := rest })]
    | [] => []
  let pageRecv := match s.toP with
    | Msg.ack q p :: rest =>
      let s := { s with toP := rest }
      if q != s.seq then [(s!"page ignores reply #{q} (latest is #{s.seq})", s)] else
      let s := { s with acked := true, root := p, live := true }
      if c.design == 2 then [(s!"page gets reply #{q}, loads", load { s with awaiting := false })]
      else [(s!"page gets reply #{q}", s)]
    | Msg.upd p :: rest =>
      let s := { s with toP := rest }
      let applies := if c.design == 2 then s.acked && s.root == some p else s.sel.isSome
      if applies then
        [(s!"page applies change of project {p} (showing {repr s.sel}), reloads",
          load { s with violW := s.violW || s.sel != some p })]
      else [(s!"page drops change of project {p}", s)]
    | [] => []
  let snap := match s.fetch with
    | some (p, none) => [(s!"REST snapshot of project {p} at v{s.ver[p]!}",
                          { s with fetch := some (p, some s.ver[p]!) })]
    | _ => []
  let render := match s.fetch with
    | some (p, some v) =>
      [(if s.sel == some p then s!"page renders project {p} v{v}" else "page drops a stale load",
        { s with fetch := none, view := if s.sel == some p then some (p, v) else s.view })]
    | _ => []
  let select := if s.switches < maxSw then
      (projects.filter (fun q => s.sel != some q)).map fun q =>
        let s0 := subscribe c { s with sel := some q, view := none, switches := s.switches + 1 }
        let s1 := if c.design == 2 && s0.live && s0.conn then { s0 with awaiting := true }
                  else load { s0 with awaiting := false }
        (s!"page selects project {q}" ++ (if s1.awaiting then " (waits for the reply)" else ", loads"), s1)
    else []
  let drop := if s.conn && s.drops < maxDrop then
      let s0 := { s with conn := false, toS := [], toP := [], pend := [], sub := none,
                         live := false, acked := false, drops := s.drops + 1 }
      let s1 := if c.design == 2 && c.closeLoad && s0.awaiting then load { s0 with awaiting := false } else s0
      [("connection drops / server restarts", s1)] ++
      (if c.downgrade then [("server restarts as an OLDER server", { s1 with newSrv := false })] else [])
    else []
  let connect := if !s.conn then
      [("page (re)connects, subscribes", subscribe c { s with conn := true, live := false })]
    else []
  writes ++ bcast ++ srvRecv ++ pageRecv ++ snap ++ render ++ select ++ drop ++ connect

/-- Everything delivered. A closed socket is not settled: the page reconnects
every 5 s (app.js onclose). -/
def quiet (s : S) : Bool :=
  s.conn && s.toS.isEmpty && s.toP.isEmpty && s.pend.isEmpty && s.fetch.isNone

/-- The status shows "Live" for the selected project. Before the fix the page
showed "Live" whenever its socket was open. -/
def pageLive (c : Cfg) (s : S) : Bool :=
  s.conn && (c.design == 0 || (s.acked && s.root == s.sel))

def lost (c : Cfg) (s : S) : Bool :=
  quiet s && pageLive c s && match s.sel, s.view with
    | some p, some (q, v) => p == q && v < s.ver[p]!
    | _, _ => false

def stuck (s : S) : Bool :=
  quiet s && match s.sel with
    | some p => (s.view.map (·.1)) != some p
    | none => false

def sanity (c : Cfg) (s : S) : Bool :=
  s.switches ≥ 2 && s.drops ≥ 1 && pageLive c s && match s.sel, s.view with
    | some p, some (q, v) => p == q && v ≥ 1 && v == s.ver[p]!
    | _, _ => false

partial def bfs (c : Cfg) (bad : S → Bool) (init : S) : Option (List String) × Nat := Id.run do
  let mut seen : Std.HashSet S := ({} : Std.HashSet S).insert init
  let mut frontier : Array (S × List String) := #[(init, [])]
  let mut n := 0
  while !frontier.isEmpty do
    let mut nf := #[]
    for (s, tr) in frontier do
      n := n + 1
      if bad s then return (some tr.reverse, n)
      for (l, s') in next c s do
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

def init (c : Cfg) : S :=
  { ver := [0, 0], writes := 0, newSrv := c.newSrv0, sub := none, pend := [], toS := [], toP := [],
    conn := false, sel := none, seq := 0, acked := false, root := none, live := false,
    awaiting := false, fetch := none, view := none, switches := 0, drops := 0,
    violW := false, violR := false }

def all (c : Cfg) (withSanity : Bool := false) : IO Unit := do
  report "W never applies another project's change" (bfs c (·.violW) (init c))
  report "R never sent another project's change" (bfs c (·.violR) (init c))
  report "L no lost update while Live" (bfs c (lost c) (init c))
  report "F the selected graph is shown" (bfs c stuck (init c))
  if withSanity then
    report "sanity (expect ✗): Live, latest version, after 2 selections and a restart"
      (bfs c (sanity c) (init c))

def main : IO Unit := do
  IO.println s!"Bounds: 2 projects, ≤{maxW} writes, ≤{maxSw} selections, ≤{maxDrop} drop/restart, one page."
  IO.println "=== design 0: code before the fix (no subscription) ==="
  all { design := 0, recheck := false, closeLoad := false, newSrv0 := false, downgrade := false }
  IO.println "--- design 0 (an older page) against the fixed server"
  all { design := 0, recheck := true, closeLoad := false, newSrv0 := true, downgrade := false }
  IO.println "=== design 1: naive subscription (load at once, apply every project change) ==="
  all { design := 1, recheck := true, closeLoad := false, newSrv0 := true, downgrade := false }
  IO.println "=== design 2: the fix (load on the reply, filter by confirmed project) ==="
  IO.println "--- server decides once per broadcast (no per-send check)"
  all { design := 2, recheck := false, closeLoad := true, newSrv0 := true, downgrade := true }
  IO.println "--- page without the load-on-close, a restart may bring up an older server"
  all { design := 2, recheck := true, closeLoad := false, newSrv0 := true, downgrade := true }
  IO.println "--- the fix as shipped: per-send check, load-on-close, restart may downgrade"
  all { design := 2, recheck := true, closeLoad := true, newSrv0 := true, downgrade := true } true
  IO.println "--- the fixed page against an older server (L is vacuous: never Live)"
  all { design := 2, recheck := true, closeLoad := true, newSrv0 := false, downgrade := false }
