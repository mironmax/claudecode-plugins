#!/usr/bin/env python3
"""Tests for core.autocommit — periodic git auto-commit of the storage root.

Runs standalone with the project venv (no pytest required):

    cd knowledge-graph/server && ./venv/bin/python tests/test_autocommit.py

or via pytest. Uses throwaway git repos under a tempdir — never touches
the real ~/.knowledge-graph.

Covers:
  1. No .git in storage root -> silent no-op
  2. Dirty tree (modified + untracked) -> commit with "Auto-save YYYY-MM-DD HH:MM"
  3. Clean tree -> no empty commit
  4. Interval parsing (KG_AUTOCOMMIT_INTERVAL, malformed values, 0 disables)
  5. AutoCommitter lifecycle: disabled start is a no-op; stop() makes a final
     commit of pending changes; stop() is idempotent; the periodic loop fires
"""

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Make `core` importable when run from the tests/ dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.autocommit import (
    AutoCommitter,
    DEFAULT_AUTOCOMMIT_INTERVAL,
    commit_storage,
    get_autocommit_interval,
)

AUTOSAVE_RE = re.compile(r"^Auto-save \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


# --- fixtures ----------------------------------------------------------------
def _git(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False
    )


def make_git_repo(base: Path, name: str) -> Path:
    """Create a tmp git repo with identity configured and one initial commit."""
    repo = base / name
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "KG Test")
    _git(repo, "config", "user.email", "kg-test@example.com")
    (repo / "user.json").write_text('{"nodes": {}}')
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "initial")
    return repo


def commit_count(repo: Path) -> int:
    out = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    return int(out or 0)


def last_message(repo: Path) -> str:
    return _git(repo, "log", "-1", "--format=%s").stdout.strip()


def is_clean(repo: Path) -> bool:
    return _git(repo, "status", "--porcelain").stdout.strip() == ""


# --- tests -------------------------------------------------------------------
def test_no_git_dir_is_noop():
    with tempfile.TemporaryDirectory() as td:
        plain = Path(td) / "no-repo"
        plain.mkdir()
        (plain / "user.json").write_text("{}")
        assert commit_storage(plain) is False
        assert not (plain / ".git").exists()
        # Missing directory entirely must not raise either
        assert commit_storage(Path(td) / "does-not-exist") is False


def test_commit_when_dirty():
    with tempfile.TemporaryDirectory() as td:
        repo = make_git_repo(Path(td), "dirty")
        before = commit_count(repo)
        # modified tracked file + untracked file
        (repo / "user.json").write_text('{"nodes": {"a": {}}}')
        (repo / "sessions.json").write_text("{}")

        assert commit_storage(repo) is True
        assert commit_count(repo) == before + 1
        assert AUTOSAVE_RE.match(last_message(repo)), last_message(repo)
        assert is_clean(repo)


def test_untracked_only_commits():
    with tempfile.TemporaryDirectory() as td:
        repo = make_git_repo(Path(td), "untracked")
        (repo / "projects").mkdir()
        (repo / "projects" / "graph.json").write_text("{}")
        assert commit_storage(repo) is True
        assert is_clean(repo)


def test_clean_tree_no_empty_commit():
    with tempfile.TemporaryDirectory() as td:
        repo = make_git_repo(Path(td), "clean")
        before = commit_count(repo)
        assert commit_storage(repo) is False
        assert commit_storage(repo) is False  # still no-op on repeat
        assert commit_count(repo) == before


def test_interval_env_parsing():
    saved = os.environ.get("KG_AUTOCOMMIT_INTERVAL")
    try:
        os.environ.pop("KG_AUTOCOMMIT_INTERVAL", None)
        assert get_autocommit_interval() == DEFAULT_AUTOCOMMIT_INTERVAL == 900

        os.environ["KG_AUTOCOMMIT_INTERVAL"] = "300"
        assert get_autocommit_interval() == 300

        os.environ["KG_AUTOCOMMIT_INTERVAL"] = "0"
        assert get_autocommit_interval() == 0
        assert AutoCommitter(Path("/nonexistent"), interval=0).enabled is False

        os.environ["KG_AUTOCOMMIT_INTERVAL"] = "banana"
        assert get_autocommit_interval() == DEFAULT_AUTOCOMMIT_INTERVAL
    finally:
        if saved is None:
            os.environ.pop("KG_AUTOCOMMIT_INTERVAL", None)
        else:
            os.environ["KG_AUTOCOMMIT_INTERVAL"] = saved


def test_disabled_committer_never_commits():
    with tempfile.TemporaryDirectory() as td:
        repo = make_git_repo(Path(td), "disabled")
        (repo / "user.json").write_text("changed")
        ac = AutoCommitter(repo, interval=0)
        ac.start()
        assert ac._thread is None  # no loop started
        ac.stop(final_commit=True)  # disabled -> no shutdown commit either
        assert not is_clean(repo)


def test_stop_makes_final_commit_and_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        repo = make_git_repo(Path(td), "final")
        # Long interval: the loop won't fire on its own during the test —
        # only the shutdown path commits.
        ac = AutoCommitter(repo, interval=3600)
        ac.start()
        (repo / "user.json").write_text("shutdown state")
        before = commit_count(repo)
        ac.stop(final_commit=True)
        assert commit_count(repo) == before + 1
        assert is_clean(repo)
        # Second stop must be a no-op even with new changes present
        (repo / "user.json").write_text("later change")
        ac.stop(final_commit=True)
        assert not is_clean(repo)


def test_periodic_loop_fires():
    with tempfile.TemporaryDirectory() as td:
        repo = make_git_repo(Path(td), "loop")
        (repo / "user.json").write_text("tick")
        before = commit_count(repo)
        ac = AutoCommitter(repo, interval=1)
        ac.start()
        try:
            deadline = time.time() + 10
            while time.time() < deadline and commit_count(repo) == before:
                time.sleep(0.2)
            assert commit_count(repo) == before + 1
            assert AUTOSAVE_RE.match(last_message(repo))
        finally:
            ac.stop(final_commit=False)


def main():
    print("=== autocommit tests ===")
    for fn in (
        test_no_git_dir_is_noop,
        test_commit_when_dirty,
        test_untracked_only_commits,
        test_clean_tree_no_empty_commit,
        test_interval_env_parsing,
        test_disabled_committer_never_commits,
        test_stop_makes_final_commit_and_is_idempotent,
        test_periodic_loop_fires,
    ):
        fn()
        print(f"  ok   {fn.__name__}")
    print("all passed")


if __name__ == "__main__":
    main()
