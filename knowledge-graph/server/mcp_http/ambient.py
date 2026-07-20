"""Ambient memory — server-side brains for the per-event hooks.

Two entry points, both fed the hook's raw stdin JSON so the bash side never
parses anything:

  build_prompt_recall  — UserPromptSubmit: full-read nudge while the loud
                         kg_read is outstanding; after it, prompt-matched
                         gists (unseen nodes only) ride the hook's
                         additionalContext. Returns None when the staged
                         random pools should speak instead.
  handle_tool_event    — PostToolUse (Read|WebFetch|WebSearch): counts targets
                         across sessions and returns a capture nudge only on
                         proven re-derivation of an uncovered target.

Both return plain text; the REST layer wraps it in hook-output JSON. Every
failure path returns None — a hook must never break a session.
"""

import json
import logging
import os
import re
import threading
import time

from core.constants import (
    NUDGE_COOLDOWN_SECONDS,
    NUDGE_MAX_PER_SESSION,
    NUDGE_TARGET_COOLDOWN_SECONDS,
    PROMPT_RECALL_CHAR_BUDGET,
    PROMPT_RECALL_MAX_HITS,
    PROMPT_RECALL_MIN_TERM_LEN,
    PROMPT_RECALL_SCORE_MULTI,
    PROMPT_RECALL_SCORE_SINGLE,
    TOOL_EVENT_FILE_MIN_SESSIONS,
    TOOL_EVENT_WEB_MIN_COUNT,
    TOOL_EVENTS_MAX_KEYS,
    project_graph_path,
    project_namespace,
    safe_project_path,
)

logger = logging.getLogger(__name__)

# The deterministic full-read reminder, moved server-side (the hook used to
# compose it from /api/session_state). Wording matches v0.9.21.
FULL_READ_NUDGE = (
    "KG preload is a PARTIAL view — the full graph is NOT in context yet. "
    "Call kg_read(session_id) once before substantive work; it renders "
    "everything the preload dropped without repeating it."
)

# Generic filler that would dominate term lists without carrying retrieval
# signal. Deliberately small — over-filtering hurts more than under-filtering
# (the score threshold already guards precision).
_STOPWORDS = frozenset("""
    about after again also back been before being between both cannot could
    does doing done down each else even every from good have having here into
    just know like little look made make many maybe more most much need needs
    okay only other over please really right same should some still such sure
    take than that them then there these they thing things think this those
    through under very want well were what when where which while will with
    would your yours
""".split())

_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_\-./]*")


def _terms(prompt: str, cap: int = 24) -> list[str]:
    """Retrieval terms from a prompt: lowercased, deduped in order, filtered."""
    out: list[str] = []
    seen: set[str] = set()
    for term in _TERM_RE.findall(prompt.lower()):
        term = term.strip("./-")
        if len(term) < PROMPT_RECALL_MIN_TERM_LEN or term in _STOPWORDS or term in seen:
            continue
        seen.add(term)
        out.append(term)
        if len(out) >= cap:
            break
    return out


def build_prompt_recall(store, session_manager, project_path: str, prompt: str) -> str | None:
    """Text to inject for this prompt, or None to let the random pools speak."""
    hit = session_manager.find_by_project_path(project_path)
    if not hit:
        return None
    sid, data = hit

    # Until the loud full-graph read happens, THE nudge outranks everything.
    if not data.get("full_read_ts"):
        return FULL_READ_NUDGE

    terms = _terms(prompt or "")
    if not terms:
        return None

    seen = session_manager.get_seen(sid)
    result = store.search(" ".join(terms), session_id=sid, seen=seen)
    threshold = PROMPT_RECALL_SCORE_MULTI if len(terms) >= 2 else PROMPT_RECALL_SCORE_SINGLE
    hits = [
        r for r in result.get("top", [])
        if not r.get("seen") and r.get("score", 0.0) >= threshold and r.get("gist")
    ][:PROMPT_RECALL_MAX_HITS]
    if not hits:
        return None

    header = (
        "KG recall — memory matching this prompt "
        f"(depth: kg_read(session_id='{sid}', ids=[...])):"
    )
    lines = [f"- [{r['level']}] {r['id']}: {r['gist']}" for r in hits]
    while hits and len("\n".join([header] + lines)) > PROMPT_RECALL_CHAR_BUDGET:
        hits.pop()
        lines.pop()
    if not hits:
        return None

    session_manager.mark_seen(sid, [r["id"] for r in hits])
    return "\n".join([header] + lines)


# --------------------------------------------------------------------------
# Tool events
# --------------------------------------------------------------------------

# One lock for all counter files — events arrive per tool call, possibly from
# parallel sessions of different projects; contention is negligible at this
# rate and one lock keeps the read-modify-write cycle trivially correct.
_events_lock = threading.Lock()

# Paths whose reads are transient by nature — never worth a capture nudge.
_NOISE_FRAGMENTS = (
    "/tmp/", "/node_modules/", "/venv/", "/.venv/", "/__pycache__/",
    "/.git/", "/dist/", "/build/", "/.claude/", "/.knowledge-graph/",
    "/scratchpad/",
)


def _events_path(project_path: str):
    """Counter file lives beside the project graph (same slug rules)."""
    return project_graph_path(project_path).parent / "tool_events.json"


def _load_events(path) -> dict:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data.setdefault("events", {})
            data.setdefault("throttle", {})
            return data
    except Exception:
        pass
    return {"events": {}, "throttle": {}}


def _save_events(path, data: dict) -> None:
    events = data["events"]
    if len(events) > TOOL_EVENTS_MAX_KEYS:
        for key in sorted(events, key=lambda k: events[k].get("last_ts", 0))[
            : len(events) - TOOL_EVENTS_MAX_KEYS
        ]:
            del events[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def _extract_target(tool: str, tool_input: dict) -> tuple[str, str] | None:
    """(kind, raw target) for the tools we track; None for anything else."""
    if tool == "Read":
        target = tool_input.get("file_path")
        return ("read", target) if target else None
    if tool == "WebFetch":
        target = tool_input.get("url")
        return ("web", target) if target else None
    if tool == "WebSearch":
        target = tool_input.get("query")
        return ("search", target.strip().lower()) if target else None
    return None


def _normalize_file(target: str, project_path: str) -> str | None:
    """Project-relative path when inside the project; None for noise paths."""
    real = os.path.realpath(target)
    probe = real + "/"
    if any(frag in probe for frag in _NOISE_FRAGMENTS):
        return None
    root = os.path.realpath(project_path)
    if (real + "/").startswith(root + "/"):
        return os.path.relpath(real, root)
    return real


def _target_covered(store, project_path: str, needle: str) -> bool:
    """Does any node in the user or project graph already reference this?"""
    needle_l = needle.lower()
    try:
        keys = ["user", project_namespace(str(safe_project_path(project_path)))]
    except ValueError:
        keys = ["user"]
    with store.lock:
        for key in keys:
            graph = store.graphs.get(key)
            if not graph:
                continue
            for node in graph["nodes"].values():
                for touch in node.get("touches", []):
                    if needle_l in touch.lower():
                        return True
                if needle_l in node.get("gist", "").lower():
                    return True
                for note in node.get("notes", []):
                    if needle_l in note.lower():
                        return True
    return False


def _nudge_text(kind: str, needle: str, entry: dict, kg_sid: str) -> str:
    distinct = len(entry.get("sessions", []))
    count = entry.get("count", 0)
    if kind == "read":
        evidence = f"read in {distinct} distinct sessions now"
        level = "project"
    elif kind == "web":
        evidence = f"fetched {count} times now"
        level = "project (or user, if the finding is cross-project)"
    else:
        evidence = f"searched {count} times now"
        level = "project (or user, if the finding is cross-project)"
    return (
        f"KG capture: '{needle}' — {evidence}, and no memory node references it. "
        "That repetition means a bottom line worth keeping was re-derived. If this "
        "one has lasting value (what it is/handles, the gotcha, the decision), "
        f"capture it: kg_put_node(session_id='{kg_sid}', level='{level}', "
        f"id='<kebab-id>', gist='<the bottom line>', touches=['{needle}']). "
        "Genuinely one-off? Skip."
    )


def handle_tool_event(store, session_manager, payload: dict) -> str | None:
    """Record a Read/WebFetch/WebSearch event; return a capture nudge or None."""
    project_path = payload.get("cwd")
    tool = payload.get("tool_name")
    claude_sid = payload.get("session_id") or "unknown"
    tool_input = payload.get("tool_input") or {}
    if not project_path or not tool:
        return None

    extracted = _extract_target(tool, tool_input if isinstance(tool_input, dict) else {})
    if not extracted:
        return None
    kind, target = extracted

    if kind == "read":
        needle = _normalize_file(target, project_path)
        if not needle:
            return None
    else:
        needle = target

    try:
        path = _events_path(project_path)
    except ValueError:
        return None  # path outside home — not a graph-bearing project

    now = time.time()
    key = f"{kind}:{needle}"

    with _events_lock:
        data = _load_events(path)
        entry = data["events"].setdefault(key, {"count": 0, "sessions": [], "last_ts": 0})
        entry["count"] += 1
        if claude_sid not in entry["sessions"]:
            entry["sessions"] = (entry["sessions"] + [claude_sid])[-10:]
        entry["last_ts"] = now

        try:
            nudge = _decide_nudge(store, session_manager, data, entry,
                                  project_path, kind, needle, now)
        except Exception:
            logger.exception("tool_event nudge decision failed")
            nudge = None
        _save_events(path, data)

    return nudge


def _decide_nudge(store, session_manager, data, entry, project_path, kind, needle, now):
    threshold_met = (
        len(entry["sessions"]) >= TOOL_EVENT_FILE_MIN_SESSIONS
        if kind == "read"
        else entry["count"] >= TOOL_EVENT_WEB_MIN_COUNT
    )
    if not threshold_met:
        return None
    if now - entry.get("nudged_ts", 0) < NUDGE_TARGET_COOLDOWN_SECONDS:
        return None

    hit = session_manager.find_by_project_path(project_path)
    if not hit:
        return None  # nobody in context to act on a nudge
    kg_sid = hit[0]

    # Graphs load lazily; make sure the project graph is in memory before the
    # coverage scan (only reached on the rare threshold-met path).
    try:
        store.read_graphs(kg_sid)
    except Exception:
        pass
    if _target_covered(store, project_path, needle):
        return None

    throttle = data["throttle"].setdefault(kg_sid, {"count": 0, "last_ts": 0})
    if throttle["count"] >= NUDGE_MAX_PER_SESSION:
        return None
    if now - throttle["last_ts"] < NUDGE_COOLDOWN_SECONDS:
        return None

    # Keep the throttle table from accumulating dead sessions.
    for sid in [s for s in data["throttle"] if now - data["throttle"][s]["last_ts"] > 7 * 86400]:
        if sid != kg_sid:
            del data["throttle"][sid]

    throttle["count"] += 1
    throttle["last_ts"] = now
    entry["nudged_ts"] = now
    return _nudge_text(kind, needle, entry, kg_sid)
