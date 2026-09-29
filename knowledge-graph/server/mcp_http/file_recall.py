"""File-anchored recall — the nodes that describe the file the agent is in.

PostToolUse brain for file tools (Read, Edit, Write, MultiEdit, NotebookEdit,
and Bash commands that plainly read a file). Nodes whose `touches` name the
file reach the session once, as gist lines, archived nodes included, through
the same hook output the capture nudge uses. Called from
ambient.handle_tool_event, which owns the nudge; covered files recall,
uncovered ones may nudge, never both.

  file_targets        — the files one tool call touched (Bash: conservative)
  build_file_recall   — lookup, seen-dedup, throttle, render, mark, log

A lookup is two dict gets per file per graph: the touches index is rebuilt
only when the store's write generation for that graph moves.
"""

import logging
import os
import threading
import time

from core.constants import (
    FILE_RECALL_CHAR_BUDGET,
    FILE_RECALL_MAX_NODES,
    FILE_RECALL_MAX_PER_WINDOW,
    FILE_RECALL_REASON,
    FILE_RECALL_WINDOW_SECONDS,
    project_namespace,
)
from core.file_requests import bash_read_files, file_targets, patch_files

from .ambient import file_key, log_recall, render_node_line

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Touches index
# --------------------------------------------------------------------------

def normalize_touch(touch) -> str | None:
    """Index key for one touches entry, or None when it names no file.

    'src/a.py:12-40 (anchor)' -> 'src/a.py'; '~/x' and absolute paths ->
    real absolute path; relative paths -> normalised relative path. URLs and
    empty entries -> None.
    """
    if not isinstance(touch, str):
        return None
    t = touch.strip()
    if not t or "://" in t:
        return None
    t = t.split()[0].split("#", 1)[0]
    while ":" in t:
        head, _, tail = t.rpartition(":")
        if "/" in tail or not head:
            break
        t = head                       # line range or symbol suffix
    t = t.rstrip("/")
    if not t:
        return None
    if t.startswith("~"):
        t = os.path.expanduser(t)
    if os.path.isabs(t):
        return os.path.realpath(t)
    t = os.path.normpath(t)
    return None if t == "." else t


class TouchIndex:
    """normalized touch -> node ids, one table per graph key, rebuilt when
    store.write_gen for that graph (or the graph object itself) changes.
    Callers hold store.lock."""

    def __init__(self):
        self._tables: dict[str, tuple] = {}

    def table(self, store, graph_key: str, relative_ok: bool) -> dict:
        graph = store.graphs.get(graph_key)
        if graph is None:
            return {}
        stamp = (store.write_gen.get(graph_key, 0), id(graph), relative_ok)
        cached = self._tables.get(graph_key)
        if cached and cached[0] == stamp:
            return cached[1]
        table: dict[str, list] = {}
        for node_id, node in graph["nodes"].items():
            for touch in node.get("touches") or []:
                key = normalize_touch(touch)
                if key is None or (not relative_ok and not os.path.isabs(key)):
                    continue
                ids = table.setdefault(key, [])
                if node_id not in ids:
                    ids.append(node_id)
        self._tables[graph_key] = (stamp, table)
        return table


_INDEX = TouchIndex()


# --------------------------------------------------------------------------
# Throttle
# --------------------------------------------------------------------------

_throttle_lock = threading.Lock()
_injections: dict[str, list[float]] = {}   # kg session -> recent injection times


def _throttled(sid: str, now: float) -> bool:
    with _throttle_lock:
        recent = [t for t in _injections.get(sid, []) if now - t < FILE_RECALL_WINDOW_SECONDS]
        _injections[sid] = recent
        for other in [s for s, ts in _injections.items() if not ts]:
            del _injections[other]
        return len(recent) >= FILE_RECALL_MAX_PER_WINDOW


def _note_injection(sid: str, now: float) -> None:
    with _throttle_lock:
        _injections.setdefault(sid, []).append(now)


def reset_throttle() -> None:
    with _throttle_lock:
        _injections.clear()


# --------------------------------------------------------------------------
# Recall
# --------------------------------------------------------------------------

def _matches(store, root: str | None, needles: list[str], seen: set) -> list[dict]:
    """Node records for every live node touching one of the files, project
    graph first, in file order. No root: user-only scope, user graph alone."""
    graphs = [("user", "user")]
    with store.lock:
        if root:
            graphs.insert(0, (project_namespace(root), "project"))
            try:
                store._ensure_project_loaded(root)
            except Exception:
                logger.debug("file recall: project graph not loadable", exc_info=True)
        found: dict[str, dict] = {}
        for graph_key, level in graphs:
            graph = store.graphs.get(graph_key)
            if graph is None:
                continue
            table = _INDEX.table(store, graph_key, relative_ok=(level == "project"))
            for needle in needles:
                probes = (needle,) if os.path.isabs(needle) else (needle, os.path.join(root, needle))
                for probe in probes:
                    for nid in table.get(probe, ()):
                        node = graph["nodes"].get(nid)
                        if (nid in found or node is None or "_orphaned_ts" in node
                                or not node.get("gist")):
                            continue       # index stale by a write in flight
                        found[nid] = {
                            "id": nid, "level": level, "gist": node["gist"],
                            "file": needle, "archived": bool(node.get("_archived")),
                            "seen": nid in seen, "_graph": graph_key,
                        }
    return list(found.values())


def _rank(store, records: list[dict]) -> list[dict]:
    """Best first by the store's node score (archived included), ties by
    recency. Nodes inside the grace period have no score yet and rank after
    scored ones, by recency."""
    now = time.time()
    keyed = []
    with store.lock:
        for graph_key in dict.fromkeys(r["_graph"] for r in records):
            graph = store.graphs.get(graph_key)
            if graph is None:
                continue
            versions = store._versions.get(graph_key, {})
            scores = store.scorer.score_all(graph["nodes"], graph["edges"], versions,
                                            include_archived=True)
            for r in records:
                node = graph["nodes"].get(r["id"])
                if r["_graph"] != graph_key or node is None:
                    continue
                keyed.append(((r["id"] in scores, scores.get(r["id"], 0.0),
                               store.scorer._recency(r["id"], node, versions, now)), r))
    keyed.sort(key=lambda kr: kr[0], reverse=True)
    return [r for _, r in keyed]


def _node_record(r: dict) -> dict:
    return {"id": r["id"], "level": r["level"], "seen": r["seen"],
            "archived": r["archived"], "file": r["file"]}


def build_file_recall(store, session_manager, hit: tuple[str, dict], project_path: str,
                      tool: str, paths: list[str], claude_sid: str | None = None,
                      *, context: dict | None = None
                      ) -> tuple[str | None, bool]:
    """(text to inject or None, whether any node covers these files).

    hit is the event's resolved (kg sid, session record); project_path is the
    hook's cwd, logged as such. Relative touches resolve against the session's
    project root — never against the cwd, which would load whatever graph the
    folder names into a user-only session. Every decision is logged, silences
    included.
    """
    sid, data = hit
    root = data.get("project_path")
    needles = list(dict.fromkeys(file_key(p, root) if root else os.path.realpath(p)
                                 for p in paths))
    seen = session_manager.get_seen(sid)
    viewed_at = time.time()
    matches = _matches(store, root, needles, seen)

    def log(outcome, records=(), **extra):
        log_recall(FILE_RECALL_REASON, project_path, claude_sid, sid,
                   outcome=outcome, tool=tool, files=needles,
                   nodes=[_node_record(r) for r in records], **(context or {}), **extra)

    if not matches:
        log("no_nodes")
        return None, False
    unseen = [r for r in matches if not r["seen"]]
    if not unseen:
        log("all_seen", matches)
        return None, True
    now = time.time()
    if _throttled(sid, now):
        log("throttled", unseen)
        return None, True

    if len(unseen) > 1:
        unseen = _rank(store, unseen) or unseen
    shown = unseen[:FILE_RECALL_MAX_NODES]

    def assemble():
        files = list(dict.fromkeys(r["file"] for r in shown))
        return "\n".join([f"KG memory on {', '.join(files)}:"]
                         + [render_node_line(r) for r in shown])

    while len(shown) > 1 and len(assemble()) > FILE_RECALL_CHAR_BUDGET:
        shown.pop()
    text = assemble()
    session_manager.mark_seen(sid, [r["id"] for r in shown], via="file", at=viewed_at)
    _note_injection(sid, now)
    log("injected", shown, chars=len(text), withheld=len(unseen) - len(shown))
    return text, True
