"""Observable activity after staged recall, not a causal benefit estimate.

The unit is event × graph level × node. Missing/ambiguous transcripts, absent
historical nodes, no later activity and unknown operations are coverage, not
negative labels. Per-exposure provenance is private and emitted only when
the caller explicitly opts in to transcripts.
"""

import bisect
from collections import Counter, defaultdict
from functools import lru_cache
import re
import time

from .data import (APPROX, KNOWN, USER_GRAPH_REL, is_file_record,
                   project_graph_rel, records_of)
from .paths import touch_path
from .transcripts import TranscriptIndex

MEMORY_SIGNALS = ("read_full", "id_in_prose", "gist_phrase", "node_update", "endorsed")
SIGNALS = MEMORY_SIGNALS + ("endorsement_requested", "touch_request", "touch_edit_request",
                           "touch_completed", "touch_edit_completed")
CAVEATS = [
    "Counts describe observable follow-through, not benefit or causal lift. "
    "Seen anchors and withheld file candidates have different selection and prior relevance.",
    "Windows include the current response and two later human requests, ending before "
    "the third later request or 30 minutes; a second view caps the same window at five minutes.",
    "Tool requests already underway when the hook fired are excluded, including every "
    "inner call of an already-started JavaScript wrapper. No JavaScript is executed.",
    "Missing or ambiguous transcripts, no later activity and unobservable operations "
    "are coverage gaps, not negative evidence. Rates describe observed activity only.",
    "Gists and touches use known/approximate git snapshots only, never the current "
    "graph. Approximate snapshots can be stale; paraphrased or semantic uses are unmeasured.",
    "File requests are a weaker association with ongoing work. Paths are exact lexical "
    "matches; today's file existence or symlinks are not historical evidence. Literal "
    "search operands can name directories; completion means the tool finished, not "
    "proof that a particular file's contents were read.",
    "An explicit node read/update is a request unless completion is recorded. Only "
    "accepted, exact-session/level endorsements from useful.jsonl count as endorsed; "
    "earlier windows without endorsement logging are censored.",
    "Repeated and overlapping exposures can credit the same activity. Distinct "
    "session/level/node counts are reported alongside exposure counts; they are not independent trials.",
    "JSON evidence contains private local paths and node references; keep it local.",
]


@lru_cache(maxsize=32768)
def normalized(text):
    return " ".join(re.findall(r"[\w-]+", text.lower()))


def _selected(recall):
    for event in recall:
        route = "file" if is_file_record(event) else "prompt"
        injected = event.get("outcome") == "injected" if route == "file" else event.get("reason") == "injected"
        if not injected and not (route == "file" and event.get("outcome") in ("all_seen", "throttled")):
            continue
        raw = (records_of(event.get("nodes")) if route == "file"
               else records_of(event.get("hits")) + records_of(event.get("connectors")))
        nodes = {}
        for node in raw:
            nid, level = node.get("id"), node.get("level", "project")
            if not isinstance(nid, str) or not nid or level not in ("project", "user"):
                continue
            if not injected and event.get("outcome") == "throttled" and node.get("seen"):
                continue
            nodes[(level, nid)] = node
        yield event, route, injected, list(nodes.values())


def _phrases(node, level, nid, graphs):
    words = normalized(node.get("gist", "")).split()
    phrases = {" ".join(words[i:i + 8]) for i in range(max(0, len(words) - 7))}
    for other_level, (graph, _) in graphs.items():
        for other_id, other in graph.get("nodes", {}).items():
            if (other_level, other_id) == (level, nid):
                continue
            other_text = " " + normalized(other.get("gist", "")) + " "
            phrases = {p for p in phrases if " " + p + " " not in other_text}
    return phrases


def _reference(transcript, group, tool=None, until=None):
    ref = {"file": transcript["file"], "line": group["line"], "ts": group["ts"],
           "decision_id": group["id"]}
    if tool is not None:
        complete = tool.get("completed_ts")
        observed = complete is not None and (until is None or complete < until)
        ref.update(line=tool["line"] if observed else tool["request_line"],
                   request_line=tool["request_line"], call_id=tool["id"],
                   status=tool["status"] if observed else "requested")
        if observed:
            ref["completed_ts"] = complete
            if tool.get("completion_line"):
                ref["completion_line"] = tool["completion_line"]
    return ref


def _ids(args):
    ids = args.get("ids")
    return ids if isinstance(ids, list) else [args.get("id")]


def _same_session(args, event):
    return not args.get("session_id") or args["session_id"] == event.get("kg_session")


def _pre_hook_endorsement(transcript, event, useful):
    """Don't credit a receipt after the hook for an earlier pending request."""
    for group in transcript["groups"]:
        if group["ts"] > event["ts"]:
            break
        for tool in group["tools"]:
            name = tool["name"].split("__")[-1].split(".")[-1]
            complete = tool.get("completed_ts")
            if (name == "kg_useful" and useful["id"] in _ids(tool["args"])
                    and _same_session(tool["args"], event)
                    and complete is not None and useful["ts"] <= complete
                    and group["ts"] < useful["ts"]):
                return True
    return False


def _signals(event, exposure, transcript, after, graphs, useful, until):
    signals = defaultdict(list)
    nid, level, ts = exposure["id"], exposure["level"], event["ts"]
    graph, state = graphs[level]
    historical = state in (KNOWN, APPROX)
    node = graph.get("nodes", {}).get(nid, {}) if historical else {}
    project = exposure["project"]
    touches = {p for t in node.get("touches", []) if (p := touch_path(t, project, level))}
    phrase_covered = historical and bool(node.get("gist")) and all(st in (KNOWN, APPROX) for _, st in graphs.values())
    phrases = _phrases(node, level, nid, graphs) if phrase_covered else set()
    phrase_covered = bool(phrases)
    collision = sum(nid in g.get("nodes", {}) for g, st in graphs.values() if st in (KNOWN, APPROX)) > 1
    for group in after:
        for block in group["prose"]:
            if block["ts"] >= until:
                continue
            text = block["text"]
            ref = dict(_reference(transcript, group), line=block["line"], ts=block["ts"])
            if not collision and re.search(r"(?<![\w-])" + re.escape(nid) + r"(?![\w-])", text):
                signals["id_in_prose"].append(ref)
            phrase = next((p for p in sorted(phrases) if " " + p + " " in " " + normalized(text) + " "), None)
            if phrase:
                signals["gist_phrase"].append(dict(ref, phrase=phrase))
        for tool in group["tools"]:
            if tool.get("completion_only") and tool["completed_ts"] >= until:
                continue  # future inner completion cannot prove an in-window request
            name = tool["name"].split("__")[-1].split(".")[-1]
            args = tool["args"]
            ref = _reference(transcript, group, tool, until)
            if nid in _ids(args) and _same_session(args, event):
                if name == "kg_read" and args.get("level") in (None, level):
                    signals["read_full"].append(ref)
                elif name == "kg_put_node" and args.get("level") == level:
                    signals["node_update"].append(ref)
                elif name == "kg_useful":
                    signals["endorsement_requested"].append(ref)
            for key, targets in (("touch_request", tool["files"]), ("touch_edit_request", tool["edit_files"])):
                matched = touches.intersection(targets)
                if matched:
                    signals[key].append(dict(ref, files=sorted(matched)))
                    if ref.get("status") == "completed":
                        complete_key = "touch_completed" if key == "touch_request" else "touch_edit_completed"
                        signals[complete_key].append(dict(ref, ts=tool["completed_ts"], files=sorted(matched)))
    for endorsement in useful:
        if (endorsement.get("refused") or endorsement.get("kg_session") != event.get("kg_session")
                or not event.get("kg_session") or endorsement.get("id") != nid
                or endorsement.get("level") != level
                or endorsement.get("claude_session") not in (None, event.get("claude_session"))
                or not ts < endorsement["ts"] < until
                or _pre_hook_endorsement(transcript, event, endorsement)):
            continue
        signals["endorsed"].append({**endorsement.get("_source", {}), "ts": endorsement["ts"],
                                     "status": "accepted", "via": endorsement.get("via")})
    return dict(signals), node, touches, phrase_covered, collision


def _summary(details, five=False):
    key = "five_minute_signals" if five else "signals"
    activity = "five_minute_activity" if five else "later_activity"
    observable = "five_minute_observable" if five else "observable"
    covered = [d for d in details if d["covered"]]
    observed = [d for d in covered if d[activity]]
    memory = [d for d in covered if any(d[key].get(s) for s in MEMORY_SIGNALS)]
    memory_observable = [d for d in observed if all(d[observable].get(s) for s in MEMORY_SIGNALS)]
    memory_on_observable = sum(any(d[key].get(s) for s in MEMORY_SIGNALS) for d in memory_observable)
    pairs = lambda ds: {(d["session"], d["level"], d["id"]) for d in ds if d.get("session")}
    per_signal = {}
    for signal in SIGNALS:
        eligible = [d for d in observed if d[observable].get(signal)]
        count = sum(bool(d[key].get(signal)) for d in covered)
        eligible_count = sum(bool(d[key].get(signal)) for d in eligible)
        per_signal[signal] = {"count": count, "observable_exposures": len(eligible),
                              "count_on_observable": eligible_count,
                              "rate": round(eligible_count / len(eligible), 4) if eligible else None}
    return {"exposures": len(details), "covered_exposures": len(covered),
            "with_later_activity": len(observed), "distinct_session_nodes": len(pairs(details)),
            "distinct_session_node_pairs": len({(d["session"], d["id"]) for d in details if d.get("session")}),
            "distinct_with_followthrough": len(pairs(memory)), "memory_specific": len(memory),
            "memory_specific_observable_exposures": len(memory_observable),
            "memory_specific_on_observable": memory_on_observable,
            "memory_specific_rate": (round(memory_on_observable / len(memory_observable), 4)
                                     if memory_observable else None),
            "signals": per_signal}


def _observable(resolved, unknown, file_unknown, touches, phrases, collision, endorsement):
    out = {s: resolved for s in SIGNALS}
    for signal in ("read_full", "node_update", "endorsement_requested"):
        out[signal] = resolved and not unknown
    out.update(id_in_prose=resolved and not collision, gist_phrase=resolved and phrases,
               endorsed=resolved and endorsement == "full")
    for signal in ("touch_request", "touch_edit_request", "touch_completed", "touch_edit_completed"):
        out[signal] = resolved and bool(touches) and not unknown and not file_unknown
    return out


def _window_unknown(transcript, after, ts, until):
    unknown = [u for u in transcript["unknown"] if ts < u["ts"] < until]
    unknown.extend({"line": tool["request_line"], "ts": group["ts"], "reason": "inner_completion_after_window"}
                   for group in after for tool in group["tools"]
                   if tool.get("completion_only") and tool["completed_ts"] >= until)
    return unknown


def build_followthrough(history, recall, useful, projects, *, all_useful=None, until=None,
                        claude_projects=None, codex_home=None):
    selected = list(_selected(recall))
    index = TranscriptIndex({r.get("claude_session") for r, *_ in selected}, claude_projects, codex_home)
    coverage, details, transcripts = Counter(), [], {}
    log_start = min((u["ts"] for u in (all_useful if all_useful is not None else useful)), default=None)
    end = until if until is not None else time.time()
    for event, route, injected, nodes in selected:
        sid, ts = event.get("claude_session"), event["ts"]
        transcript = index.get(sid, event.get("transcript_path"))
        harness, status = transcript["harness"], transcript["status"]
        coverage[f"{route}/{'injection' if injected else 'baseline'}/{status}/{harness}"] += 1
        resolved = status == "resolved"
        if resolved:
            transcripts[transcript["file"]] = transcript["diagnostics"]
            users = transcript["users"]
            first = bisect.bisect_right(users, ts)
            cutoff = min(ts + 1800, end, users[first + 2] if first + 2 < len(users) else float("inf"))
            after = [g for g in transcript["groups"] if ts < g["ts"] < cutoff]
            unknown = _window_unknown(transcript, after, ts, cutoff)
        else:
            cutoff, after, unknown = min(ts + 1800, end), [], []
        file_unknown = [{"line": tool["request_line"], "ts": group["ts"]}
                        for group in after for tool in group["tools"] if tool.get("file_observable") is False]
        project = projects.get(event.get("kg_session"), event.get("project"))
        graphs = {"user": history.at(USER_GRAPH_REL, ts)}
        if project:
            graphs["project"] = history.at(project_graph_rel(project), ts)
        else:
            graphs["project"] = ({"nodes": {}}, "unavailable")
        for node in nodes:
            level = node.get("level", "project")
            cohort = (("injected_seen" if node.get("seen") else "injected_unseen") if injected
                      else "withheld_" + event["outcome"])
            detail = {"recall": event.get("_source", {}), "ts": ts, "until": cutoff,
                      "five_minute_until": min(ts + 300, cutoff), "route": route, "harness": harness,
                      "cohort": cohort, "session": sid, "kg_session": event.get("kg_session"),
                      "project": project, "id": node["id"], "level": level, "covered": resolved,
                      "transcript_status": status, "transcript": transcript.get("file"),
                      "later_activity": bool(after), "future_groups": len(after),
                      "unknown_operations": unknown, "unobservable_file_requests": file_unknown,
                      "signals": {}, "five_minute_signals": {},
                      "observable": {}}
            signals, historical_node, touches, phrases, collision = _signals(
                event, detail, transcript, after, graphs, useful, cutoff) if resolved else ({}, {}, set(), False, False)
            detail["signals"] = signals
            after_five = [g for g in after if g["ts"] < detail["five_minute_until"]]
            detail["five_minute_signals"] = _signals(
                event, detail, transcript, after_five, graphs, useful, detail["five_minute_until"])[0] if resolved else {}
            detail["five_minute_activity"] = bool(after_five)
            detail["graph_state"] = graphs[level][1] if graphs[level][1] in (KNOWN, APPROX) else "unavailable"
            detail["node_found"] = bool(historical_node)
            detail["touches"] = len(touches)
            detail["prose_level_ambiguous"] = collision
            detail["endorsement_coverage"] = ("full" if log_start is not None and log_start <= ts else "partial"
                                               if log_start is not None and log_start < cutoff else "unavailable")
            detail["observable"] = _observable(resolved, unknown, file_unknown, touches, phrases, collision,
                                                detail["endorsement_coverage"])
            five_unknown = _window_unknown(transcript, after_five, ts, detail["five_minute_until"]) if resolved else []
            five_file_unknown = [u for u in file_unknown if u["ts"] < detail["five_minute_until"]]
            detail["five_minute_observable"] = _observable(resolved, five_unknown, five_file_unknown, touches,
                                                            phrases, collision, detail["endorsement_coverage"])
            detail["five_minute_unknown_operations"] = five_unknown
            details.append(detail)
    cohorts = defaultdict(list)
    for detail in details:
        cohorts["/".join(detail[k] for k in ("route", "harness", "cohort"))].append(detail)
    summary = {key: dict(_summary(ds), five_minute=_summary(ds, True)) for key, ds in sorted(cohorts.items())}
    return {"window": {"minutes": 30, "later_human_requests": 2, "sensitivity_minutes": 5, "until": end},
            "events": len(selected), "injection_events": sum(injected for _, _, injected, _ in selected),
            "coverage": {"events": dict(sorted(coverage.items())),
                         "exposures_by_transcript_status": dict(Counter(d["transcript_status"] for d in details)),
                         "historical_graph_state": dict(Counter(d["graph_state"] for d in details if d["covered"])),
                         "missing_historical_nodes": sum(not d["node_found"] for d in details if d["covered"]),
                         "no_later_activity": sum(not d["later_activity"] for d in details if d["covered"]),
                         "exposures_with_unknown_operations": sum(bool(d["unknown_operations"]) for d in details),
                         "exposures_with_unobservable_file_requests": sum(bool(d["unobservable_file_requests"]) for d in details),
                         "exposures_with_ambiguous_prose_level": sum(d["prose_level_ambiguous"] for d in details),
                         "endorsement_windows": dict(Counter(d["endorsement_coverage"] for d in details if d["covered"])),
                         "inventory_diagnostics": dict(index.diagnostics), "transcript_diagnostics": transcripts},
            "summary": summary, "details": details, "caveats": CAVEATS}


def format_followthrough(report):
    lines = ["", "Activity after staged recall (descriptive)"]
    cov = report["coverage"]
    lines.append("  event coverage: " + ", ".join(f"{k}={n}" for k, n in cov["events"].items()))
    lines.append("  historical graph coverage: " + ", ".join(f"{k}={n}" for k, n in cov["historical_graph_state"].items()))
    lines.append(f"  coverage gaps: missing historical nodes {cov['missing_historical_nodes']}, "
                 f"no later activity {cov['no_later_activity']}, "
                 f"exposures with unknown operations {cov['exposures_with_unknown_operations']}, "
                 f"unobservable file requests {cov['exposures_with_unobservable_file_requests']}")
    for key, cohort in report["summary"].items():
        lines.append(f"  {key}: exposures {cohort['exposures']}, resolved {cohort['covered_exposures']}, "
                     f"with later activity {cohort['with_later_activity']}, "
                     f"distinct session/node {cohort['distinct_session_node_pairs']}, "
                     f"distinct session/level/node {cohort['distinct_session_nodes']}")
        lines.append(f"    memory-specific {cohort['memory_specific']}; "
                     f"distinct {cohort['distinct_with_followthrough']}; "
                     f"five-minute {cohort['five_minute']['memory_specific']}")
        for signal, stats in cohort["signals"].items():
            rate = "n/a" if stats["rate"] is None else f"{stats['rate'] * 100:.1f}%"
            lines.append(f"    {signal}: observed {stats['count']}; "
                         f"{stats['count_on_observable']}/{stats['observable_exposures']} observable ({rate})")
            example = next((d["signals"][signal][0] for d in report["details"]
                            if "/".join(d[k] for k in ("route", "harness", "cohort")) == key
                            and d["signals"].get(signal)), None)
            if example:
                lines.append(f"      evidence {example.get('file', '?')}:{example.get('line', '?')} "
                             f"({example.get('status', 'assistant prose')})")
    lines.extend("  - " + caveat for caveat in report["caveats"])
    return lines
