"""Inputs for the harness: the two observation logs and graphs over time.

Everything here only reads. Git is invoked with optional locks disabled, so
not even the index is refreshed in the storage root.
"""

import bisect
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from core.constants import RECALL_LOG_NAME, USEFUL_LOG_NAME, project_slug
from core.persistence import GraphPersistence

# Graph state labels, strongest first. "known": git history proves the file
# did not change between the snapshot and the record. "approx": a git
# snapshot from before the record, but the file may have changed in between.
# "current": no usable history, so the graph as it is now.
KNOWN, APPROX, CURRENT = "known", "approx", "current"

USER_GRAPH_REL = "user.json"


def project_graph_rel(project_path: str) -> str | None:
    """Storage-relative graph path for a project, without the server's
    rename migration (which moves files). None for an unusable path."""
    try:
        return f"projects/{project_slug(project_path)}/graph.json"
    except (ValueError, TypeError):
        return None


def parse_time(value: str | None) -> float | None:
    """Epoch seconds from an epoch number or an ISO date/datetime (UTC when
    no zone is given). None passes through."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def read_jsonl(path: Path, skipped: list | None = None) -> list[dict]:
    """Records of one log and its rotated .prev, oldest first.

    Lines that are not a JSON object with a numeric ts are skipped (and
    counted into `skipped` when given): a log truncated mid-write must not
    stop a report, and a record without a time cannot be ordered or windowed.
    """
    records = []
    bad = 0
    for p in (path.with_name(path.name + ".prev"), path):
        try:
            text = p.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                rec = None
            ts = rec.get("ts") if isinstance(rec, dict) else None
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                records.append(rec)
            else:
                bad += 1
    records.sort(key=lambda r: r["ts"])
    if skipped is not None:
        skipped.append(bad)
    return records


def in_window(rec: dict, since: float | None, until: float | None) -> bool:
    ts = rec["ts"]
    return (since is None or ts >= since) and (until is None or ts < until)


def load_logs(root: Path | None, recall_path: Path | None = None,
              useful_path: Path | None = None, since: float | None = None,
              until: float | None = None, skipped: dict | None = None
              ) -> tuple[list[dict], list[dict]]:
    """(recall records, useful records) inside the window. Explicit paths
    win over the storage root's default file names. Unreadable line counts
    go into `skipped` ({"recall": n, "useful": n}) when given."""
    recall_path = recall_path or (root / RECALL_LOG_NAME if root else None)
    useful_path = useful_path or (root / USEFUL_LOG_NAME if root else None)
    bad_r: list = []
    bad_u: list = []
    recall = read_jsonl(recall_path, bad_r) if recall_path else []
    useful = read_jsonl(useful_path, bad_u) if useful_path else []
    if skipped is not None:
        skipped.update(recall=sum(bad_r), useful=sum(bad_u))
    return ([r for r in recall if in_window(r, since, until)],
            [r for r in useful if in_window(r, since, until)])


def session_projects(root: Path | None, useful: list[dict]) -> dict:
    """KG session id -> the project path the session registered, from the
    session registry (live sessions only) and then the endorsement log."""
    out = {u["kg_session"]: u["project"] for u in useful
           if u.get("kg_session") and isinstance(u.get("project"), str)}
    try:
        registry = json.loads((root / "sessions.json").read_text(encoding="utf-8")) if root else {}
    except (OSError, ValueError):
        registry = {}
    if isinstance(registry, dict):
        for sid, data in registry.items():
            if isinstance(data, dict) and isinstance(data.get("project_path"), str):
                out[sid] = data["project_path"]
    return out


def _empty_graph() -> dict:
    return {"nodes": {}, "edges": {}}


class GraphHistory:
    """Graph files as they were at a timestamp, read from the storage root's
    git history when it has one.

    The server's autocommit stages the whole tree, so when the first commit
    at or after a record's time does not change a file, that file held the
    same content from its previous change through the record: its state is
    KNOWN. When that first later commit does change the file, the change
    happened somewhere in between and the earlier snapshot is only APPROX.
    Each snapshot is parsed once and shared by every caller in this process,
    so treat returned graphs as read-only. None of them is the server's.
    """

    def __init__(self, root: Path | None):
        self.root = root
        self._git = bool(root) and (root / ".git").exists()
        self._repo: tuple[list[float], list[str]] | None = None
        self._file_commits: dict[str, tuple[list[float], list[str]]] = {}
        self._blobs: dict[tuple[str, str], dict] = {}
        self._current: dict[str, dict] = {}

    def _run_git(self, *args: str) -> str | None:
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        try:
            out = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(self.root), *args],
                capture_output=True, text=True, timeout=60, env=env,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    def _log(self, *paths: str) -> list[tuple[float, str]]:
        """(commit time, sha) oldest first — the whole repo when no path."""
        out = self._run_git("log", "--format=%ct %H", "--", *paths)
        rows = []
        for line in (out or "").splitlines():
            ct, _, sha = line.partition(" ")
            if sha:
                rows.append((float(ct), sha))
        rows.sort(key=lambda r: r[0])       # bisect needs time order
        return rows

    def _commits_for(self, rel: str) -> tuple[list[float], list[str]]:
        if rel not in self._file_commits:
            rows = self._log(rel)
            self._file_commits[rel] = ([t for t, _ in rows], [s for _, s in rows])
        return self._file_commits[rel]

    def _repo_commits(self) -> tuple[list[float], list[str]]:
        if self._repo is None:
            rows = self._log()
            self._repo = ([t for t, _ in rows], [s for _, s in rows])
        return self._repo

    def _blob(self, sha: str, rel: str) -> dict:
        key = (sha, rel)
        if key not in self._blobs:
            out = self._run_git("show", f"{sha}:{rel}")
            data = None
            if out is not None:
                try:
                    data = json.loads(out)
                except ValueError:
                    data = None
            self._blobs[key] = _parse(data)     # empty: deleted or unparsable
        return self._blobs[key]

    def _current_graph(self, rel: str) -> dict:
        if rel not in self._current:
            data = None
            if self.root:
                try:
                    data = json.loads((self.root / rel).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = None
            self._current[rel] = _parse(data)
        return self._current[rel]

    def at(self, rel: str | None, ts: float) -> tuple[dict, str]:
        """(graph, state) for a storage-relative path at time ts."""
        if rel is None:
            return _empty_graph(), CURRENT
        if not self._git:
            return self._current_graph(rel), CURRENT
        repo_times, repo_shas = self._repo_commits()
        if not repo_times or repo_times[0] > ts:
            # History starts after this record: nothing to read it from.
            return self._current_graph(rel), CURRENT
        times, shas = self._commits_for(rel)
        i = bisect.bisect_right(times, ts)          # commits changing rel at <= ts
        # The first repo commit at or after ts decides whether the snapshot
        # still held at ts: if it is also a change to this file, it may not.
        j = bisect.bisect_left(repo_times, ts)
        if j == len(repo_times):
            state = APPROX                           # nothing committed since
        else:
            next_change = shas[i] if i < len(shas) else None
            state = APPROX if next_change == repo_shas[j] else KNOWN
        if i == 0:
            return _empty_graph(), state             # file did not exist yet
        return self._blob(shas[i - 1], rel), state


def _parse(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return _empty_graph()
    try:
        graph, _versions, _progress = GraphPersistence.parse(data)
    except (KeyError, TypeError, AttributeError):
        return _empty_graph()
    return graph
