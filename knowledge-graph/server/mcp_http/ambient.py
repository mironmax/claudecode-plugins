"""Ambient memory — server-side brains for the per-event hooks.

Two entry points, both fed the hook's raw stdin JSON so the bash side never
parses anything:

  build_prompt_recall  — UserPromptSubmit: full-read nudge while the loud
                         kg_read is outstanding; after it, prompt-matched
                         gists (unseen nodes only) ride the hook's
                         additionalContext. Returns None when the staged
                         random pools should speak instead.
  handle_tool_event    — PostToolUse: file recall (file_recall.py) for the
                         file a tool touched when nodes cover it; otherwise,
                         for Read/WebFetch/WebSearch, counts targets across
                         sessions and returns a capture nudge only on proven
                         re-derivation of an uncovered target.

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
    PROMPT_RECALL_MIN_SOLO_IDF,
    PROMPT_RECALL_MIN_TERM_LEN,
    PROMPT_RECALL_SCORE_MULTI,
    PROMPT_RECALL_SCORE_SINGLE,
    RECALL_LOG_MAX_BYTES,
    RECALL_LOG_NAME,
    TOOL_EVENT_FILE_MIN_SESSIONS,
    TOOL_EVENT_WEB_MIN_COUNT,
    TOOL_EVENTS_MAX_KEYS,
    get_storage_root,
    project_graph_path,
    project_namespace,
    safe_project_path,
)
from core.persistence import append_jsonl

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
    all and any are but can did for get got had has her him his how its let
    may nor not now off one our out per say see she the too two via was who
    why yet you
    use used uses using need needed want wanted make makes making new yes
""".split())

_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_\-./]*")

# Harness-generated records that reach UserPromptSubmit without a human ask.
_NOTIFICATION_MARKERS = ("<task-notification>", "[SYSTEM NOTIFICATION")


# The two scrubbers below are hand scans rather than regexes: they run over
# the raw prompt, which is unbounded input, and a regex with an unbounded
# class before a closing delimiter is quadratic when that delimiter is absent.
def _strip_image_placeholders(p: str) -> str:
    """Replace every '[Image: ...]' token with a space."""
    out, i = [], 0
    while True:
        j = p.find("[Image:", i)
        if j < 0:
            out.append(p[i:])
            return "".join(out)
        k = p.find("]", j)
        if k < 0:
            out.append(p[i:])
            return "".join(out)
        out.append(p[i:j])
        out.append(" ")
        i = k + 1


def _swallow_quoted_paths(p: str, kept: list[str]) -> str:
    """Replace each quoted span containing '/' with a space; its basename
    goes to `kept`. Drag-and-dropped paths with spaces arrive quoted
    ('/a dir/file.pdf') — one path, several whitespace tokens."""
    out, i, n = [], 0, len(p)
    nxt = {"'": p.find("'"), '"': p.find('"')}   # next occurrence of each quote
    while i < n:
        for q in nxt:
            if 0 <= nxt[q] < i:
                nxt[q] = p.find(q, i)
        found = [x for x in nxt.values() if x >= 0]
        if not found:
            out.append(p[i:])
            break
        j = min(found)
        q = p[j]
        out.append(p[i:j])
        k = p.find(q, j + 1)
        inner = p[j + 1:k] if k > 0 else ""
        if k > 0 and "/" in inner:
            base = inner.rstrip("/").rsplit("/", 1)[-1]
            if base:
                kept.append(base)
            out.append(" ")
            i = k + 1
        else:
            out.append(q)
            nxt[q] = k          # the closing quote (or none) is the next opener
            i = j + 1
    return "".join(out)


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
    p = _strip_image_placeholders(p)
    kept: list[str] = []
    p = _swallow_quoted_paths(p, kept)
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


# --------------------------------------------------------------------------
# Injection log
# --------------------------------------------------------------------------

def _recall_log_path():
    return get_storage_root() / RECALL_LOG_NAME


def log_recall(reason: str, project_path: str, claude_sid: str | None,
               sid: str | None = None, terms=None, **extra) -> None:
    """Append one line describing what recall decided for this prompt.

    Every outcome is recorded, not just the injections. Fire rate needs the
    denominator, and the near-misses — the prompts that scored just under the
    bar — are the only evidence a threshold change can be argued from; until
    now nothing has ever seen them. Never raises: a hook must not break a
    session, so a failed write is a debug line and nothing more.
    """
    record = {
        "ts": round(time.time(), 3),
        "reason": reason,
        "project": project_path or None,
        "claude_session": claude_sid,
        "kg_session": sid,
    }
    if terms is not None:
        # Terms, not the prompt: enough to replay the ranking after the
        # transcript that held the prompt has expired, without keeping a
        # second copy of everything the user typed.
        record["terms"] = list(terms)
    record.update(extra)
    append_jsonl(_recall_log_path(), record, RECALL_LOG_MAX_BYTES)


def _hit_record(r: dict) -> dict:
    """The ranking evidence for one node, small enough to keep every time."""
    return {
        "id": r.get("id"),
        "level": r.get("level"),
        "score": round(r.get("score", 0.0), 5),
        "seen": bool(r.get("seen")),
        "title_match": bool(r.get("title_match", False)),
        "matched_terms": r.get("matched_terms"),
        "max_term_idf": round(r.get("max_term_idf", 0.0), 4),
    }


def build_prompt_recall(store, session_manager, project_path: str, prompt: str,
                        claude_sid: str | None = None) -> str | None:
    """Text to inject for this prompt, or None to let the random pools speak."""
    # Resolve by the Claude session id the hook payload carries — concurrent
    # sessions in one project must each track their OWN seen-set (newest-by-
    # path made the older session read and poison the newer one's dedup
    # state, observed live as mid-session session_id drift). Path lookup
    # stays as fallback for sessions registered before the binding existed.
    hit = session_manager.find_by_claude_sid(claude_sid) if claude_sid else None
    if not hit:
        hit = session_manager.find_by_project_path(project_path)
    if not hit:
        # No registered session — nothing to attribute a record to, and the
        # server has no view of this prompt at all. Deliberately unlogged.
        return None
    sid, data = hit

    # Until the loud full-graph read happens, THE nudge outranks everything.
    if not data.get("full_read_ts"):
        log_recall("full_read_nudge", project_path, claude_sid, sid)
        return FULL_READ_NUDGE

    text = _prompt_text(prompt)
    if text is None:
        log_recall("not_a_prompt", project_path, claude_sid, sid)
        return None
    terms = _terms(text)
    if not terms:
        log_recall("no_terms", project_path, claude_sid, sid)
        return None

    seen = session_manager.get_seen(sid)
    result = store.search(" ".join(terms), session_id=sid, seen=seen,
                          top_k=RECALL_SEARCH_TOP_K)
    decision = decide_recall(result, terms)
    reason = decision["reason"]
    threshold = decision["threshold"]
    if reason == "no_hits":
        # The near-miss record: what the search DID find, and how close it
        # came. This is the only place the cost of the threshold and the
        # evidence gate is visible.
        log_recall("no_hits", project_path, claude_sid, sid, terms=terms,
                   threshold=threshold,
                   best=[_hit_record(r) for r in decision["best"]])
        return None
    if reason == "all_seen":
        log_recall("all_seen", project_path, claude_sid, sid, terms=terms,
                   threshold=threshold,
                   hits=[_hit_record(r) for r in decision["hits"]])
        return None
    if reason == "trimmed_to_seen":
        log_recall("trimmed_to_seen", project_path, claude_sid, sid, terms=terms,
                   threshold=threshold)
        return None

    hits, connectors = decision["hits"], decision["connectors"]
    session_manager.mark_seen(sid, decision["shown_unseen"], via="ambient")
    injected = decision["text"]
    log_recall("injected", project_path, claude_sid, sid, terms=terms,
               threshold=threshold,
               chars=len(injected),
               hits=[_hit_record(r) for r in hits],
               connectors=[{"id": c.get("id"), "level": c.get("level"),
                            "seen": bool(c.get("seen"))} for c in connectors],
               edges=decision["edges"])
    return injected


def render_node_line(rec: dict, indent: str = "") -> str:
    """One node in an injection: its gist when unseen, a bare anchor when
    the session already holds it. Shared by prompt and file recall."""
    if rec.get("seen"):
        return f"{indent}- [{rec['level']}] {rec['id']} (in context)"
    return f"{indent}- [{rec['level']}] {rec['id']}: {rec['gist']}"


# top_k above MAX_HITS on purpose: the evidence gate filters the widened list,
# so one sharp hit ranked 6th by accumulation still gets its turn.
RECALL_SEARCH_TOP_K = 10


def decide_recall(result: dict, terms: list[str]) -> dict:
    """What recall does with one search result — pure, no session, no log.

    Split out of build_prompt_recall so the evaluation harness (server/eval)
    replays the production decision rather than a copy of it. Returns
    {"reason", "threshold"} plus, by reason:
      no_hits          — "best": the top search records (near misses)
      all_seen         — "hits": the gated hits, every one already seen
      trimmed_to_seen  — nothing more
      injected         — "hits", "connectors", "edges" (count), "text",
                         "shown_unseen" (ids the session now has seen)
    """
    threshold = PROMPT_RECALL_SCORE_MULTI if len(terms) >= 2 else PROMPT_RECALL_SCORE_SINGLE
    # Gate 1 — speak at all: enough match quality among the top hits, and the
    # match must be evidence, not a lexical stray: corroborated by a second
    # term, or near-unique in the graph, or naming the node's id/gist. A
    # notes-only brush with one moderately common word is the measured noise
    # mechanism (week-2 audit) and stays silent. Records without match meta
    # (older store) pass — the gate fails open.
    def _evidence(r):
        if "matched_terms" not in r:
            return True
        return (r["matched_terms"] >= 2
                or r.get("max_term_idf", 0.0) >= PROMPT_RECALL_MIN_SOLO_IDF
                or r.get("title_match", False))

    hits = [
        r for r in result.get("top", [])
        if r.get("score", 0.0) >= threshold and r.get("gist") and _evidence(r)
    ]
    # Injection order: a node NAMED by a prompt term first, then plain score.
    # kg_search keeps pure RRF order — this reordering is recall's own.
    #
    # max_term_idf used to sit between them, to rescue nodes named by a RARE
    # term from accumulation hits. It inverted the ranking instead, because
    # idf is computed PER GRAPH (store.search_graph_rrf): the user graph is a
    # heterogeneous pile where nearly any specific term is unique (idf ~1.0),
    # while a project graph is topically dense, so the very vocabulary a
    # project node is ABOUT scores low there. Ranking on the single rarest
    # term therefore preferred a one-word coincidence in the user graph over
    # a project node matching ten prompt terms. Measured 2026-08-10 by
    # replaying live prompts: "…rewrite for version 2.0 of MCP … do you have
    # notes" ranked v0934-venv-selfheal-plan, mcp-dep-unbounded-2x-risk and
    # mcp2-migration-shape 1-2-3 by score, and this sort replaced all three
    # with verify-report-against-reporters-box (one term, idf 1.0).
    #
    # title_match survives as the primary key: it is a per-node fact (the
    # evidence is in the id or gist, not buried in notes) and carries no
    # cross-graph calibration, so it ranks honestly.
    hits.sort(key=lambda r: (r.get("title_match", False),
                             r.get("score", 0.0)), reverse=True)
    hits = hits[:PROMPT_RECALL_MAX_HITS]
    if not hits:
        return {"reason": "no_hits", "threshold": threshold,
                "best": result.get("top", [])[:3]}

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
        return {"reason": "all_seen", "threshold": threshold, "hits": list(hits)}

    # Week-1 audit: the "depth: kg_read(ids=[...])" invitation that used to
    # live here was followed 0/46 times — gists inline suffice. Trimmed so
    # the header doesn't water down the payload.
    header = "KG recall — memory matching this prompt:"

    hit_lines = [render_node_line(r) for r in hits]
    conn_lines = [render_node_line(c, indent="  ") for c in connectors]
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
        return {"reason": "trimmed_to_seen", "threshold": threshold}
    return {"reason": "injected", "threshold": threshold, "hits": hits,
            "connectors": connectors, "edges": len(edges), "text": assemble(),
            "shown_unseen": shown_unseen}


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


def file_key(target: str, project_path: str) -> str:
    """Project-relative path when inside the project, else the real path."""
    real = os.path.realpath(os.path.expanduser(target))
    root = os.path.realpath(project_path)
    if (real + "/").startswith(root + "/"):
        return os.path.relpath(real, root)
    return real


def _normalize_file(target: str, project_path: str) -> str | None:
    """file_key, or None for paths too transient to be worth a capture nudge."""
    probe = os.path.realpath(target) + "/"
    if any(frag in probe for frag in _NOISE_FRAGMENTS):
        return None
    return file_key(target, project_path)


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
    """One PostToolUse event: file recall for a file the memory covers, else
    (Read/WebFetch/WebSearch only) count the target and maybe nudge capture.
    Never both in one response."""
    project_path = payload.get("cwd")
    tool = payload.get("tool_name")
    claude_sid = payload.get("session_id") or "unknown"
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not project_path or not tool:
        return None
    try:
        project_path = str(safe_project_path(project_path))
    except ValueError:
        return None  # outside home — not a graph-bearing project

    recall, covered = None, False
    try:
        from .file_recall import build_file_recall, file_targets
        paths = file_targets(tool, tool_input, project_path)
        if paths:
            recall, covered = build_file_recall(store, session_manager, project_path,
                                                tool, paths, payload.get("session_id"))
    except Exception:
        logger.exception("file recall failed")

    extracted = _extract_target(tool, tool_input)
    if not extracted:
        return recall
    kind, target = extracted

    if kind == "read":
        needle = _normalize_file(target, project_path)
        if not needle:
            return recall
    else:
        needle = target

    path = _events_path(project_path)
    now = time.time()
    key = f"{kind}:{needle}"

    with _events_lock:
        data = _load_events(path)
        entry = data["events"].setdefault(key, {"count": 0, "sessions": [], "last_ts": 0})
        entry["count"] += 1
        if claude_sid not in entry["sessions"]:
            entry["sessions"] = (entry["sessions"] + [claude_sid])[-10:]
        entry["last_ts"] = now

        nudge = None
        if not covered:
            try:
                nudge = _decide_nudge(store, session_manager, data, entry,
                                      project_path, kind, needle, now,
                                      claude_sid=claude_sid)
            except Exception:
                logger.exception("tool_event nudge decision failed")
        _save_events(path, data)

    return recall or nudge


def _decide_nudge(store, session_manager, data, entry, project_path, kind, needle, now,
                  claude_sid=None):
    threshold_met = (
        len(entry["sessions"]) >= TOOL_EVENT_FILE_MIN_SESSIONS
        if kind == "read"
        else entry["count"] >= TOOL_EVENT_WEB_MIN_COUNT
    )
    if not threshold_met:
        return None
    if now - entry.get("nudged_ts", 0) < NUDGE_TARGET_COOLDOWN_SECONDS:
        return None

    hit = session_manager.find_by_claude_sid(claude_sid) if claude_sid else None
    if not hit:
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
