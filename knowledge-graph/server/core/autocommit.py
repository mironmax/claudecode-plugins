"""Periodic git auto-commit of the KG storage root.

The storage root (~/.knowledge-graph) is optionally a git repository. Before
this module existed, commits only happened from manage_server.sh on managed
stop/restart — but in normal operation the server is launched by the
SessionStart hook and dies with machine shutdown, so a managed stop (and thus
a commit) never ran. This module makes the *server itself* commit periodically,
so history accumulates no matter how the server is started or killed.

Behavior:
  - Interval from KG_AUTOCOMMIT_INTERVAL (seconds); default 900 (15 min);
    0 disables the feature entirely (including the shutdown commit).
  - No .git directory in the storage root -> silent no-op (checked every
    tick, so a later `git init` is picked up without a restart).
  - Commits only when the working tree actually has changes (modified or
    untracked) — never an empty commit.
  - Commit message keeps the manage_server.sh convention:
    "Auto-save YYYY-MM-DD HH:MM".
  - git failures are logged (WARNING) and never propagate — the memory
    server must not die because of a backup problem.

manage_server.sh's commit_storage() is intentionally left in place: it still
serves the CLI `kg-memory commit` and the managed stop paths.
"""

import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Default commit cadence in seconds (15 minutes).
DEFAULT_AUTOCOMMIT_INTERVAL = 900

# Same message convention as manage_server.sh commit_storage().
COMMIT_MESSAGE_FORMAT = "Auto-save %Y-%m-%d %H:%M"

# Hard cap on any single git invocation so a hung git (e.g. lock contention)
# can't stall the committer thread forever.
GIT_TIMEOUT_SECONDS = 60


def get_autocommit_interval() -> int:
    """Read KG_AUTOCOMMIT_INTERVAL from the environment.

    Returns seconds between commit attempts; 0 (or negative) disables.
    Malformed values fall back to the default rather than crashing startup.
    """
    raw = os.getenv("KG_AUTOCOMMIT_INTERVAL", str(DEFAULT_AUTOCOMMIT_INTERVAL))
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            f"Invalid KG_AUTOCOMMIT_INTERVAL={raw!r}; "
            f"using default {DEFAULT_AUTOCOMMIT_INTERVAL}s"
        )
        return DEFAULT_AUTOCOMMIT_INTERVAL


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command with cwd pinned to the storage root."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def commit_storage(storage_root: Path) -> bool:
    """Commit pending changes in storage_root, if any.

    Returns True iff a commit was created. Never raises:
      - storage_root missing or not a git repo -> False, silent
      - clean working tree (nothing modified, nothing untracked) -> False
      - any git failure -> False, logged at WARNING
    """
    storage_root = Path(storage_root)
    try:
        if not (storage_root / ".git").is_dir():
            return False

        status = _run_git(["status", "--porcelain"], storage_root)
        if status.returncode != 0:
            logger.warning(
                f"Auto-commit: git status failed in {storage_root}: "
                f"{status.stderr.strip()}"
            )
            return False
        if not status.stdout.strip():
            return False  # clean tree — no empty commits

        add = _run_git(["add", "-A"], storage_root)
        if add.returncode != 0:
            logger.warning(
                f"Auto-commit: git add failed in {storage_root}: "
                f"{add.stderr.strip()}"
            )
            return False

        message = datetime.now().strftime(COMMIT_MESSAGE_FORMAT)
        commit = _run_git(["commit", "-m", message, "--quiet"], storage_root)
        if commit.returncode != 0:
            logger.warning(
                f"Auto-commit: git commit failed in {storage_root}: "
                f"{(commit.stderr or commit.stdout).strip()}"
            )
            return False

        logger.info(f"Auto-committed KG storage ({storage_root}): {message}")
        return True

    except Exception as e:
        logger.warning(f"Auto-commit failed for {storage_root}: {e}")
        return False


class AutoCommitter:
    """Background thread committing the storage root every `interval` seconds.

    Mirrors the store's saver-thread pattern: daemon thread, Event-based wait
    (so stop() takes effect immediately instead of after up to a full
    interval), idempotent shutdown.
    """

    def __init__(self, storage_root: Path, interval: int | None = None):
        self.storage_root = Path(storage_root)
        self.interval = get_autocommit_interval() if interval is None else interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopped = False

    @property
    def enabled(self) -> bool:
        return self.interval > 0

    def start(self):
        """Start the periodic commit loop (no-op when disabled)."""
        if not self.enabled:
            logger.info("Storage auto-commit disabled (KG_AUTOCOMMIT_INTERVAL <= 0)")
            return
        if self._thread is not None:
            return  # already started
        if not (self.storage_root / ".git").is_dir():
            # Informational only — the loop still runs and will start
            # committing if the user later runs `git init` in the storage root.
            logger.info(
                f"Storage auto-commit idle: {self.storage_root} is not a git "
                f"repository (run `git init` there to enable versioned history)"
            )
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="kg-autocommit"
        )
        self._thread.start()
        logger.info(
            f"Storage auto-commit active: every {self.interval}s in {self.storage_root}"
        )

    def _loop(self):
        while not self._stop_event.wait(self.interval):
            commit_storage(self.storage_root)

    def stop(self, final_commit: bool = True):
        """Stop the loop; optionally make one last best-effort commit.

        Idempotent — the lifespan hook and the post-serve fallback may both
        call this; the second call is a no-op. The final commit only runs
        when the feature is enabled, and should be invoked *after* the store
        flushes dirty graphs to disk so the commit captures the final state.
        """
        if self._stopped:
            return
        self._stopped = True
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if final_commit and self.enabled:
            commit_storage(self.storage_root)
