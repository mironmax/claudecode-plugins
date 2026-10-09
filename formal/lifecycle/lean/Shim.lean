import Std
/-!
Model of one JSON-RPC write through `kg mcp` (knowledge-graph/cli/mcp_shim.py)
to the HTTP server, across a restart or a crash.

The shim opens one connection per message and POSTs it (mcp_shim.py:56-68).
It retries once, and only on a refused connection (:96-105), after
ensure_server has waited for the server or started it (:46-53). Any other
failure (reset, disconnect) is answered with a JSON-RPC error (:85-91).

Server side (uvicorn under mcp_streamable_server.py:984-1009): a graceful stop
(SIGTERM from kg stop / restart / systemctl) closes the listener first. A
connection still in the accept backlog is then reset unread. Requests already
being handled run to the end and get their reply. A crash (or kg stop's
SIGKILL after 6 s, kg.py:246-249) drops everything in flight, replied or not.

Variants:
  retryAll     — the shim retries any connection error, not just a refused one;
  harnessRetry — the agent calls the tool again after an error reply.
-/

structure S where
  srv : Nat        -- 0 up, 1 closing (listener closed, in-flight requests finish), 2 down
  phase : Nat      -- 0 to send, 1 in the accept backlog, 2 being handled, 3 applied (reply pending),
                   -- 4 reply delivered, 5 error delivered
  attempt : Nat    -- shim attempts for this call
  calls : Nat      -- calls the harness made
  applied : Nat    -- times the server applied the write
  restarts : Nat
  crashes : Nat
  deriving DecidableEq, Repr, Hashable, BEq

structure Cfg where
  retryAll : Bool := false
  harnessRetry : Bool := false
  maxCrash : Nat := 0
  maxRestart : Nat := 2

def fail (c : Cfg) (s : S) (why : String) : String × S :=
  if c.retryAll && s.attempt == 0 then (s!"{why}: shim retries", { s with phase := 0, attempt := 1 })
  else (s!"{why}: shim answers with an error", { s with phase := 5 })

def next (c : Cfg) (s : S) : List (String × S) :=
  let shim : List (String × S) := match s.phase with
    | 0 => if s.srv == 0 then [("shim connects and sends the POST", { s with phase := 1 })]
           else if s.attempt == 0 then [("connection refused: ensure_server waits for /health, or starts it", { s with attempt := 1 })]
           -- ensure_server returns once /health answers; it gives up only if no start succeeds
           else if s.srv == 2 && s.restarts ≥ c.maxRestart then [("no server comes back: shim answers 'server unreachable'", { s with phase := 5 })]
           else []
    | 1 => if s.srv == 0 then [("server accepts and reads the request", { s with phase := 2 })]
           else [fail c s "listener closed with the connection in its backlog: reset"]
    | 2 => if s.srv != 2 then [("handler applies the write", { s with phase := 3, applied := s.applied + 1 })] else []
    | 3 => if s.srv != 2 then [("reply reaches the harness", { s with phase := 4 })] else []
    | 5 => if c.harnessRetry && s.calls < 2 then
             [("agent calls the tool again", { s with phase := 0, attempt := 0, calls := s.calls + 1 })] else []
    | _ => []
  let server : List (String × S) :=
    (if s.srv == 0 then [("SIGTERM: listener closes (graceful)", { s with srv := 1 })] else []) ++
    (if s.srv == 1 && !(s.phase == 2 || s.phase == 3) then [("graceful stop completes", { s with srv := 2 })] else []) ++
    (if s.srv == 2 && s.restarts < c.maxRestart then [("server (re)starts", { s with srv := 0, restarts := s.restarts + 1 })] else []) ++
    (if s.srv != 2 && s.crashes < c.maxCrash then
      let s1 := { s with srv := 2, crashes := s.crashes + 1 }
      if [1, 2, 3].contains s.phase then [fail c s1 "server crashes mid-request: reset"]
      else [("server crashes", s1)]
     else [])
  shim ++ server

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

def init : S := { srv := 0, phase := 0, attempt := 0, calls := 1, applied := 0, restarts := 0, crashes := 0 }

def runAll (c : Cfg) : IO Unit := do
  IO.println s!"--- retryAll={c.retryAll} harnessRetry={c.harnessRetry} crashes≤{c.maxCrash} restarts≤{c.maxRestart}"
  report "M1 a write is applied at most once" (bfs c (fun s => s.applied > 1) init)
  report "M2 an error reply means the write was not applied" (bfs c (fun s => s.phase == 5 && s.applied > 0) init)
  report "sanity (expect ✗): refused during a restart, the retry lands the write" (bfs c (fun s => s.phase == 4 && s.attempt == 1) init)
  report "M3 (expect ✗) a restart alone never costs an error reply" (bfs c (fun s => s.phase == 5 && s.crashes == 0) init)

def main : IO Unit := do
  runAll {}                                          -- today, graceful restarts only
  runAll { maxCrash := 1 }                           -- today, a crash too
  runAll { maxCrash := 1, harnessRetry := true }     -- the agent retries after an error
  runAll { retryAll := true }                        -- if the shim retried resets too
  runAll { retryAll := true, maxCrash := 1 }
