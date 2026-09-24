"""Descriptive statistics over the logs, and the text rendering of a report.

Wording is deliberate: an endorsement is the only explicit label, and an
injected node nobody endorsed is a weak negative (endorsement is sparse), so
nothing here is called precision, recall or noise.
"""

from collections import Counter, defaultdict
from statistics import median

from .replay import DUG_UP_VIAS, REPLAYABLE, endorsements, records_of

NO_PROJECT = "(no project)"

CAVEATS = [
    "Ground truth is endorsement only. Dug-up endorsements (via search, read, "
    "or never shown) are the misses recall should have prevented; injected "
    "nodes never endorsed are weak negatives, not proof of noise.",
    "Endorsed via 'unknown' (seen before routes were tracked) counts as "
    "neither surfaced nor dug up.",
    "The seen-set is reconstructed, not logged: nodes active at the session's "
    "full read, plus ids logged records flag as seen. 'seen-set agreement' "
    "is the error bar of that reconstruction.",
    "'Earlier in the same session' means before the endorsement; the log "
    "does not say when the node was dug up, which may have been earlier.",
    "Graph state per prompt is 'known' (git proves the file unchanged since "
    "the snapshot), 'approx' (a git snapshot that may be stale) or 'current' "
    "(no history: today's graph). Only 'known' prompts enter the consistency "
    "check.",
]


def _ratio(n, d):
    return round(n / d, 4) if d else None


def _describe(recall: list[dict], useful: list[dict]) -> dict:
    reasons = Counter(r.get("reason") for r in recall)
    ranked = sum(reasons[k] for k in REPLAYABLE)
    injected = reasons["injected"]
    chars = [r["chars"] for r in recall if r.get("reason") == "injected"
             and isinstance(r.get("chars"), (int, float))]

    accepted = endorsements(useful)
    refused = Counter(u.get("refused") for u in useful if u.get("refused"))
    via = Counter(u.get("via") or "never shown" for u in accepted)
    dug_up = sum(1 for u in accepted if u.get("via") in DUG_UP_VIAS)

    # Injected-then-endorsed: distinct (session, id) recall put in front of
    # the session unseen, and the session endorsed afterwards.
    first_injected: dict[tuple, float] = {}
    for r in recall:
        if r.get("reason") != "injected":
            continue
        for key in ("hits", "connectors"):
            for x in records_of(r.get(key)):
                if x.get("id") and not x.get("seen"):
                    first_injected.setdefault((r.get("kg_session"), x["id"]), r.get("ts") or 0)
    endorsed_ts = {(u["kg_session"], u["id"]): u.get("ts") or 0 for u in accepted}
    then_endorsed = sum(1 for k, ts in first_injected.items()
                        if k in endorsed_ts and endorsed_ts[k] > ts)

    return {
        "prompts_logged": len(recall),
        "reasons": dict(reasons),
        "ranked": ranked,
        "fire_rate_all": _ratio(injected, len(recall)),
        "fire_rate_ranked": _ratio(injected, ranked),
        "payload_chars": {
            "n": len(chars),
            "mean": round(sum(chars) / len(chars), 1) if chars else None,
            "median": median(chars) if chars else None,
            "max": max(chars) if chars else None,
        },
        "endorsements": {
            "accepted": len(accepted),
            "refused": dict(refused),
            "via": dict(via),
            "dug_up": dug_up,
            "dug_up_share": _ratio(dug_up, len(accepted)),
        },
        "injected_nodes": len(first_injected),
        "injected_then_endorsed": then_endorsed,
        "injected_then_endorsed_rate": _ratio(then_endorsed, len(first_injected)),
    }


def describe(recall: list[dict], useful: list[dict]) -> dict:
    """Overall and per-project descriptive block."""
    by_proj_r, by_proj_u = defaultdict(list), defaultdict(list)
    for r in recall:
        by_proj_r[r.get("project") or NO_PROJECT].append(r)
    for u in useful:
        by_proj_u[u.get("project") or NO_PROJECT].append(u)
    projects = sorted(set(by_proj_r) | set(by_proj_u))
    return {
        "overall": _describe(recall, useful),
        "projects": {p: _describe(by_proj_r[p], by_proj_u[p]) for p in projects},
    }


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

def _pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _desc_lines(d: dict, indent: str = "  ") -> list[str]:
    e, pc = d["endorsements"], d["payload_chars"]
    reasons = ", ".join(f"{k} {v}" for k, v in sorted(d["reasons"].items(), key=lambda kv: -kv[1]))
    via = ", ".join(f"{k} {v}" for k, v in sorted(e["via"].items(), key=lambda kv: -kv[1]))
    refused = ", ".join(f"{k} {v}" for k, v in sorted(e["refused"].items()))
    lines = [
        f"{indent}prompts logged {d['prompts_logged']} ({reasons or 'none'})",
        f"{indent}fire rate {_pct(d['fire_rate_all'])} of logged, "
        f"{_pct(d['fire_rate_ranked'])} of ranked ({d['ranked']})",
    ]
    if pc["n"]:
        lines.append(f"{indent}payload chars: mean {pc['mean']}, median {pc['median']}, max {pc['max']}")
    lines.append(f"{indent}endorsements accepted {e['accepted']}"
                 + (f" (refused: {refused})" if refused else ""))
    if e["accepted"]:
        lines.append(f"{indent}  via: {via}")
        lines.append(f"{indent}  dug up (search/read/never shown): {e['dug_up']} "
                     f"= {_pct(e['dug_up_share'])} of accepted")
    lines.append(f"{indent}injected nodes {d['injected_nodes']}, endorsed afterwards "
                 f"{d['injected_then_endorsed']} ({_pct(d['injected_then_endorsed_rate'])})")
    return lines


def format_text(rep: dict) -> str:
    out = ["Retrieval evaluation"]
    inp = rep["inputs"]
    out.append(f"  storage root: {inp['root']}  (git history: {'yes' if inp['git'] else 'no'})")
    out.append(f"  window: {inp['since'] or '-'} .. {inp['until'] or '-'}")
    out.append(f"  records: recall {inp['recall_records']}, useful {inp['useful_records']}")
    bad = {k: n for k, n in inp["unreadable_lines"].items() if n}
    if bad:
        out.append("  skipped unreadable lines: " + ", ".join(f"{k} {n}" for k, n in bad.items()))

    out.append("")
    out.append("Descriptive — overall")
    out.extend(_desc_lines(rep["descriptive"]["overall"]))
    for proj, d in rep["descriptive"]["projects"].items():
        out.append(f"Descriptive — {proj}")
        out.extend(_desc_lines(d))

    rp = rep.get("replay")
    if rp:
        out.append("")
        c = rp["consistency"]
        skipped = ", ".join(f"{k} {v}" for k, v in c["skipped_by_state"].items()) or "none"
        verdict = ("n/a — no prompt with fully known graph state" if not c["checked"]
                   else "OK" if c["matched"] == c["checked"] else "MISMATCH")
        out.append(f"Consistency check (baseline vs logged decisions, known graph state): "
                   f"{c['matched']}/{c['checked']} match — {verdict}")
        out.append(f"  skipped by graph state: {skipped}")
        for m in c["mismatches"]:
            out.append(f"  mismatch {m['kg_session']} @ {m['ts']}: logged {m['logged']} "
                       f"{m['logged_ids']} vs replayed {m['replayed']} {m['replayed_ids']}")
        sa = rp["seen_agreement"]
        out.append(f"Seen-set agreement (baseline, reconstructed seen, speak/silent vs log): "
                   f"{sa['agree']}/{sa['steps']}")

        out.append("")
        out.append("Replay by variant")
        for name, v in rp["variants"].items():
            dug, amb = v["dug_up"], v["ambient_endorsed"]
            states = ", ".join(f"{k} {n}" for k, n in sorted(v["graph_state"].items()))
            out.append(f"  {name}")
            out.append(f"    prompts {v['prompts']} (graph state: {states or 'none'})")
            line = f"    injections {v['injections']}, nodes surfaced {v['surfaced_nodes']}"
            if "vs_baseline" in v:
                vb = v["vs_baseline"]
                line += f"  (vs baseline: +{vb['extra_injections']} extra, -{vb['dropped_injections']} dropped)"
            out.append(line)
            out.append(f"    dug-up endorsements surfaced before endorsement: "
                       f"{dug['recovered']}/{dug['replayable']} replayable ({dug['total']} total)")
            out.append(f"    ambient endorsements still surfaced before endorsement: "
                       f"{amb['kept']}/{amb['replayable']} replayable ({amb['total']} total)")
            out.append(f"    surfaced then endorsed: {v['surfaced_then_endorsed']}")
    elif rep.get("replay_skipped"):
        out.append("")
        out.append(f"Replay skipped: {rep['replay_skipped']}")

    out.append("")
    out.append("What this measures")
    out.extend(f"  - {c}" for c in rep["caveats"])
    return "\n".join(out)


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def build_report(root, recall_path=None, useful_path=None, since=None, until=None,
                 variants=("baseline",)) -> dict:
    """The whole report as a JSON-ready dict. Replay runs when a storage
    root is given; the baseline always runs first, since every other variant
    is compared against it."""
    from .data import GraphHistory, load_logs, session_projects
    from .replay import Replayer, score_variant, seen_agreement

    skipped: dict = {}
    recall, useful = load_logs(root, recall_path, useful_path, since, until, skipped)
    history = GraphHistory(root)
    rep = {
        "inputs": {
            "root": str(root) if root else None,
            "git": history._git,
            "since": since, "until": until,
            "recall_records": len(recall), "useful_records": len(useful),
            "unreadable_lines": skipped,
        },
        "descriptive": describe(recall, useful),
        "caveats": CAVEATS,
    }
    if root is None:
        rep["replay_skipped"] = "no storage root, so no graphs to replay against"
        return rep

    names = ["baseline"] + [v for v in variants if v != "baseline"]
    # Unwindowed endorsements: a session's registered project does not
    # depend on which of its endorsements fall inside the window.
    _, all_useful = load_logs(root, recall_path, useful_path)
    replayer = Replayer(history, recall, session_projects(root, all_useful))
    runs = {name: replayer.run_variant(name) for name in names}
    rep["replay"] = {
        "sessions": len(replayer.sessions),
        "consistency": replayer.consistency(),
        "seen_agreement": seen_agreement(runs["baseline"]),
        "variants": {
            name: score_variant(steps, useful,
                                None if name == "baseline" else runs["baseline"])
            for name, steps in runs.items()
        },
    }
    return rep
