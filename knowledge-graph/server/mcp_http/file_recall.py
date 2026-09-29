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
import shlex
import threading
import time

from core.constants import (
    FILE_RECALL_CHAR_BUDGET,
    FILE_RECALL_MAX_BASH_FILES,
    FILE_RECALL_MAX_NODES,
    FILE_RECALL_MAX_PER_WINDOW,
    FILE_RECALL_REASON,
    FILE_RECALL_WINDOW_SECONDS,
    project_namespace,
)

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
# Which files a tool call touched
# --------------------------------------------------------------------------

_PATH_TOOLS = {"Read": "file_path", "Edit": "file_path", "Write": "file_path",
               "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}

_READ_COMMANDS = frozenset({"cat", "head", "tail", "less", "sed", "grep", "jq", "nl", "rg"})
_SEPARATORS = frozenset({"|", "||", "&", "&&", ";", "\n", "(", ")", "|&", ";;"})
# Short options that consume the next word, per command.
_ARG_OPTS = {
    "head": set("nc"),
    "tail": set("ncs"),
    "sed": set("ef"),
    "grep": set("efmABCdD"),
    "jq": set("f"),
    "cat": set(), "less": set(),
}
# jq long options and how many words each consumes.
_JQ_LONG_ARGS = {"--arg": 2, "--argjson": 2, "--slurpfile": 2, "--rawfile": 2,
                 "--indent": 1, "--from-file": 1, "--tab": 0}
_UNSAFE_CHARS = set("$`*?[]{}")

# New commands use an explicit option grammar: an unknown option may consume
# the next word, so treating that word as a filename would invent a read.
_RG_ARG_LONG = {"--regexp", "--file", "--glob", "--iglob", "--type", "--type-not",
                "--context", "--before-context", "--after-context", "--max-count",
                "--max-columns", "--max-depth", "--max-filesize", "--encoding",
                "--engine", "--threads", "--sort", "--sortr", "--color"}
_RG_FLAG_LONG = {"--line-number", "--no-line-number", "--with-filename", "--no-filename",
                 "--ignore-case", "--case-sensitive", "--smart-case", "--fixed-strings",
                 "--word-regexp", "--line-regexp", "--invert-match", "--only-matching",
                 "--count", "--count-matches", "--files-with-matches", "--files-without-match",
                 "--quiet", "--hidden", "--no-ignore", "--no-ignore-vcs", "--no-heading",
                 "--heading", "--pcre2", "--multiline", "--multiline-dotall", "--text",
                 "--json", "--no-messages", "--stats", "--no-config"}
_NL_ARG_LONG = {"--body-numbering", "--header-numbering", "--footer-numbering",
                "--section-delimiter", "--line-increment", "--join-blank-lines",
                "--number-format", "--number-separator", "--starting-line-number",
                "--number-width"}


def _explicit_operands(cmd, words):
    """nl/rg reads with known options and explicit file operands only."""
    long_args = _RG_ARG_LONG if cmd == "rg" else _NL_ARG_LONG
    long_flags = _RG_FLAG_LONG if cmd == "rg" else {"--no-renumber"}
    short_args = set("efgtTABCmMEj") if cmd == "rg" else set("bdfhilnsvw")
    short_flags = set("nNHilsSIFwxcqoaUPuL0") if cmd == "rg" else {"p"}
    operands, pattern_given, opts_done, i = [], False, False, 0
    while i < len(words):
        word = words[i]
        i += 1
        if opts_done or not word.startswith("-") or word == "-":
            operands.append(word)
            continue
        if word == "--":
            opts_done = True
            continue
        if word.startswith("--"):
            name, sep, _ = word.partition("=")
            if name in long_args:
                pattern_given |= cmd == "rg" and name in ("--regexp", "--file")
                if not sep:
                    if i == len(words):
                        return None
                    i += 1
            elif name not in long_flags or sep:
                return None
            continue
        flags = word[1:]
        for k, char in enumerate(flags):
            if char in short_args:
                pattern_given |= cmd == "rg" and char in "ef"
                if k == len(flags) - 1:
                    if i == len(words):
                        return None
                    i += 1
                break
            if char not in short_flags:
                return None
    return operands[1:] if cmd == "rg" and not pattern_given else operands


def _lex(command: str) -> list[str] | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None                    # unbalanced quotes


def _operands(cmd: str, words: list[str]) -> list[str] | None:
    """The file operands of one read command, or None when it is not a read
    (sed without -n, sed -i, jq --args)."""
    if cmd in ("nl", "rg"):
        return _explicit_operands(cmd, words)
    arg_opts = _ARG_OPTS[cmd]
    operands: list[str] = []
    script_given = False               # sed -e/-f, grep -e/-f, jq -f
    quiet = False                      # sed -n
    i, opts_done = 0, False
    while i < len(words):
        w = words[i]
        i += 1
        if opts_done or not w.startswith("-") or w == "-":
            operands.append(w)
            continue
        if w == "--":
            opts_done = True
            continue
        if w.startswith("--"):
            name = w.split("=", 1)[0]
            if cmd == "jq":
                if name in ("--args", "--jsonargs"):
                    return None
                if name == "--from-file":
                    script_given = True
                if "=" not in w:
                    i += _JQ_LONG_ARGS.get(name, 0)
            elif cmd == "sed" and name in ("--in-place",):
                return None
            elif cmd == "sed" and name in ("--quiet", "--silent"):
                quiet = True
            elif cmd in ("sed", "grep") and name in ("--expression", "--file", "--regexp"):
                script_given = True
                if "=" not in w:
                    i += 1
            continue
        if cmd in ("head", "tail") and w[1:].isdigit():
            continue                   # head -20
        flags = w[1:]
        for k, ch in enumerate(flags):
            if cmd == "sed" and ch == "i":
                return None
            if cmd == "sed" and ch == "n":
                quiet = True
            if ch in arg_opts:
                if (cmd in ("sed", "grep") and ch in "ef") or (cmd == "jq" and ch == "f"):
                    script_given = True
                if k == len(flags) - 1:
                    i += 1             # the argument is the next word
                break                  # else it is attached: -n5, -e'p'
    if cmd == "sed" and not quiet:
        return None
    if cmd in ("sed", "grep", "jq") and not script_given:
        operands = operands[1:]        # the script / pattern / filter
    return operands


def bash_read_files(command: str, cwd: str | None, *, diagnostics: dict | None = None) -> list[str]:
    """Absolute paths of existing files a Bash command plainly reads.

    Conservative by construction: only cat/head/tail/less/sed -n/grep/jq/nl/rg at
    the head of a pipeline segment, only operands without shell expansion,
    relative operands only while no `cd` has run, and only paths that exist
    as regular files. Missing a file is fine; inventing one is not.
    """
    if not isinstance(command, str) or "<<" in command:
        return []
    tokens = _lex(command)
    if not tokens:
        return []
    segments, cur = [], []
    for tok in tokens:
        if tok in _SEPARATORS:
            segments.append(cur)
            cur = []
        else:
            cur.append(tok)
    segments.append(cur)

    found: list[str] = []
    unresolved = False
    moved = False                      # a cd ran: relative operands are ambiguous
    for seg in segments:
        words, redirect_in = [], []
        k = 0
        while k < len(seg):
            tok = seg[k]
            if tok and set(tok) <= set("<>&"):
                # A redirect: its fd number is already in words, its target next.
                if words and words[-1].isdigit():
                    words.pop()
                if k + 1 < len(seg):
                    if tok == "<":
                        redirect_in.append(seg[k + 1])
                    k += 1
            else:
                words.append(tok)
            k += 1
        while words and "=" in words[0] and not words[0].startswith(("-", "/", ".")):
            words.pop(0)               # FOO=bar cmd
        if not words:
            continue
        cmd = os.path.basename(words[0])
        if cmd in ("cd", "pushd", "popd"):
            moved = True
            continue
        if cmd not in _READ_COMMANDS:
            continue
        operands = _operands(cmd, words[1:])
        if operands is None:
            continue
        for op in operands + redirect_in:
            if op == "-" or not op or _UNSAFE_CHARS & set(op):
                continue
            if op.startswith("~"):
                op = os.path.expanduser(op)
            if not os.path.isabs(op):
                if moved or cwd is None:
                    unresolved = True
                    continue
                op = os.path.join(cwd, op)
            path = os.path.realpath(op)
            if os.path.isfile(path) and path not in found:
                found.append(path)
            if len(found) >= FILE_RECALL_MAX_BASH_FILES:
                return found
    if diagnostics is not None and unresolved:
        diagnostics["outcome"] = "unresolved_cwd"
    return found


def file_targets(tool: str, tool_input: dict, cwd: str, *, shell_cwd_known: bool = True,
                 diagnostics: dict | None = None) -> list[str]:
    """Absolute paths the tool call touched; empty for anything untracked."""
    field = _PATH_TOOLS.get(tool)
    if field:
        target = tool_input.get(field)
        if not isinstance(target, str) or not target:
            return []
        target = os.path.expanduser(target)
        return [target if os.path.isabs(target) else os.path.join(cwd, target)]
    if tool == "Bash":
        workdir = tool_input.get("workdir")
        if isinstance(workdir, str) and os.path.isabs(workdir):
            return bash_read_files(tool_input.get("command"), workdir, diagnostics=diagnostics)
        return bash_read_files(tool_input.get("command"), cwd if shell_cwd_known else None,
                               diagnostics=diagnostics)
    if tool == "apply_patch":
        return patch_files(tool_input.get("command") or tool_input.get("input"), cwd)
    return []


# Codex edits files through apply_patch; the payload carries the patch text,
# and each file it touches is named on one header line.
_PATCH_HEADERS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: ")


def patch_files(patch, cwd: str) -> list[str]:
    """Absolute paths an apply_patch call adds, updates, deletes or moves to."""
    if not isinstance(patch, str):
        return []
    found = []
    for line in patch.splitlines():
        for header in _PATCH_HEADERS:
            if line.startswith(header):
                target = os.path.expanduser(line[len(header):].strip())
                if target:
                    path = target if os.path.isabs(target) else os.path.join(cwd, target)
                    if path not in found:
                        found.append(path)
    return found


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
