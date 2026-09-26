"""Replay logged prompts under ranking variants.

The seen-set is not logged, so it is reconstructed per session, and the
reconstruction is the harness's largest approximation:

  * Recall only ranks after the session's full read, and the full read marks
    every active node seen. So every node active in the graphs at the
    session's first post-nudge record counts as seen for the whole session.
  * A node a logged record flags as seen, and that production recall had not
    injected earlier in the session, reached the session some other way
    (search, read by id): seen from that record on, for every variant.
  * What a variant itself surfaces is seen from then on — its own history,
    not production's.

The consistency check sidesteps the reconstruction: it applies the logged
seen flags of the ids in each record, so what it tests is the ranking and the
gates, which is what "baseline reproduces production" has to mean.
"""

from collections import defaultdict

from core.constants import FILE_RECALL_REASON
from core.utils import active_node_ids

from .data import KNOWN, GraphHistory, USER_GRAPH_REL, project_graph_rel
from .variants import Graphs, baseline_decision, resolve

# Records that carry the terms the ranking saw. File recall records carry
# files, not terms: they are never replayed, but what they injected is seen.
REPLAYABLE = ("injected", "no_hits", "all_seen", "trimmed_to_seen")
# Routes that mean the endorsed node was dug up rather than put in front of
# the session. "unknown" (seen before routes were tracked) is neither.
DUG_UP_VIAS = ("search", "read", None)
MISMATCH_EXAMPLES = 5


def records_of(value) -> list[dict]:
    """A logged list of node records, tolerating null and junk entries."""
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _ids(recs, only_unseen=False):
    return [r.get("id") for r in records_of(recs)
            if r.get("id") and not (only_unseen and r.get("seen"))]


def is_file_record(r: dict) -> bool:
    return r.get("reason") == FILE_RECALL_REASON


def file_injected_ids(r: dict) -> list:
    """Ids a file recall record put in front of the session, else []."""
    if not is_file_record(r) or r.get("outcome") != "injected":
        return []
    return _ids(r.get("nodes"), True)


class Session:
    """One KG session's replayable prompts plus what the log says it saw."""

    def __init__(self, sid: str, records: list[dict], project: str | None = None):
        self.sid = sid
        self.records = records
        # The recall log's project is the hook's cwd; the session's
        # registered path, when known, is what the server searched.
        self.project = project or next(
            (r.get("project") for r in records if r.get("project")), None)
        # Only prompts mark the full read: file recall fires on tool calls,
        # which run before the full read as readily as after it.
        post_nudge = [r for r in records
                      if r.get("reason") != "full_read_nudge" and not is_file_record(r)]
        self.full_read_ts = post_nudge[0]["ts"] if post_nudge else None
        self.steps = [r for r in records
                      if r.get("reason") in REPLAYABLE and r.get("terms")
                      and isinstance(r["terms"], list)
                      and all(isinstance(t, str) for t in r["terms"])]
        # Per step: ids seen through non-ambient routes by then (cumulative),
        # file recall included, and ids production recall surfaced before it.
        self.other_seen: list[frozenset] = []
        self.prod_before: list[frozenset] = []
        other, prod = set(), set()
        steps = {id(r) for r in self.steps}
        for r in records:
            if id(r) not in steps:
                other |= set(file_injected_ids(r)) - prod
                continue
            self.prod_before.append(frozenset(prod))
            flagged = {i for key in ("hits", "best", "connectors")
                       for i, seen in ((x.get("id"), x.get("seen")) for x in records_of(r.get(key)))
                       if seen and i}
            other |= flagged - prod
            self.other_seen.append(frozenset(other))
            if r.get("reason") == "injected":
                prod |= set(_ids(r.get("hits"), True)) | set(_ids(r.get("connectors"), True))


class Replayer:
    def __init__(self, history: GraphHistory, recall: list[dict],
                 session_projects: dict | None = None):
        self.history = history
        session_projects = session_projects or {}
        by_sid = defaultdict(list)
        for r in recall:
            if r.get("kg_session"):
                by_sid[r["kg_session"]].append(r)
        self.sessions = {sid: Session(sid, sorted(recs, key=lambda r: r["ts"]),
                                      session_projects.get(sid))
                         for sid, recs in by_sid.items()}
        self.sessions = {sid: s for sid, s in self.sessions.items() if s.steps}
        self._active: dict[str, frozenset] = {}

    def graphs_at(self, project: str | None, ts: float) -> Graphs:
        user, user_state = self.history.at(USER_GRAPH_REL, ts)
        states = {"user": user_state}
        proj = None
        rel = project_graph_rel(project) if project else None
        if rel:
            proj, states["project"] = self.history.at(rel, ts)
        return Graphs(user=user, project=proj, project_path=project, states=states)

    def active_at_full_read(self, s: Session) -> frozenset:
        if s.sid not in self._active:
            g = self.graphs_at(s.project, s.full_read_ts)
            ids = active_node_ids(g.user["nodes"])
            if g.project is not None:
                ids |= active_node_ids(g.project["nodes"])
            self._active[s.sid] = frozenset(ids)
        return self._active[s.sid]

    def run_variant(self, name: str) -> dict:
        """{sid: [{"ts", "surfaced": [ids], "states": {...}}, ...]} — one
        entry per replayed step, surfaced empty when the variant is silent."""
        fn = resolve(name)
        out = {}
        for sid, s in self.sessions.items():
            base = self.active_at_full_read(s)
            own: set = set()
            steps = []
            for k, r in enumerate(s.steps):
                g = self.graphs_at(s.project, r["ts"])
                seen = frozenset(base | s.other_seen[k] | own)
                ids = fn(list(r["terms"]), g, seen)
                surfaced = list(dict.fromkeys(i for i in ids if i not in seen))
                own.update(surfaced)
                steps.append({"ts": r["ts"], "surfaced": surfaced, "states": g.states,
                              "logged_reason": r.get("reason")})
            out[sid] = steps
        return out

    def consistency(self) -> dict:
        """Does the baseline reproduce the logged decisions? Checked only
        where every graph state is KNOWN, with the record's own seen flags."""
        checked = matched = 0
        skipped: dict[str, int] = defaultdict(int)
        mismatches = []
        for sid, s in self.sessions.items():
            base = self.active_at_full_read(s)
            for k, r in enumerate(s.steps):
                g = self.graphs_at(s.project, r["ts"])
                weak = sorted({st for st in g.states.values() if st != KNOWN})
                if weak:
                    skipped[",".join(weak)] += 1
                    continue
                seen = set(base | s.other_seen[k] | s.prod_before[k])
                for key in ("hits", "best", "connectors"):
                    for x in records_of(r.get(key)):
                        if x.get("id"):
                            (seen.add if x.get("seen") else seen.discard)(x["id"])
                d = baseline_decision(list(r["terms"]), g, seen)
                logged = r.get("reason")
                ok = d["reason"] == logged
                if ok and logged in ("injected", "all_seen"):
                    ok = _ids(d["hits"]) == _ids(r.get("hits"))
                elif ok and logged == "no_hits":
                    ok = _ids(d["best"]) == _ids(r.get("best"))
                checked += 1
                if ok:
                    matched += 1
                elif len(mismatches) < MISMATCH_EXAMPLES:
                    mismatches.append({
                        "kg_session": sid, "ts": r["ts"], "terms": r["terms"],
                        "logged": logged, "replayed": d["reason"],
                        "logged_ids": _ids(r.get("hits") or r.get("best")),
                        "replayed_ids": _ids(d.get("hits") or d.get("best")),
                    })
        return {"checked": checked, "matched": matched,
                "skipped_by_state": dict(skipped), "mismatches": mismatches}


def endorsements(useful: list[dict]) -> list[dict]:
    """Accepted endorsements, first per (session, id)."""
    out, keys = [], set()
    for u in useful:
        if u.get("refused") or not u.get("kg_session") or not u.get("id"):
            continue
        key = (u["kg_session"], u["id"])
        if key not in keys:
            keys.add(key)
            out.append(u)
    return out


def score_variant(steps_by_sid: dict, useful: list[dict], baseline: dict | None) -> dict:
    """Endorsement-grounded counts for one variant's replay."""
    surfaced_at: dict[tuple, float] = {}
    injections = 0
    states: dict[str, int] = defaultdict(int)
    for sid, steps in steps_by_sid.items():
        for st in steps:
            states[min(st["states"].values(), key=_STATE_ORDER.index)] += 1
            if st["surfaced"]:
                injections += 1
                for i in st["surfaced"]:
                    surfaced_at.setdefault((sid, i), st["ts"])

    dug = {"total": 0, "replayable": 0, "recovered": 0, "recovered_ids": []}
    amb = {"total": 0, "replayable": 0, "kept": 0, "lost_ids": []}
    endorsed_after = 0
    for u in endorsements(useful):
        sid, nid, ts = u["kg_session"], u["id"], u.get("ts") or 0
        steps = steps_by_sid.get(sid)
        before = steps is not None and any(st["ts"] < ts for st in steps)
        hit = surfaced_at.get((sid, nid))
        early = hit is not None and hit < ts
        if early:
            endorsed_after += 1
        if u.get("via") in DUG_UP_VIAS:
            dug["total"] += 1
            if before:
                dug["replayable"] += 1
                if early:
                    dug["recovered"] += 1
                    dug["recovered_ids"].append(nid)
        elif u.get("via") == "ambient":
            amb["total"] += 1
            if before:
                amb["replayable"] += 1
                if early:
                    amb["kept"] += 1
                else:
                    amb["lost_ids"].append(nid)

    result = {
        "prompts": sum(len(v) for v in steps_by_sid.values()),
        "graph_state": dict(states),
        "injections": injections,
        "surfaced_nodes": len(surfaced_at),
        "surfaced_then_endorsed": endorsed_after,
        "dug_up": dug,
        "ambient_endorsed": amb,
    }
    if baseline is not None:
        extra = dropped = 0
        for sid, steps in steps_by_sid.items():
            for st, bst in zip(steps, baseline.get(sid, [])):
                if st["surfaced"] and not bst["surfaced"]:
                    extra += 1
                elif bst["surfaced"] and not st["surfaced"]:
                    dropped += 1
        result["vs_baseline"] = {"extra_injections": extra, "dropped_injections": dropped}
    return result


_STATE_ORDER = ["current", "approx", "known"]     # weakest first: a step is as
                                                  # known as its least-known graph


def seen_agreement(baseline_steps: dict) -> dict:
    """How often the baseline under the RECONSTRUCTED seen-set agrees with
    the logged speak/silent decision — the reconstruction's own error bar."""
    agree = total = 0
    for steps in baseline_steps.values():
        for st in steps:
            total += 1
            agree += bool(st["surfaced"]) == (st["logged_reason"] == "injected")
    return {"steps": total, "agree": agree}
