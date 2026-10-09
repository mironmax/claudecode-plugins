import Std
/-!
The saver's per-graph tick on an idle graph: compaction, refill, rebalance and
the orphan pass, as `MultiProjectGraphStore._maybe_compact` sequences them
(mcp_http/store.py:1905-1911), then `_prune_orphans` (:2005-2035, a 365-day
grace, so it never acts here). Line numbers at 1d03171.

The tick is deterministic, so the search is an enumeration of graphs, not of
interleavings: every graph within the bounds below, from every initial
archived set, is ticked until its state (archived and orphaned sets) repeats.
That trajectory is complete: an idle tick is a function of that state alone
(the scores are percentile ranks; the clock moves every recency and every
decayed endorsement alike, so it never reorders them). A repeated state with
a pass still acting is a cycle that runs forever.

Faithful parts, each citing the code it mirrors:
- size: `CharEstimator.estimate_graph` (core/estimator.py:62), as a closed form
  for ids n0..n3 and rel "rel": "ACTIVE:" 8 when any node is active, gist+7 per
  active line, the ARCHIVED header 53 when any is archived, 5 per anchor, 15 per
  live edge (core/utils.py:103: no orphaned end, at least one active end).
  `compaction/repro/repro_tick.py` checks this form against the real estimator;
- scores: `NodeScorer.score_breakdown` (core/scorer.py:177-255) in IEEE doubles
  with Python's operation order: tie-averaged percentiles of recency,
  connectedness (`_connectedness_details`, :42-90: an active neighbour 1.0, an
  archived one 0.2, `max(0.66 in + 0.33 out, 0.5 log(1 + degree))`) and
  endorsements, weighted 0.25 / 0.40 / 0.35 (core/constants.py:243-245);
- the fresh tier: `fresh_ids` (scorer.py:126-169), budget int(0.3 max);
- the four passes (core/compactor.py, cited per definition) with Python's
  stable sorts and first-wins min/max. The orphan pass cannot act within these
  bounds (it needs anchors, 5 characters each, over 0.3·max); it only adds
  orphans and nothing on an idle tick removes one, so it takes no part in a
  cycle. compaction/repro/tick_search.py covers larger graphs.
Bounds (6,477 graphs, 35,976 trajectories per variant): 2 nodes with gists of
20, 80 or 200 characters, recency ascending with the id or all equal, creation
order ascending or descending, no endorsement or one on either node; 3 nodes
with gists of 20 or 200, recency and creation ascending, no endorsement. Any
set of directed edges. Budgets max = ⌊k·T/10⌋ with T the all-active size,
k = 4..13 for 2 nodes and 5, 7, 9, 11, 13 for 3, leaving out a budget below
the all-archived size (no compaction can meet it). Every initial archived set.

Properties (the checks of compaction/repro/tick_search.py, on the model):
  C1  a node archived on tick t (by compaction or a rebalance swap) and still
      archived when t ends is promoted on tick t+1, labelled by the two passes;
  F   a node active, then archived, then active again (C1 as states);
  R   one tick's rebalance swaps a node out and back in;
  C2  a cycle: the state repeats while passes still act, so it runs forever,
      with a write-through every tick; "in one tick" when each tick ends where
      it began, "across ticks" when the active set itself alternates;
  B1  refill ends above the fill ceiling;  B2 rebalance ends above max;
  B3  refill stops while one more archived node would fit under the ceiling
      ("B3 last" when that node is the last archived one);
  B4  compaction archives yet leaves the graph above max with an active node
      outside the fresh tier (the fresh tier is never archived: a graph whose
      rest is fresh stays over budget by design, compactor.py:50-69);
  B5  compaction acts right after a refill (refill set off an archive);
  B6  where the graph settles, a node stays archived although the graph with
      every node active is under the fill ceiling.

Variants: `code` is the code at 1d03171. `A` lets refill run on a tick that
compacted (store.py:1909 skipped it; the F7 fix). `B` makes refill promote the
whole archive first when the graph with every node active fits under the
ceiling (the F34 fix). `post` and `pot` are two candidate rules for rebalance
(F33), not adopted: `post` re-scores a tentative swap and keeps it only if
the promoted node still wins by the margin afterwards; `pot` keeps a swap only
if it raises the summed score of the active nodes, scored after the swap, by
the margin. Runs in about 1.5 minutes.
-/

-- core/constants.py
def TARGET : Float := 0.8          -- COMPACTION_TARGET_RATIO, :56
def ARCH_RATIO : Float := 0.30     -- ARCHIVED_BUDGET_RATIO, :203
def MARGIN : Float := 0.05         -- RESURRECTION_MARGIN, :205
def MAX_SWAPS := 3                 -- REBALANCE_MAX_SWAPS, :208
def FRESH_RATIO : Float := 0.30    -- FRESH_BUDGET_RATIO, :465
def W_REC : Float := 0.25  -- :243-245
def W_CON : Float := 0.40
def W_USE : Float := 0.35
def ARCH_EDGE_W : Float := 0.2     -- ARCHIVED_EDGE_WEIGHT, :256
def HUB_W : Float := 0.5           -- HUB_FLOOR_WEIGHT, :264

-- rendered sizes for ids n0..n3 and rel "rel" (core/render.py)
def LINE (g : Nat) : Nat := g + 7
def ANCHOR := 5
def EDGE := 15
def ACTIVE_HDR := 8
def ARCH_HDR := 53

/-- Python's int(m * r) for m ≥ 0. -/
def ratio (m : Nat) (r : Float) : Nat := (m.toFloat * r).floor.toUInt64.toNat

structure Cfg where
  n : Nat
  gist : Array Nat
  edges : Array (Nat × Nat)   -- directed, in insertion order
  read : Array Nat            -- _last_read_ts (recency raw; versions empty)
  use : Array Nat             -- number of _useful_ts stamps, all at one time
  created : Array Nat         -- _created_ts
  M : Nat                     -- max_chars

structure St where
  arch : Nat   -- bitmask: "_archived" set
  orph : Nat   -- bitmask: "_orphaned_ts" set
  deriving BEq, Hashable, Repr, Inhabited

def bit (m i : Nat) : Bool := (m >>> i) % 2 == 1
def isOrph (s : St) (i : Nat) : Bool := bit s.orph i
def isArch (s : St) (i : Nat) : Bool := bit s.arch i && !bit s.orph i
def isAct (s : St) (i : Nat) : Bool := !bit s.arch i && !bit s.orph i
def setArch (s : St) (i : Nat) (b : Bool) : St :=
  let m := s.arch - (if bit s.arch i then 1 <<< i else 0)
  { s with arch := if b then m + (1 <<< i) else m }

/-- Stable insertion sort: `lt a b` puts a before b; equal keys keep list order
(Python's sorted, also with reverse=True). -/
def insertBy (lt : Nat → Nat → Bool) (x : Nat) : List Nat → List Nat
  | [] => [x]
  | y :: ys => if lt x y then x :: y :: ys else y :: insertBy lt x ys
def sortBy (lt : Nat → Nat → Bool) (l : List Nat) : List Nat :=
  l.foldl (fun acc x => insertBy lt x acc) []

def ids (c : Cfg) : List Nat := List.range c.n

/-- estimate_graph(nodes, edges) (estimator.py:62). -/
def size (c : Cfg) (s : St) : Nat := Id.run do
  let act := (ids c).filter (isAct s)
  let arc := (ids c).filter (isArch s)
  let mut t := (if act.isEmpty then 0 else ACTIVE_HDR) + (if arc.isEmpty then 0 else ARCH_HDR)
               + ANCHOR * arc.length
  for i in act do t := t + LINE c.gist[i]!
  for (f, to) in c.edges do
    if !isOrph s f && !isOrph s to && (isAct s f || isAct s to) then t := t + EDGE
  return t

/-- fresh_ids (scorer.py:142-169): newest first while line + new citations fit. -/
def freshIds (c : Cfg) (s : St) : List Nat := Id.run do
  let F := ratio c.M FRESH_RATIO
  if F == 0 then return []
  let cands := sortBy (fun a b => c.created[a]! > c.created[b]!) ((ids c).filter (!isOrph s ·))
  let mut used := 0
  let mut fresh : List Nat := []
  let mut charged : List Nat := []
  for i in cands do
    let mut cost := LINE c.gist[i]!
    let mut newE : List Nat := []
    for k in [0:c.edges.size] do
      let (f, t) := c.edges[k]!
      if f == i || t == i then
        let other := if f == i then t else f
        if !charged.contains k && !isOrph s other then
          newE := k :: newE
          cost := cost + EDGE
    if used + cost > F then break
    fresh := i :: fresh
    used := used + cost
    charged := newE ++ charged
  return fresh

/-- Tie-aware percentiles (scorer.py:221-242) over `elig`; `key` is indexed by node. -/
def pct (elig : List Nat) (key : Array Float) (n : Nat) : Array Float := Id.run do
  let mut out := Array.replicate n 0.0
  let srt := (sortBy (fun a b => key[a]! < key[b]!) elig).toArray
  let m := srt.size
  if m == 1 then return out.set! srt[0]! 0.5
  let mut i := 0
  while i < m do
    let mut j := i
    while j + 1 < m && key[srt[j+1]!]! == key[srt[i]!]! do j := j + 1
    let avg := (i + j).toFloat / 2.0
    for k in [i:j+1] do out := out.set! srt[k]! (avg / (m - 1).toFloat)
    i := j + 1
  return out

/-- score_all (scorer.py:171-255); none = not scored (orphaned, fresh, or archived
when `incl` is false). -/
def scoresRaw (c : Cfg) (s : St) (fresh : List Nat) (incl : Bool) : Array (Option Float) := Id.run do
  let elig := (ids c).filter fun i => !isOrph s i && (incl || !bit s.arch i) && !fresh.contains i
  if elig.isEmpty then return Array.replicate c.n none
  let w (j : Nat) : Float := if isAct s j then 1.0 else if isArch s j then ARCH_EDGE_W else 0.0
  let conn (i : Nat) : Float := Id.run do
    let mut inD : Float := 0.0
    let mut outD : Float := 0.0
    let mut deg := 0
    for (f, t) in c.edges do
      if t == i then
        inD := inD + w f
        deg := deg + 1
      if f == i then
        outD := outD + w t
        deg := deg + 1
    let hub := HUB_W * Float.log (1.0 + deg.toFloat)
    let wd := 0.66 * inD + 0.33 * outD
    return if hub > wd then hub else wd
  let rp := pct elig (c.read.map (·.toFloat)) c.n
  let cp := pct elig ((ids c).toArray.map conn) c.n
  let up := pct elig (c.use.map (·.toFloat)) c.n
  let mut out := Array.replicate c.n none
  for i in elig do out := out.set! i (some (W_REC * rp[i]! + W_CON * cp[i]! + W_USE * up[i]!))
  return out

def get (sc : Array (Option Float)) (i : Nat) : Float := (sc[i]!).getD 0.0

/-- _candidate_scores (compactor.py:253-257): unified pool, fresh ranked first. -/
def candRaw (c : Cfg) (fresh : List Nat) (all : Array (Option Float)) : Array (Option Float) := Id.run do
  let mut sc := all
  for i in fresh do sc := sc.set! i (some (2.0 + c.created[i]!.toFloat / 1e12))
  return sc

/-- Everything a pass reads from a state, computed once per state of the graph
(the passes call the scorer many times over at most 3^n states). -/
structure Ctx where
  c : Cfg
  sz : Array Nat
  fr : Array (List Nat)
  sAct : Array (Array (Option Float))   -- score_all(include_archived=False)
  sAll : Array (Array (Option Float))   -- score_all(include_archived=True)
  cand : Array (Array (Option Float))   -- _candidate_scores

def code (c : Cfg) (s : St) : Nat := s.arch + (s.orph <<< c.n)

def Ctx.mk' (c : Cfg) : Ctx := Id.run do
  let mut x : Ctx := { c, sz := #[], fr := #[], sAct := #[], sAll := #[], cand := #[] }
  for k in [0:4 ^ c.n] do
    let s : St := { arch := k % 2 ^ c.n, orph := k >>> c.n }
    let f := freshIds c s
    let all := scoresRaw c s f true
    x := { x with sz := x.sz.push (size c s), fr := x.fr.push f, sAct := x.sAct.push (scoresRaw c s f false),
                  sAll := x.sAll.push all, cand := x.cand.push (candRaw c f all) }
  return x

def Ctx.size (x : Ctx) (s : St) : Nat := x.sz[code x.c s]!
def Ctx.fresh (x : Ctx) (s : St) : List Nat := x.fr[code x.c s]!
def Ctx.scores (x : Ctx) (s : St) (incl : Bool) : Array (Option Float) :=
  if incl then x.sAll[code x.c s]! else x.sAct[code x.c s]!
def Ctx.candScores (x : Ctx) (s : St) : Array (Option Float) := x.cand[code x.c s]!

/-- compact_if_needed (compactor.py:33-132). Returns its list (archived_this_pass,
resurrected nodes included, as the code returns it) and the resurrected ones. -/
def compact (x : Ctx) (s0 : St) : St × List Nat × List Nat := Id.run do
  let mut s := s0
  let mut est := x.size s
  if est ≤ x.c.M then return (s, [], [])  -- :46
  let sa := x.scores s false  -- :53
  let elig := (ids x.c).filter fun i => (sa[i]!).isSome
  if elig.isEmpty then return (s, [], [])  -- :55 stall
  let target := ratio x.c.M TARGET  -- :75
  let mut arch : List Nat := []
  for i in sortBy (fun a b => get sa a < get sa b) elig do  -- :74, :78-89
    if est ≤ target then break
    if !bit s.arch i then
      s := setArch s i true
      est := x.size s
      arch := arch ++ [i]
  let mut res : List Nat := []
  if !arch.isEmpty then  -- :93-129
    let u := x.scores s true
    for j in arch do
      let aj := get u j
      let mut best : Option Nat := none
      let mut bestSc : Float := -1.0
      for k in ids x.c do
        if k != j then
          match u[k]! with
          | some v => if isArch s k && v > bestSc then
                        best := some k
                        bestSc := v
          | none => pure ()
      match best with
      | some b =>
          if bestSc - aj ≥ MARGIN then
            let s' := setArch s b false
            if x.size s' ≤ x.c.M then  -- :118
              s := s'
              res := res ++ [b]
      | none => pure ()
  return (s, arch, res)

/-- refill_if_room (compactor.py:134-251). `whole`: the B variant, which first
promotes the whole archive when the graph with every node active fits. -/
def refill (x : Ctx) (whole : Bool) (s0 : St) : St × List Nat := Id.run do
  let ceil := ratio x.c.M TARGET  -- :172
  let mut s := s0
  let mut est := x.size s
  let arc := (ids x.c).filter (isArch s)
  if whole && !arc.isEmpty then
    let sw := arc.foldl (fun t i => setArch t i false) s
    if x.size sw ≤ ceil then return (sw, arc)
  if est ≥ ceil then return (s, [])  -- :173
  let mut promoted : List Nat := []
  let mut tooBig : List Nat := []
  for _ in [0:x.c.n + 1] do  -- :184
    let cands := (ids x.c).filter fun i => isArch s i && !tooBig.contains i
    if cands.isEmpty then break
    let sc := x.candScores s  -- :196
    let ranked := sortBy (fun a b => get sc a > get sc b) cands  -- :197
    let mut nl := Array.replicate x.c.n 0  -- :205-216
    for (f, t) in x.c.edges do
      if isArch s f && isArch s t then
        nl := nl.set! f (nl[f]! + EDGE)
        if t != f then nl := nl.set! t (nl[t]! + EDGE)
    let mut got := false
    for i in ranked do  -- :219-244
      let delta := LINE x.c.gist[i]! - ANCHOR + nl[i]!
      if est + delta > ceil then
        tooBig := i :: tooBig
        continue
      let s' := setArch s i false
      let e' := x.size s'
      if e' > ceil then
        tooBig := i :: tooBig
        continue
      s := s'
      est := e'
      promoted := promoted ++ [i]
      got := true
      break
    if !got then break
  return (s, promoted)

inductive Rb where | code | post | pot deriving BEq, Inhabited

/-- Summed score of the active scored nodes (the `pot` variant's measure). -/
def phi (x : Ctx) (s : St) : Float := Id.run do
  let sc := x.candScores s
  let mut t : Float := 0.0
  for i in ids x.c do
    if isAct s i then t := t + get sc i
  return t

/-- rebalance (compactor.py:259-305), with the variants' extra acceptance test. -/
def rebalance (x : Ctx) (rb : Rb) (s0 : St) : St × List (Nat × Nat) := Id.run do
  let mut s := s0
  let mut swaps : List (Nat × Nat) := []
  for _ in [0:MAX_SWAPS] do
    let sc := x.candScores s  -- :277
    let fresh := x.fresh s
    let act := (ids x.c).filter fun i => isAct s i && !fresh.contains i && (sc[i]!).isSome
    let arc := (ids x.c).filter fun i => isArch s i && (sc[i]!).isSome
    if act.isEmpty || arc.isEmpty then break  -- :284
    let worst := act.foldl (fun w i => if get sc i < get sc w then i else w) act.head!  -- :286
    let mut swapped := false
    for best in sortBy (fun a b => get sc a > get sc b) arc do  -- :288
      if get sc best - get sc worst < MARGIN then break  -- :289
      let s' := setArch (setArch s worst true) best false
      if x.size s' > x.c.M then continue  -- :293
      if rb == .post then
        let sc' := x.candScores s'
        if get sc' best - get sc' worst < MARGIN then continue
      if rb == .pot then
        if phi x s' < phi x s + MARGIN then continue
      s := s'
      swaps := swaps ++ [(best, worst)]
      swapped := true
      break
    if !swapped then break
  return (s, swaps)

/-- orphan_archived_if_needed (compactor.py:307-361). -/
def orphan (x : Ctx) (s0 : St) : St × List Nat := Id.run do
  let arc := (ids x.c).filter (isArch s0)
  if arc.isEmpty then return (s0, [])
  let budget := ratio x.c.M ARCH_RATIO
  let mut chars := ANCHOR * arc.length
  if chars ≤ budget then return (s0, [])
  let sc := x.scores s0 true
  let inf : Float := 1.0 / 0.0
  let key (i : Nat) : Float := (sc[i]!).getD inf
  let mut s := s0
  let mut out : List Nat := []
  for i in sortBy (fun a b => key a < key b) arc do
    if chars ≤ budget then break
    s := { s with orph := s.orph + (if bit s.orph i then 0 else 1 <<< i) }
    chars := chars - ANCHOR
    out := out ++ [i]
  return (s, out)

structure Variant where
  name : String
  sameTick : Bool   -- refill may run on a tick that compacted
  whole : Bool      -- refill promotes the whole archive when it all fits
  rb : Rb
  deriving Inhabited

structure Ev where
  arch : List Nat := []           -- compact_if_needed's return
  res : List Nat := []            -- of which resurrected
  refl : List Nat := []
  swaps : List (Nat × Nat) := []
  orph : List Nat := []
  flags : List String := []
  size : Nat := 0
  deriving Inhabited

def Ev.acted (e : Ev) : Bool :=
  !e.arch.isEmpty || !e.refl.isEmpty || !e.swaps.isEmpty || !e.orph.isEmpty

/-- One saver tick on one graph: store.py:1905-1911 (then _prune_orphans, a no-op
within its 365-day grace). `last` is the last pass that acted (for B5). -/
def tick (x : Ctx) (v : Variant) (s : St) (last : String) : St × Ev × String := Id.run do
  let ceil := ratio x.c.M TARGET
  let mut fl : List String := []
  let mut last := last
  let (s1, a, res) := compact x s
  if !a.isEmpty then
    if x.size s1 > x.c.M && (ids x.c).any (fun i => isAct s1 i && !(x.fresh s1).contains i) then
      fl := "B4" :: fl
    if last == "refill" then fl := "B5" :: fl
    last := "compact"
  let mut s2 := s1
  let mut r : List Nat := []
  if a.isEmpty || v.sameTick then  -- :1909
    let before := x.size s1
    let (sr, pr) := refill x v.whole s1
    s2 := sr
    r := pr
    if !r.isEmpty then
      last := "refill"
      if x.size s2 > ceil then fl := "B1" :: fl
    if before < ceil then
      let left := (ids x.c).filter (isArch s2)
      if left.any (fun i => x.size (setArch s2 i false) ≤ ceil) then
        fl := (if left.length > 1 then "B3" else "B3 last") :: fl
  let mut s3 := s2
  let mut w : List (Nat × Nat) := []
  if a.isEmpty && r.isEmpty then  -- :1910
    let (sw, pw) := rebalance x v.rb s2
    s3 := sw
    w := pw
    if !w.isEmpty then
      last := "rebalance"
      if x.size s3 > x.c.M then fl := "B2" :: fl
  let (s4, o) := orphan x s3  -- :1911
  return (s4, { arch := a, res, refl := r, swaps := w, orph := o, flags := fl, size := x.size s4 }, last)

def nm (i : Nat) : String := s!"n{i}"
def nms (l : List Nat) : String := ", ".intercalate (l.map nm)
def showSet (c : Cfg) (p : Nat → Bool) : String := "{" ++ nms ((ids c).filter p) ++ "}"

def Ev.show (e : Ev) : String := Id.run do
  let mut parts : List String := []
  if !e.arch.isEmpty then
    parts := parts ++ [s!"compact archives [{nms e.arch}]" ++
      (if e.res.isEmpty then "" else s!", resurrects [{nms e.res}]")]
  if !e.refl.isEmpty then parts := parts ++ [s!"refill promotes [{nms e.refl}]"]
  if !e.swaps.isEmpty then
    parts := parts ++ ["rebalance swaps " ++ " ".intercalate (e.swaps.map fun (b, w) => s!"{nm b} in/{nm w} out")]
  if !e.orph.isEmpty then parts := parts ++ [s!"orphan [{nms e.orph}]"]
  return if parts.isEmpty then "nothing acts" else "; ".intercalate parts

/-- Tick from s0 until the state repeats; returns the states, the events
(ev[t] takes st[t] to st[t+1]) and the index the repeat returns to. -/
partial def trajectory (x : Ctx) (v : Variant) (s0 : St) : Array St × Array Ev × Nat := Id.run do
  let mut st := #[s0]
  let mut evs : Array Ev := #[]
  let mut last := ""
  let mut s := s0
  repeat
    let (s', e, l) := tick x v s last
    last := l
    evs := evs.push e
    match st.findIdx? (· == s') with
    | some k =>
        -- the tick after the repeat is ev[k] again, unless it relied on `last` (B5 only)
        st := st.push s'
        return (st, evs, k)
    | none =>
        st := st.push s'
        s := s'
  return (st, evs, 0)

def archAt (c : Cfg) (s : St) : List Nat := (ids c).filter (isArch s)
def actAt (c : Cfg) (s : St) : List Nat := (ids c).filter (isAct s)

/-- The properties a trajectory violates. -/
def violations (x : Ctx) (st : Array St) (evs : Array Ev) (k : Nat) : List String := Id.run do
  let c := x.c
  let mut out : List String := []
  -- where the graph settles (every state of its cycle), a node stays archived
  -- although the graph with every node active is under the fill ceiling
  for t in [k:st.size] do
    let s := st[t]!
    if (ids c).any (isArch s) && x.size { s with arch := s.arch &&& s.orph } ≤ ratio c.M TARGET then
      out := "B6" :: out
  -- the event after the last one is ev[k] (the cycle repeats)
  let nextEv (t : Nat) : Ev := if t + 1 < evs.size then evs[t+1]! else evs[k]!
  for t in [0:evs.size] do
    let e := evs[t]!
    let e2 := nextEv t
    let endArch := archAt c st[t+1]!
    let downC := e.arch.filter endArch.contains
    let downR := (e.swaps.map (·.2)).filter endArch.contains
    let upF := e2.refl
    let upR := e2.swaps.map (·.1)
    if downC.any upF.contains then out := "C1 compact->refill" :: out
    if downC.any upR.contains then out := "C1 compact->rebalance" :: out
    if downR.any upF.contains then out := "C1 rebalance->refill" :: out
    if downR.any upR.contains then out := "C1 rebalance->rebalance" :: out
    if (e.swaps.map (·.2)).any (e.swaps.map (·.1)).contains then out := "R" :: out
    out := e.flags ++ out
  -- a state active, archived, active again (C1 seen as states)
  for t in [0:st.size - 2] do
    if (actAt c st[t]!).any (fun i => (archAt c st[t+1]!).contains i && (actAt c st[t+2]!).contains i) then
      out := "F" :: out
  -- the cycle: states k .. end repeat forever
  let cyc := (List.range (evs.size - k)).map (· + k)
  if cyc.any (fun t => evs[t]!.acted) then
    out := (if evs.size - k == 1 then "C2 cycle in one tick" else "C2 cycle across ticks") :: out
  if out.any (·.startsWith "C1") then out := "C1" :: out
  return out.eraseDups

def showCfg (c : Cfg) (s0 : St) : String :=
  s!"max={c.M} ceiling={ratio c.M TARGET} fresh budget={ratio c.M FRESH_RATIO}; " ++
  "nodes " ++ ", ".intercalate ((ids c).map fun i =>
    s!"{nm i}(gist {c.gist[i]!}, read {c.read[i]!}, created {c.created[i]!}" ++
    (if c.use[i]! > 0 then ", endorsed)" else ")")) ++
  "; edges " ++ (if c.edges.isEmpty then "none" else
    ", ".intercalate (c.edges.toList.map fun (f, t) => s!"{nm f}->{nm t}")) ++
  s!"; start archived {showSet c (isArch s0)}"

def showTrace (c : Cfg) (st : Array St) (evs : Array Ev) (k : Nat) : List String :=
  (List.range evs.size).map fun t =>
    s!"      tick {t}: {evs[t]!.show} -> archived {showSet c (isArch st[t+1]!)}" ++
    (if (ids c).any (isOrph st[t+1]!) then s!" orphaned {showSet c (isOrph st[t+1]!)}" else "") ++
    s!", {evs[t]!.size} chars" ++ (if t + 1 == evs.size then s!" (= state after tick {k - 1})" else "")

-- enumeration
def subsets {α} : List α → List (List α)
  | [] => [[]]
  | x :: xs => let r := subsets xs; r.map (x :: ·) ++ r
def tuples (n : Nat) (vals : List Nat) : List (List Nat) :=
  (List.range n).foldl (fun acc _ => acc.flatMap fun t => vals.map (t ++ [·])) [[]]

/-- One bound: n nodes, the gist lengths each node may take, whether recency
and creation order also take their second form (all equal; descending). -/
structure Scope where
  n : Nat
  gists : List Nat
  more : Bool         -- also: recency all equal, creation descending, an endorsement
  ks : List Nat       -- budgets max = k·T/10

def configs (sc : Scope) : List Cfg := Id.run do
  let n := sc.n
  let pairs := (List.range n).flatMap fun a =>
    (List.range n).filterMap fun b => if a != b then some (a, b) else none
  let asc := (List.range n).map (· + 1)
  let mut out : List Cfg := []
  for g in tuples n sc.gists do
    for es in subsets pairs do
      for rec in (if sc.more then [asc, List.replicate n 1] else [asc]) do
        for u in (if sc.more then [none] ++ (List.range n).map some else [none]) do
          for cr in (if sc.more then [asc, asc.reverse] else [asc]) do
            let base : Cfg := { n, gist := g.toArray, edges := es.toArray, read := rec.toArray,
                                use := ((List.range n).map fun i => if u == some i then 1 else 0).toArray,
                                created := cr.toArray, M := 0 }
            let T := size base { arch := 0, orph := 0 }
            let floor := size base { arch := 2 ^ n - 1, orph := 0 }
            for k in sc.ks do
              -- a budget below the all-archived size cannot be met at all
              if k * T / 10 ≥ floor then out := { base with M := k * T / 10 } :: out
  return out.reverse

def SCOPES : List Scope :=
  [{ n := 2, gists := [20, 80, 200], more := true, ks := List.range' 4 10 },
   { n := 3, gists := [20, 200], more := false, ks := [5, 7, 9, 11, 13] }]

def PROPS : List String :=
  ["C1", "C1 compact->refill", "C1 compact->rebalance", "C1 rebalance->refill",
   "C1 rebalance->rebalance", "F", "R", "C2 cycle in one tick", "C2 cycle across ticks",
   "B1", "B2", "B3", "B3 last", "B4", "B5", "B6"]

def variants : List Variant :=
  [{ name := "code", sameTick := false, whole := false, rb := .code },
   { name := "A", sameTick := true, whole := false, rb := .code },
   { name := "A+B", sameTick := true, whole := true, rb := .code },
   { name := "A+B+post", sameTick := true, whole := true, rb := .post },
   { name := "A+B+pot", sameTick := true, whole := true, rb := .pot }]

structure Tally where
  counts : Std.HashMap String Nat := {}
  first : Std.HashMap String String := {}
  deriving Inhabited

def main : IO Unit := do
  let vs := variants.toArray
  let mut tallies : Array Tally := vs.map fun _ => {}
  let mut total := 0
  let mut graphs := 0
  for sc in SCOPES do
    for c in configs sc do
      graphs := graphs + 1
      let x := Ctx.mk' c
      for m in List.range (2 ^ c.n) do
        let s0 : St := { arch := m, orph := 0 }
        total := total + 1
        for vi in [0:vs.size] do
          let (st, evs, k) := trajectory x vs[vi]! s0
          let mut t := tallies[vi]!
          for p in violations x st evs k do
            t := { t with counts := t.counts.insert p (t.counts.getD p 0 + 1) }
            if !t.first.contains p then
              let tr := "\n".intercalate (("    " ++ showCfg c s0) :: showTrace c st evs k)
              t := { t with first := t.first.insert p tr }
          tallies := tallies.set! vi t
  IO.println s!"{graphs} graphs, {total} trajectories per variant (every start), each ticked to its fixpoint or cycle"
  for vi in [0:vs.size] do
    let t := tallies[vi]!
    IO.println s!"===== variant {vs[vi]!.name}"
    for p in PROPS do
      let n := t.counts.getD p 0
      IO.println s!"{if n == 0 then "✓" else "✗"} {p}: {n}"
    for p in PROPS do
      match t.first.get? p with
      | some tr => IO.println s!"  first {p}:\n{tr}"
      | none => pure ()
