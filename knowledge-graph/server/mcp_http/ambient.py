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
    PROMPT_RECALL_MIN_PROMPT_CHARS,
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

# Harness-generated records that reach UserPromptSubmit without a human ask.
_NOTIFICATION_MARKERS = ("<task-notification>", "[SYSTEM NOTIFICATION")
_IMAGE_PLACEHOLDER_RE = re.compile(r"\[Image:[^\]]*\]")
# Drag-and-dropped paths with spaces arrive quoted ('/a dir/file.pdf') —
# one path, several whitespace tokens; swallow the span whole.
_QUOTED_PATH_RE = re.compile(r"'[^']*/[^']*'|\"[^\"]*/[^\"]*\"")


def _prompt_text(prompt: str) -> str | None:
    """The humanly-typed part of a prompt, or None when there is none.

    Task notifications and image pastes carry no user intent, yet their file
    paths and boilerplate match nodes well enough to fire recall (20% of
    week-1 injections). Notifications stay silent outright. Path tokens
    reduce to their basename — a basename can still legitimately match a
    node's touches — but only non-path text counts toward the speak-at-all
    floor.
    """
    p = (prompt or "").strip()
    if not p or any(marker in p for marker in _NOTIFICATION_MARKERS):
        return None
    p = _IMAGE_PLACEHOLDER_RE.sub(" ", p)
    kept: list[str] = []

    def _swallow_quoted(match) -> str:
        base = match.group(0).strip("'\"").rstrip("/").rsplit("/", 1)[-1]
        if base:
            kept.append(base)
        return " "

    p = _QUOTED_PATH_RE.sub(_swallow_quoted, p)
    floor_chars = 0
    for tok in p.split():
        core = tok.strip("'\"()[]<>,")
        if "/" in core:
            base = core.rstrip("/").rsplit("/", 1)[-1]
            if base:
                kept.append(base)
        else:
            kept.append(tok)
            floor_chars += len(tok)
    if floor_chars < PROMPT_RECALL_MIN_PROMPT_CHARS:
        return None
    return " ".join(kept)


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

    text = _prompt_text(prompt)
    if text is None:
        return None
    terms = _terms(text)
    if not terms:
        return None

    seen = session_manager.get_seen(sid)
    result = store.search(" ".join(terms), session_id=sid, seen=seen)
    threshold = PROMPT_RECALL_SCORE_MULTI if len(terms) >= 2 else PROMPT_RECALL_SCORE_SINGLE

    # Gate 1 — speak at all: enough match quality among the top hits.
    hits = [
        r for r in result.get("top", [])
        if r.get("score", 0.0) >= threshold and r.get("gist")
    ][:PROMPT_RECALL_MAX_HITS]
    if not hits:
        return None

    # The whole neighbourhood rides along: connector nodes on the paths
    # between hits, and the path edges themselves (already deduped cite-once
    # within this result — edges have no cross-session tracking on purpose).
    # Edge endpoints can also be sub-threshold or lower-ranked matches; every
    # endpoint must render a node line, so those get pulled in connector-style
    # from the search records — an edge citing an unrendered id is dropped.
    hit_ids = {r["id"] for r in hits}
    node_pool = {r["id"]: r for r in result.get("connectors", [])}
    node_pool.update({r["id"]: r for r in result.get("more", [])})
    node_pool.update({r["id"]: r for r in result.get("top", [])})

    connectors = []
    edges = []
    for e in result.get("path_edges", []):
        extra = []
        resolvable = True
        for ep in (e["from"], e["to"]):
            if ep in hit_ids or any(c["id"] == ep for c in connectors):
                continue
            rec = node_pool.get(ep)
            if rec and rec.get("gist"):
                extra.append(rec)
            else:
                resolvable = False
                break
        if resolvable:
            connectors.extend(extra)
            edges.append(e)

    # Gate 2 — novelty: at least one UNSEEN node, or the injection would be
    # pure repetition. Seen nodes still render — as bare id anchors that
    # re-focus attention at near-zero budget — but never justify speaking.
    def _unseen(records):
        return [r for r in records if not r.get("seen")]
    if not _unseen(hits) and not _unseen(connectors):
        return None

    # Week-1 audit: the "depth: kg_read(ids=[...])" invitation that used to
    # live here was followed 0/46 times — gists inline suffice. Trimmed so
    # the header doesn't water down the payload.
    header = "KG recall — memory matching this prompt:"

    def node_line(rec, indent=""):
        if rec.get("seen"):
            return f"{indent}- [{rec['level']}] {rec['id']} (in context)"
        return f"{indent}- [{rec['level']}] {rec['id']}: {rec['gist']}"

    hit_lines = [node_line(r) for r in hits]
    conn_lines = [node_line(c, indent="  ") for c in connectors]
    edge_lines = [f"  {e['from']} --{e['rel']}--> {e['to']}" for e in edges]

    def assemble():
        parts = [header] + hit_lines
        if conn_lines or edge_lines:
            parts.append("  connections:")
            parts.extend(conn_lines)
            parts.extend(edge_lines)
        return "\n".join(parts)

    # Trim ladder, least valuable first: edges, then connectors, then seen
    # anchors, then excess unseen hits (at least one always survives — gate 2
    # guaranteed one exists). Edges drop before the nodes they cite, so a
    # reference can never dangle.
    while edge_lines and len(assemble()) > PROMPT_RECALL_CHAR_BUDGET:
        edge_lines.pop()
    while conn_lines and len(assemble()) > PROMPT_RECALL_CHAR_BUDGET:
        conn_lines.pop()
        connectors.pop()
    while len(assemble()) > PROMPT_RECALL_CHAR_BUDGET and any(r.get("seen") for r in hits):
        idx = max(i for i, r in enumerate(hits) if r.get("seen"))
        hits.pop(idx)
        hit_lines.pop(idx)
    while len(hits) > 1 and len(assemble()) > PROMPT_RECALL_CHAR_BUDGET:
        hits.pop()
        hit_lines.pop()

    shown_unseen = [r["id"] for r in _unseen(hits)] + [c["id"] for c in _unseen(connectors)]
    if not shown_unseen:
        return None
    session_manager.mark_seen(sid, shown_unseen)
    return assemble()


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
