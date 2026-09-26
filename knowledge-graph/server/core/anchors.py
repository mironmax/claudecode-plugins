"""Anchor repair — `touches` entries that no longer resolve.

A node's touches are its anchor to the world: `server/core/debt.py:120-140
(the formula)`, `~/.claude/settings.json`, `src/auth/`. Files move, get
renamed, get deleted, and nothing repairs the pointer — the node keeps
claiming a location that no longer exists, and the next session that follows
it lands on nothing.

Two halves, split by cost:

  Detection (`dangling_touches`) is a stat per entry. It runs wherever debt
  is computed, including every kg_read, so it never walks a tree and never
  runs git.

  Discovery (`resolve_dangling`) finds where a dangling entry went, and runs
  only when a chore is being chosen, off the request thread. A chore has no
  filesystem (chores/settings.json is MCP-only), so the server does all of
  this ahead of time and the chore only decides. Discovery never guesses: it
  offers the rename git recorded, or — when git records no delete — the ONE
  file in the project tree with the same basename. A delete git recorded is
  `gone`, whatever took the name since. Two same-named files is a refusal, not
  a coin toss — a wrong anchor is worse than a dangling one, because it looks
  right.
"""

import os
import re
import subprocess
from pathlib import Path

from .constants import (
    ANCHOR_GIT_TIMEOUT_SECONDS,
    ANCHOR_RENAME_HOPS,
    ANCHOR_WALK_MAX_ENTRIES,
    ANCHOR_WALK_SKIP_DIRS,
)

_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,10}$")
_GLOB_CHARS = frozenset("*?[]{}")


def parse_touch(entry) -> tuple[str, str] | None:
    """(path, suffix) for a path-shaped touches entry, else None.

    The suffix is everything after the path — `:88-120 (rotation schedule)` —
    kept verbatim so a repaired entry keeps its line range and anchor text.
    Entries that are not recognisably a path (free text, a URL, a glob) return
    None: detection must not flag what it cannot check.
    """
    if not isinstance(entry, str):
        return None
    s = entry.strip()
    if not s or "://" in s:
        return None
    token = s.split()[0]
    rest = s[len(token):]
    path, sep, tail = token.partition(":")
    if not path or any(c in _GLOB_CHARS for c in path):
        return None
    if not (path.startswith(("~", "/")) or "/" in path or _EXT_RE.search(path)):
        return None
    return path, sep + tail + rest


def _locations(path: str, root: Path | None, home: Path | None) -> list[Path]:
    """Where a parsed path could live. Empty when there is nothing to check against."""
    if path == "~" or path.startswith("~/"):
        return [home / path[2:]] if home else []
    if path.startswith("~"):
        return []                       # ~otheruser: not ours to resolve
    if path.startswith("/"):
        return [Path(path)]
    # Relative means relative to the project. A user graph has no such base,
    # and guessing one (home) would flag every project-relative entry there.
    return [root / path] if root else []


def dangling_touches(nodes, project_root=None, home=None) -> dict:
    """{node_id: [entry, ...]} for active nodes whose touches no longer resolve.

    A relative entry resolves against the project root (so only in a project
    graph); `~` against home; an absolute path as itself. A project graph whose root is gone from
    this machine reports nothing — every relative anchor would look dangling,
    and none of them is the node's fault.
    """
    root = Path(project_root) if project_root else None
    if root is not None and not root.is_dir():
        return {}
    home = Path(home) if home else None
    out: dict = {}
    for n in nodes:
        if n.get("_archived") or "_orphaned_ts" in n:
            continue
        for entry in n.get("touches") or []:
            parsed = parse_touch(entry)
            if not parsed:
                continue
            places = _locations(parsed[0], root, home)
            if not places:
                continue
            try:
                if any(p.exists() for p in places):
                    continue
            except OSError:
                continue
            out.setdefault(n["id"], []).append(entry)
    return out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TreeIndex:
    """Basename -> paths under one project root, built once per decision.

    `complete` is False when the walk stopped at ANCHOR_WALK_MAX_ENTRIES. An
    incomplete index can still say "more than one match" (ambiguous), but it
    can never say "exactly one" or "none" — both need the whole tree.
    """

    def __init__(self, root: Path, max_entries: int = ANCHOR_WALK_MAX_ENTRIES):
        self.root = root
        self.max_entries = max_entries
        self.files: dict[str, list[str]] = {}
        self.dirs: dict[str, list[str]] = {}
        self.complete = True
        self._built = False

    def ensure(self) -> "TreeIndex":
        """Walk on first use only — a decision git fully answers never walks."""
        if self._built:
            return self
        self._built = True
        root, max_entries = self.root, self.max_entries
        seen = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in ANCHOR_WALK_SKIP_DIRS)
            rel_dir = os.path.relpath(dirpath, root)
            prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
            for d in dirnames:
                self.dirs.setdefault(d, []).append(prefix + d)
            for f in sorted(filenames):
                self.files.setdefault(f, []).append(prefix + f)
            seen += len(dirnames) + len(filenames)
            if seen >= max_entries:
                self.complete = False
                break
        return self


def _git(root: Path, *args) -> str | None:
    """stdout of one git command, or None on any failure. Read-only by construction."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1",
               GIT_TERMINAL_PROMPT="0")
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=off", *args],
            capture_output=True, text=True, timeout=ANCHOR_GIT_TIMEOUT_SECONDS,
            env=env, check=False,
        )
    except Exception:
        return None
    return res.stdout if res.returncode == 0 else None


def git_toplevel(root: Path) -> Path | None:
    out = _git(root, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out and out.strip() else None


def git_rename_target(toplevel: Path, top_rel: str) -> tuple[str | None, str]:
    """(new top-relative path, evidence) for a file git saw renamed away.

    A pathspec on the OLD path shows its rename as a plain delete — `log
    --follow` only follows backward from a path that still exists. So: find the
    commit that deleted the path, then read that commit's own name-status with
    rename detection, which pairs the delete with its destination. Chains
    (a -> b -> c) are followed a few hops until a destination still exists.
    """
    cur = top_rel
    evidence = "no git record"
    for _hop in range(ANCHOR_RENAME_HOPS):
        sha = (_git(toplevel, "log", "-n1", "--diff-filter=D", "--format=%H", "--", cur)
               or "").strip()
        if not sha:
            return None, evidence
        show = _git(toplevel, "show", "-M", "--name-status", "-z", "--format=", sha)
        if show is None:
            return None, evidence
        parts = [p for p in show.strip("\0\n").split("\0")]
        new = None
        i = 0
        while i < len(parts):
            status = parts[i].strip()
            if status[:1] in ("R", "C") and i + 2 < len(parts):
                if status[:1] == "R" and parts[i + 1] == cur:
                    new = parts[i + 2]
                i += 3
            else:
                i += 2
        if not new:
            return None, f"deleted in git {sha[:8]}"
        evidence = f"renamed in git {sha[:8]}"
        if (toplevel / new).exists():
            return new, evidence
        cur = new
    return None, "rename chain too long to follow"


def _format_like(original: str, target: Path, root: Path, home: Path | None,
                 is_dir: bool) -> str:
    """Write the found path in the same form the entry used (relative, ~, absolute)."""
    if original.startswith("~") and home is not None:
        try:
            out = "~/" + target.relative_to(home).as_posix()
        except ValueError:
            out = target.as_posix()
    elif original.startswith("/"):
        out = target.as_posix()
    else:
        out = target.relative_to(root).as_posix()
    if is_dir and not out.endswith("/"):
        out += "/"
    return out


def resolve_dangling(entries, project_root, home=None, index: TreeIndex | None = None,
                     toplevel: Path | None | bool = False) -> list[dict]:
    """One record per dangling entry: what the server could find, and how sure.

    verdict:
      moved      — exactly one place it went; `replacement` is the new entry,
                   same form and same line-range suffix as the old one
      gone       — git records a delete with no rename, or the complete tree
                   holds no file of that name; the entry can be removed
      ambiguous  — several same-named candidates; refuse, never pick
      unknown    — outside the project tree, or the walk was incomplete

    index / toplevel: pass them in to share one walk and one rev-parse across
    several nodes; toplevel=False means "not looked up yet".
    """
    given = Path(os.path.normpath(project_root)) if project_root else None
    root = given.resolve() if given else None
    home = Path(home) if home else None
    out = []
    for entry in entries:
        parsed = parse_touch(entry)
        rec = {"entry": entry, "verdict": "unknown", "replacement": None, "evidence": ""}
        out.append(rec)
        if not parsed or root is None:
            rec["evidence"] = "no project tree to search"
            continue
        path, suffix = parsed
        places = _locations(path, None, home) if path.startswith(("~", "/")) else [root / path]
        if not places:
            rec["evidence"] = "not a path this server can resolve"
            continue
        rel = None
        for base_dir in (given, root):
            try:
                rel = Path(os.path.normpath(places[0])).relative_to(base_dir).as_posix()
                break
            except ValueError:
                continue
        if rel is None:
            rec["evidence"] = "outside the project tree"
            continue
        is_dir = path.endswith("/")
        rel = rel.rstrip("/")
        base = rel.rsplit("/", 1)[-1]

        found, git_ev = None, ""
        if not is_dir:
            if toplevel is False:
                toplevel = git_toplevel(root)
            if toplevel:
                try:
                    top_rel = (root / rel).relative_to(toplevel).as_posix()
                except ValueError:
                    top_rel = None
                if top_rel:
                    new, git_ev = git_rename_target(toplevel, top_rel)
                    if new:
                        found = toplevel / new
                        try:
                            found.relative_to(root)
                        except ValueError:
                            found, git_ev = None, git_ev + " (to outside the project)"
        if found is not None:
            rec.update(verdict="moved", evidence=git_ev,
                       replacement=_format_like(path, found, root, home, is_dir) + suffix)
            continue
        # A delete git recorded without a rename outranks a same-named file:
        # the name was freed, and whatever holds it now arrived on its own.
        if git_ev.startswith("deleted"):
            rec.update(verdict="gone", evidence=git_ev)
            continue

        if index is None:
            index = TreeIndex(root)
        index.ensure()
        matches = (index.dirs if is_dir else index.files).get(base, [])
        extra = f"; {git_ev}" if git_ev else ""
        if len(matches) > 1:
            rec.update(verdict="ambiguous",
                       evidence=f"{len(matches)} {'directories' if is_dir else 'files'} "
                                f"named {base} — not guessing{extra}")
        elif not index.complete:
            rec["evidence"] = "project tree too large to search completely" + extra
        elif len(matches) == 1:
            rec.update(verdict="moved",
                       evidence=f"the only {'directory' if is_dir else 'file'} named {base} "
                                f"in the project{extra}",
                       replacement=_format_like(path, root / matches[0], root, home, is_dir)
                       + suffix)
        else:
            rec.update(verdict="gone",
                       evidence=f"nothing named {base} anywhere in the project{extra}")
    return out


def anchor_candidates(dangling: dict, project_root, home=None,
                      max_nodes: int | None = None) -> dict:
    """{node_id: [record, ...]} for nodes whose dangling entries a chore can act on.

    A node qualifies when at least one entry is `moved` or `gone`; ambiguous
    and unknown entries ride along in its record (the chore is told to leave
    them) but never make a node a target on their own. One walk and one
    rev-parse serve every node.
    """
    if not dangling or not project_root:
        return {}
    root = Path(project_root)
    if not root.is_dir():
        return {}
    index = TreeIndex(root.resolve())
    toplevel = git_toplevel(root.resolve())
    out: dict = {}
    for i, (nid, entries) in enumerate(dangling.items()):
        if max_nodes is not None and i >= max_nodes:
            break
        recs = resolve_dangling(entries, root, home, index=index, toplevel=toplevel)
        if any(r["verdict"] in ("moved", "gone") for r in recs):
            out[nid] = recs
    return out
