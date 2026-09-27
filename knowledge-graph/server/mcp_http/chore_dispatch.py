"""Chore dispatch — maintenance that fires on user activity, not on a clock.

The systemd tick is a poll-decider looking for a moment when the machine is
awake, the quota gauge is fresh, the 5h window is nearly over and usage is
low. Measured over 45 days that moment arrived 28 times for 12 graphs. The
terms fight each other: the gauge is written only by an interactive
session's statusline, so a fresh gauge means someone is working, which is
when the usage gate is closed; and when nobody is working the laptop is
suspended, so the blind night window aims at hours that mostly do not exist.

This module fires on the one signal that genuinely correlates with
opportunity: a prompt arriving. The server already sees every prompt (the
UserPromptSubmit hook posts it for recall), already holds both live graphs,
and already knows which nodes the live session has in context. So it can
decide "one small chore is due here, on these two nodes" and launch a
detached headless agent to do it — costing the live session no context and
requiring no cooperation from the model running it.

Off unless switched on. Spawning agents spends the user's quota, so the
default is silence; ~/.knowledge-graph/chores.json {"enabled": true} or
KG_CHORES=1 turns it on. Every gate below fails closed, and every failure
path returns quietly: this runs on the hook's request thread, and a hook
must never slow or break a session.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from core.anchors import anchor_candidates, dangling_touches
from core.chores import (
    build_chore_prompt,
    build_pass_prompt,
    declined_ids,
    is_churning,
    pick_chore,
    recent_chore_targets,
)
from core.constants import (
    ANCHOR_RESOLVE_MAX_NODES,
    CHORE_CONFIG_NAME,
    CHORE_DEBT_FLOOR,
    CHORE_GAUGE_MAX_5H,
    CHORE_GAUGE_MAX_7D,
    CHORE_GAUGE_MAX_AGE_SECONDS,
    CHORE_GRAPH_COOLDOWN_SECONDS,
    CHORE_LESSONS_CHAR_BUDGET,
    CHORE_LESSONS_MAX,
    CHORE_LOG_MAX_BYTES,
    CHORE_LOG_NAME,
    CHORE_MAX_PER_DAY,
    CHORE_MIN_INTERVAL_SECONDS,
    CHORE_MODEL,
    CHORE_TASK_ID,
    CHORE_TIMEOUT_SECONDS,
    CODEX_ROLLOUT_TAIL_BYTES,
    LIFT_EDGE_REL,
    PASS_GAUGE_MAX_5H,
    PASS_GAUGE_MAX_7D,
    PASS_INTERVAL_DAYS,
    PASS_MAX_PER_DAY,
    PASS_MIN_ACTIVE_NODES,
    PASS_PACE_MAX,
    PASS_TIMEOUT_SECONDS,
    PROGRESS_TRAIL_KEY,
    get_storage_root,
    project_namespace,
)
from core.debt import MAINTAIN_TASK_ID, activity_days, compute_debt
from core.persistence import append_jsonl

logger = logging.getLogger(__name__)

STATE_NAME = "chore_state.json"
# The scoped allowlist the chore runs under ships WITH the plugin, and that is
# deliberate. The previous dispatcher kept its settings in ~/.config, outside
# the repo, and drifted: the file never gained kg_rename_node after v0.9.35
# added it, so the one pass that tried id work recorded ids_renamed: 0. A
# template versioned alongside the prompt that uses it cannot drift from it.
SHIPPED_SETTINGS = Path(__file__).resolve().parents[2] / "chores" / "settings.json"
# The pass tier needs the delete tools a merge requires; the chore tier must
# not have them, so they live in separate files rather than one permissive one.
SHIPPED_PASS_SETTINGS = Path(__file__).resolve().parents[2] / "chores" / "pass-settings.json"

_lock = threading.Lock()
_running = threading.Event()      # at most one chore process at a time
_config_cache: dict = {"mtime": None, "data": {}}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def _config() -> dict:
    """Chore config, re-read when the file changes. Never raises."""
    path = get_storage_root() / CHORE_CONFIG_NAME
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if mtime != _config_cache["mtime"]:
        data = {}
        if mtime is not None:
            try:
                data = json.loads(path.read_text()) or {}
            except Exception:
                logger.warning("chores.json unreadable — chores stay off")
                data = {}
        _config_cache["mtime"] = mtime
        _config_cache["data"] = data
    cfg = dict(_config_cache["data"])
    if os.getenv("KG_CHORES"):
        cfg["enabled"] = os.getenv("KG_CHORES") not in ("0", "false", "no", "")
    return cfg


def _enabled(cfg: dict) -> bool:
    return bool(cfg.get("enabled"))


def enabled() -> bool:
    """Cheap inline gate for the request path — no disk beyond an mtime stat.

    Everyone who has not opted in pays exactly this much per prompt: one stat
    of a file that does not exist, and no thread.
    """
    return _enabled(_config())


def _settings_path(cfg: dict, tier: str = "chore") -> str | None:
    key, shipped = (("pass_settings", SHIPPED_PASS_SETTINGS) if tier == "pass"
                    else ("settings", SHIPPED_SETTINGS))
    explicit = cfg.get(key)
    if explicit:
        return explicit if Path(explicit).exists() else None
    return str(shipped) if shipped.exists() else None


# Variables Claude Code sets for its child processes: when the server was
# started from inside a session, these name that session, not the chore.
_LAUNCHER_ENV_PREFIX = "CLAUDE_CODE_"
_LAUNCHER_ENV_KEYS = frozenset({"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT"})


def chore_env(base: dict | None = None) -> dict:
    """The server's environment minus the session that launched it; user
    settings re-apply anything legitimate. KG_CHORE=1 stands this plugin's
    own hooks down inside the run (no preload, no recall, no re-dispatch),
    in any harness: its hooks inherit the variable from the runner.
    """
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items()
           if not k.startswith(_LAUNCHER_ENV_PREFIX) and k not in _LAUNCHER_ENV_KEYS}
    env["KG_CHORE"] = "1"
    return env


# --------------------------------------------------------------------------
# Runners: the harness that runs the agent, and whose quota it spends
# --------------------------------------------------------------------------
#
# Selection and gating are harness-neutral; a runner owns the three things
# that are not: finding its binary, the command that runs a prompt headless
# with only the kg tools, and the quota gauge. The gauge belongs to the
# runner, not to the harness that sent the prompt: a chore launched through
# Codex spends the ChatGPT plan's windows and is gated on them, never on
# Claude's. Every gauge returns the same keys as ~/.claude/last-limits.json
# (five_hour_pct, seven_day_pct, seven_day_resets_at, updated_at), which is
# what the gates below read.

def chore_command(claude_bin: str, model: str, settings: str) -> list[str]:
    """A chore is system-wide: user settings only (the plugin lives there),
    nothing from any project's .claude/. The --settings allowlist applies
    on top regardless of sources."""
    return [claude_bin, "-p", "--model", model, "--setting-sources", "user",
            "--settings", settings]


class ClaudeRunner:
    name = "claude"

    def binary(self, cfg: dict) -> str | None:
        explicit = cfg.get("claude_bin")
        if explicit:
            return explicit if Path(explicit).exists() else None
        default = Path.home() / ".local/bin/claude"
        if default.exists():
            return str(default)
        return shutil.which("claude")

    def gauge(self, cfg: dict, now: float) -> dict:
        """The status line's snapshot; raises when it cannot be read."""
        path = Path(os.path.expanduser(cfg.get("limits", "~/.claude/last-limits.json")))
        return json.loads(path.read_text())

    def command(self, cfg: dict, job: dict) -> list[str]:
        return chore_command(job["bin"], cfg.get("model", CHORE_MODEL), job["settings"])


# The shipped allowlists name tools the way Claude Code does; Codex takes the
# bare MCP tool names. One file per tier stays the single source for both.
_CLAUDE_TOOL_PREFIX = "mcp__plugin_knowledge-graph_kg__"


def allowed_tools(settings_path: str) -> list[str]:
    """Bare kg tool names a settings file allows."""
    allow = (json.loads(Path(settings_path).read_text()).get("permissions") or {}).get("allow") or []
    return [t[len(_CLAUDE_TOOL_PREFIX):] for t in allow if t.startswith(_CLAUDE_TOOL_PREFIX)]


def codex_limits(rollout: Path) -> dict | None:
    """The last rate-limit reading in a Codex rollout, as gauge keys.

    Codex writes `token_count` events carrying the plan's windows after each
    model response; `window_minutes` says which window is which. A window
    whose reset has passed has emptied, whatever it last read.
    """
    size = rollout.stat().st_size
    with open(rollout, "rb") as f:
        f.seek(max(0, size - CODEX_ROLLOUT_TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        if '"rate_limits"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        limits = (event.get("payload") or {}).get("rate_limits")
        if not limits:
            continue
        out = {"updated_at": _iso_ts(event.get("timestamp")) or rollout.stat().st_mtime}
        for window in (limits.get("primary"), limits.get("secondary")):
            if not window:
                continue
            minutes = window.get("window_minutes")
            key = {300: "five_hour", 10080: "seven_day"}.get(minutes)
            if not key:
                continue
            pct, resets = window.get("used_percent"), window.get("resets_at")
            if resets and resets < time.time():
                pct = 0.0
            out[f"{key}_pct"] = pct
            out[f"{key}_resets_at"] = resets
        return out
    return None


def _iso_ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def newest_rollout(home: Path, days: int = 3) -> Path | None:
    """Most recently written rollout in the last few day directories.

    Rollouts live under sessions/YYYY/MM/DD/, one file per session; only the
    newest days can hold a fresh reading, so the walk stays small however
    long the history is.
    """
    root = home / "sessions"
    try:
        day_dirs = sorted((d for y in root.iterdir() if y.is_dir()
                           for m in y.iterdir() if m.is_dir()
                           for d in m.iterdir() if d.is_dir()), reverse=True)[:days]
    except OSError:
        return None
    files = [f for d in day_dirs for f in d.glob("rollout-*.jsonl")]
    return max(files, key=lambda f: f.stat().st_mtime, default=None)


class CodexRunner:
    name = "codex"

    def binary(self, cfg: dict) -> str | None:
        explicit = cfg.get("codex_bin")
        if explicit:
            return explicit if Path(explicit).exists() else None
        return shutil.which("codex")

    def gauge(self, cfg: dict, now: float) -> dict:
        rollout = newest_rollout(codex_home())
        data = codex_limits(rollout) if rollout else None
        if not data:
            raise ValueError("no Codex rate-limit reading")
        return data

    def command(self, cfg: dict, job: dict) -> list[str]:
        """codex exec with no shell, no web and only the tier's kg tools.

        --ignore-user-config keeps the user's own model, effort and plugins
        (whose skills and hooks a chore does not need) out of the run; auth
        is not config and still applies. The kg server is named here rather
        than inherited, and its tools pre-approved, because exec has no one
        to approve a call. The prompt arrives on stdin.
        """
        host = os.getenv("KG_HTTP_HOST", "127.0.0.1")
        port = os.getenv("KG_HTTP_PORT", "8765")
        effort = cfg.get("codex_reasoning_effort",
                         "medium" if job["tier"] == "pass" else "low")
        cmd = [job["bin"], "exec", "--ephemeral", "--ignore-user-config",
               "--skip-git-repo-check", "-s", "read-only", "--disable", "shell_tool",
               "-c", 'web_search="disabled"',
               "-c", f'mcp_servers.kg.url="http://{host}:{port}/"',
               "-c", f"mcp_servers.kg.enabled_tools={json.dumps(allowed_tools(job['settings']))}",
               "-c", 'mcp_servers.kg.default_tools_approval_mode="approve"',
               "-c", f'model_reasoning_effort="{effort}"']
        if cfg.get("codex_model"):
            cmd += ["-m", cfg["codex_model"]]
        return cmd


RUNNERS = {r.name: r for r in (ClaudeRunner(), CodexRunner())}


def _runner(cfg: dict):
    """The configured runner; "auto" (the default) prefers Claude Code, as
    before this choice existed, and falls back to Codex when only Codex is
    installed. A binary named in the config pins its runner even when the
    path is wrong: that surfaces as "binary missing", where falling through
    would silently spend a different subscription's quota."""
    choice = cfg.get("runner", "auto")
    if choice in RUNNERS:
        return RUNNERS[choice]
    for runner in RUNNERS.values():
        if cfg.get(f"{runner.name}_bin") or runner.binary(cfg):
            return runner
    return RUNNERS["claude"]


# --------------------------------------------------------------------------
# Dispatcher state (survives a restart; the cooldowns are the whole safety net)
# --------------------------------------------------------------------------

def _state_path() -> Path:
    return get_storage_root() / STATE_NAME


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {"last_ts": 0, "graphs": {}, "day": "", "count": 0}


def _too_soon(state: dict, cfg: dict, now: float) -> bool:
    return now - (state.get("last_ts") or 0) < cfg.get(
        "min_interval_s", CHORE_MIN_INTERVAL_SECONDS)


def _roll_day(state: dict, now: float) -> None:
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    if state.get("day") != today:
        state["day"], state["count"], state["pass_count"] = today, 0, 0


def _cap_reached(state: dict, cfg: dict, tier: str) -> bool:
    if tier == "pass":
        return state.get("pass_count", 0) >= cfg.get("pass_max_per_day", PASS_MAX_PER_DAY)
    return state.get("count", 0) >= cfg.get("max_per_day", CHORE_MAX_PER_DAY)


def _write_state(state: dict) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, path)
    except Exception:
        logger.debug("chore state write failed", exc_info=True)


def _log(record: dict) -> None:
    """One JSON line per decision — dispatches AND refusals.

    The refusals are the useful half: without them the only visible fact is
    that nothing ran, which is exactly the state the old dispatcher was in
    for weeks before anyone measured why.
    """
    append_jsonl(get_storage_root() / CHORE_LOG_NAME,
                 {"ts": round(time.time(), 3), **record}, CHORE_LOG_MAX_BYTES)


# --------------------------------------------------------------------------
# Quota gauge — same reasoning as the tick script, stricter thresholds
# --------------------------------------------------------------------------

def weekly_pace(data: dict, now: float) -> float | None:
    """Weekly usage against the linear burn that would end the week at 100%.

    < 1.0 means the week is running under pace and will leave quota unspent —
    and unspent weekly quota is simply lost at the reset. The 5h gauge is what
    the day's own work needs; the weekly allowance is the one that routinely
    goes to waste, so it is what funds the expensive tier.

    Self-adjusting, which is why there is no day-of-week rule anywhere: early
    in the week a real working day puts usage above the line and the pass
    waits, while a quiet week drifts further under it every day, concentrating
    firings near the reset by arithmetic rather than by calendar.
    """
    resets = data.get("seven_day_resets_at")
    pct = data.get("seven_day_pct")
    if not resets or pct is None:
        return None
    window = 7 * 86400
    elapsed = 1.0 - (resets - now) / window
    if elapsed <= 0.02:          # the first ~3 hours divide by ~nothing
        return None
    return (pct / 100.0) / min(1.0, elapsed)


def _gauge_read(cfg: dict, now: float, runner) -> tuple[dict, dict | None, str]:
    """(reading, raw, error). Both tiers need a FRESH gauge.

    The tick script tolerates a stale reading because waiting for a fresh one
    overnight means never running. An activity-triggered run has the opposite
    problem: it fires while someone is working, so a fresh reading is the
    normal case and a stale one means the statusline is not rendering — which
    is precisely when spending blind would land on the user's own session.
    """
    try:
        data = runner.gauge(cfg, now)
    except Exception:
        return {}, None, "gauge unreadable"
    age = now - (data.get("updated_at") or 0)
    pace = weekly_pace(data, now)
    reading = {
        "5h": data.get("five_hour_pct"),
        "7d": data.get("seven_day_pct"),
        "pace": round(pace, 2) if pace is not None else None,
        "age_s": round(age),
    }
    if age > cfg.get("gauge_max_age_s", CHORE_GAUGE_MAX_AGE_SECONDS):
        return reading, None, "gauge stale"
    return reading, data, ""


def _chore_gauge_ok(cfg: dict, raw: dict) -> str:
    """"" when a chore may spend, else the gate that refused."""
    p5, p7 = raw.get("five_hour_pct"), raw.get("seven_day_pct")
    if p5 is None or p5 >= cfg.get("max_5h", CHORE_GAUGE_MAX_5H):
        return f"5h {p5}%"
    if p7 is not None and p7 >= cfg.get("max_7d", CHORE_GAUGE_MAX_7D):
        return f"7d {p7}%"
    return ""


def _pass_gauge_ok(cfg: dict, raw: dict, now: float) -> str:
    """"" when a pass may spend, else the gate that refused.

    Tighter on the 5h window than a chore (a pass runs for minutes, not
    seconds), and funded by the weekly surplus rather than by absolute
    headroom — with an absolute backstop, because at 6.5 days elapsed the
    linear line sits at 93% and would otherwise wave through a spent week.
    """
    p5, p7 = raw.get("five_hour_pct"), raw.get("seven_day_pct")
    if p5 is None or p5 >= cfg.get("pass_max_5h", PASS_GAUGE_MAX_5H):
        return f"pass: 5h {p5}%"
    if p7 is not None and p7 >= cfg.get("pass_max_7d", PASS_GAUGE_MAX_7D):
        return f"pass: 7d {p7}%"
    pace = weekly_pace(raw, now)
    if pace is None:
        return "pass: weekly pace unknown"
    if pace > cfg.get("pass_pace_max", PASS_PACE_MAX):
        return f"pass: week running at {pace:.2f}x pace, no surplus"
    return ""


# --------------------------------------------------------------------------
# Target selection
# --------------------------------------------------------------------------

def _ensure_loaded(store, project_path: str | None) -> None:
    """Load a project graph that lazy loading has not reached yet.

    Without this the whole decision runs on an empty graph: no nodes, no debt,
    and — the dangerous one — no progress, so `_days_since_pass` reads
    "never passed" and every server restart would look like a graph overdue
    for a full pass. Seen live on the first pass-tier dry run.
    """
    if not project_path:
        return
    try:
        with store.lock:
            store._ensure_project_loaded(project_path)
    except Exception:
        logger.debug("could not load project graph for %s", project_path, exc_info=True)


def _graph_debt(store, graph_key: str, now: float,
                project_path: str | None = None) -> tuple[dict, list, list]:
    """(debt, nodes, edges) for a loaded graph. Caller holds no lock.

    project_path: the project root its touches resolve against; None for the
    user graph, whose anchors resolve against home only.
    """
    with store.lock:
        graph = store.graphs.get(graph_key)
        if not graph:
            return {}, [], []
        nodes = [dict(n) for n in graph["nodes"].values()]
        edges = [dict(e) for e in graph["edges"].values()]
        progress = store._progress.get(graph_key, {})
    last_maintain = (progress.get(MAINTAIN_TASK_ID) or {}).get("last_ts")
    ts_pool = [n.get("_last_read_ts") for n in nodes]
    debt = compute_debt(nodes, edges, last_maintain,
                        activity_days(ts_pool, now=now), now=now,
                        project_root=project_path, home=Path.home())
    return debt, nodes, edges


def _trails(store, graph_key: str) -> tuple[list, list]:
    with store.lock:
        progress = store._progress.get(graph_key, {})
    chore = (progress.get(CHORE_TASK_ID) or {}).get(PROGRESS_TRAIL_KEY) or []
    maintain = (progress.get(MAINTAIN_TASK_ID) or {}).get(PROGRESS_TRAIL_KEY) or []
    return list(chore), list(maintain)


def _live_seen(session_manager, max_age_s: int = 4 * 3600) -> set | None:
    """Node ids any recently-active session holds in context; None if unknown.

    Not a blanket veto — see core.chores: a session that has done the loud
    full read holds EVERY active node, so barring all of them would refuse
    every chore in the project actually being worked on. It bars renames
    (which turn the session's id into a NOT FOUND) and demotes the rest.
    Unknown is not empty: an empty set would clear every node for renaming,
    so the caller refuses to dispatch instead.
    """
    try:
        return session_manager.recently_seen_ids(max_age_s)
    except Exception:
        logger.debug("live-context read failed", exc_info=True)
        return None


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def _spawn(cfg: dict, job: dict, prompt: str, store) -> None:
    """Launch the agent detached and watch it from one thread.

    Detached (start_new_session) so a server restart never orphans a
    half-finished run into the server's own process group, and watched so the
    outcome — return code, duration, and the debt it actually moved — reaches
    the log. Without the watcher the process would also linger as a zombie
    until the server exits.
    """
    cmd = RUNNERS[job["runner"]].command(cfg, job)
    # Run from the store directory, not the project: the prompt names the
    # graph via kg_read(cwd=...), and a project dir would still supply
    # CLAUDE.md and .mcp.json, which --setting-sources does not govern.
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, cwd=str(get_storage_root()),
            start_new_session=True, env=chore_env(),
        )
    except Exception as e:
        _running.clear()
        _log({"event": "spawn_failed", "tier": job["tier"],
              "graph": job["graph"], "error": str(e)})
        return

    def watch():
        started = time.time()
        rc, tail = None, ""
        try:
            out, _ = proc.communicate(prompt, timeout=job["timeout_s"])
            rc = proc.returncode
            tail = (out or "")[-600:]
        except subprocess.TimeoutExpired:
            # The run leads its own session: kill the group, or whatever the
            # agent started (MCP clients, helpers) outlives it.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            try:
                proc.communicate(timeout=10)
            except Exception:
                pass
            rc, tail = -9, "timeout"
        except Exception as e:
            rc, tail = -1, str(e)[:600]
        finally:
            _running.clear()
        try:
            debt_after, _n, _e = _graph_debt(store, job["graph"], time.time(),
                                             job.get("project_path"))
        except Exception:
            debt_after = {}
        record = {
            "event": "done", "tier": job["tier"], "runner": job["runner"], "graph": job["graph"],
            "level": job["level"], "kind": job.get("kind"),
            "targets": job.get("targets"), "rc": rc,
            "elapsed_s": round(time.time() - started, 1),
            "debt_before": job["debt_before"], "debt_after": debt_after.get("score"),
            "tail": tail.strip().replace("\n", " ")[-400:],
        }
        if job.get("kind") == "lift":
            try:
                record["outcome"] = lift_outcome(store, job)
            except Exception:
                logger.debug("lift outcome not read", exc_info=True)
        _log(record)

    threading.Thread(target=watch, daemon=True, name="kg-run-watch").start()


def _lift_edges(store, graph_key: str, members) -> list[str]:
    """Keys of the member -> principle edges that exist right now."""
    members = set(members or [])
    with store.lock:
        graph = store.graphs.get(graph_key) or {"edges": {}}
        return sorted(f"{e['from']}->{e['to']}" for e in graph["edges"].values()
                      if e.get("rel") == LIFT_EDGE_REL and e.get("from") in members)


def lift_outcome(store, job: dict) -> dict:
    """What a lift chore actually did, as the server can observe it.

    Every lift decision lands in chores.jsonl, so an audit can line a lift up
    against the endorsement and recall logs and ask whether the touched nodes
    fared worse afterwards — the "maintenance shock" AgingBench measures
    (docs/research/synthesis.md, open question 4). The chore's own stamp says
    what it meant to do; the edges say what it did, and a stamp can be
    missing when a run is cut short.
    """
    graph_key = job["graph"]
    before = set(job.get("lift_edges_before") or [])
    new_edges = [k for k in _lift_edges(store, graph_key, job.get("targets"))
                 if k not in before]
    principles = sorted({k.split("->", 1)[1] for k in new_edges})
    since = job.get("dispatched_ts") or 0
    with store.lock:
        nodes = (store.graphs.get(graph_key) or {"nodes": {}})["nodes"]
        created = [p for p in principles
                   if (nodes.get(p) or {}).get("_created_ts", 0) >= since]
        trail = ((store._progress.get(graph_key, {}).get(CHORE_TASK_ID) or {})
                 .get(PROGRESS_TRAIL_KEY) or [])
    stamp = next((dict(e) for e in reversed(trail)
                  if (e.get("_ts") or 0) >= since and e.get("kind") == "lift"), None)
    return {
        "principles": principles,
        "created": created,
        "linked": sorted({k.split("->", 1)[0] for k in new_edges}),
        "stamp": ({k: stamp.get(k) for k in ("done", "principle", "declined")}
                  if stamp else None),
    }


def _days_since_pass(store, graph_key: str, now: float) -> float:
    """Days since the last STAMPED full pass — the question debt cannot answer.

    Debt measures whether the graph is correct, never whether structural work
    is waiting. Chores drive the deficit term to nothing, and a graph with no
    countable wear caps at the formula's own constant 0.25 — permanently under
    the 0.3 the scheduled dispatcher selects on. Left on debt alone, a
    well-chored graph would never see a pass again, and entity consolidation
    and duplicate merges would never happen on it.
    """
    with store.lock:
        progress = store._progress.get(graph_key, {})
    last = (progress.get(MAINTAIN_TASK_ID) or {}).get("last_ts")
    if not last:
        return float("inf")      # never passed
    return (now - last) / 86400


def _pick_target(store, session_manager, state, cfg, now, candidates):
    """(tier, payload) for the neediest live graph, or (None, reason)."""
    floor = cfg.get("debt_floor", CHORE_DEBT_FLOOR)
    cooldown = cfg.get("graph_cooldown_s", CHORE_GRAPH_COOLDOWN_SECONDS)
    interval_days = cfg.get("pass_interval_days", PASS_INTERVAL_DAYS)
    held = _live_seen(session_manager)
    if held is None:
        return None, "live context unknown"

    # The pass tier goes first: it is rarer, it is the only thing that does the
    # structural categories, and it resets the staleness the chores cannot.
    for _level, _graph_key, ppath in candidates:
        _ensure_loaded(store, ppath)

    due = []
    for level, graph_key, ppath in candidates:
        debt, nodes, _edges = _graph_debt(store, graph_key, now, ppath)
        # A graph too small to have structure does not repay a full pass.
        if not debt or debt.get("active_nodes", 0) < PASS_MIN_ACTIVE_NODES:
            continue
        age = _days_since_pass(store, graph_key, now)
        if age >= interval_days:
            due.append((age, level, graph_key, ppath, debt, {n["id"] for n in nodes}))
    if due:
        due.sort(key=lambda r: -r[0])
        age, level, graph_key, ppath, debt, ids = due[0]
        # The pass agent picks its own rename and merge targets, so the rule
        # the chore tier enforces in pick_chore has to travel in the prompt.
        return "pass", {"level": level, "graph": graph_key, "project_path": ppath,
                        "debt": debt, "held": sorted(held & ids),
                        "days_since_pass": None if age == float("inf") else round(age, 1)}

    scored = []
    for level, graph_key, ppath in candidates:
        last = (state.get("graphs") or {}).get(graph_key, 0)
        if now - last < cooldown:
            continue
        debt, nodes, edges = _graph_debt(store, graph_key, now, ppath)
        if not debt or debt["score"] < floor:
            continue
        scored.append((debt["score"], level, graph_key, ppath, debt, nodes, edges))
    if not scored:
        return None, "no graph above floor or all on cooldown"

    scored.sort(key=lambda r: -r[0])
    _score, level, graph_key, ppath, debt, nodes, edges = scored[0]
    chore_trail, maintain_trail = _trails(store, graph_key)
    # Anchor candidates walk the project tree and ask git, which is why they
    # are found here, off the request thread, and only for this one graph.
    anchors = {}
    if ppath:
        try:
            dangling = dangling_touches(nodes, ppath, Path.home())
            # The resolve cap must not be spent on nodes no chore may take.
            skip = recent_chore_targets(chore_trail) | declined_ids(maintain_trail, dangling)
            skip |= {n["id"] for n in nodes if n["id"] in dangling and is_churning(n, now)}
            dangling = {k: v for k, v in dangling.items() if k not in skip}
            anchors = anchor_candidates(dangling, ppath, Path.home(),
                                        max_nodes=ANCHOR_RESOLVE_MAX_NODES)
        except Exception:
            logger.debug("anchor discovery failed", exc_info=True)
    chore = pick_chore(nodes, edges, in_context=held,
                       chore_trail=chore_trail, maintain_trail=maintain_trail,
                       anchors=anchors, now=now)
    if not chore:
        return None, f"no eligible target in {graph_key} (debt {debt['score']})"
    chore.level = level
    chore.graph = graph_key
    chore.project_path = ppath
    chore.debt = debt["score"]
    return "chore", chore


def maybe_dispatch(store, session_manager, project_path: str | None) -> None:
    """Consider one run for this prompt. Fast, silent, never raises.

    Called from the prompt hook's request thread, so every gate that can be
    decided without touching disk is decided first.
    """
    try:
        cfg = _config()
        if not _enabled(cfg):
            return
        if _running.is_set():
            return

        now = time.time()
        state = _read_state()
        if _too_soon(state, cfg, now):
            return

        # Past this point the moment is eligible, so refusals are worth a line:
        # they are the record of WHY nothing ran, which is the question the old
        # dispatcher could not answer for weeks.
        _roll_day(state, now)

        runner = _runner(cfg)
        reading, raw, err = _gauge_read(cfg, now, runner)
        if err:
            _log({"event": "skip", "reason": err, "runner": runner.name, "gauge": reading})
            return

        # Candidates: the two graphs already in memory for this session. An
        # activity-triggered run deliberately never reaches for a graph nobody
        # is using — the scheduled timer still covers dormant ones, and the
        # in-context rules that keep a run off the user's working nodes only
        # exist for live sessions.
        candidates = [("user", "user", None)]
        if project_path:
            candidates.append(("project", project_namespace(project_path), project_path))

        tier, payload = _pick_target(store, session_manager, state, cfg, now, candidates)
        if not tier:
            _log({"event": "skip", "reason": payload})
            return

        if tier == "pass":
            if _cap_reached(state, cfg, tier):
                _log({"event": "skip", "reason": "daily pass cap",
                      "graph": payload["graph"]})
                return
            gate = _pass_gauge_ok(cfg, raw, now)
            if gate:
                _log({"event": "skip", "reason": gate, "gauge": reading,
                      "graph": payload["graph"],
                      "days_since_pass": payload["days_since_pass"]})
                return
        else:
            if _cap_reached(state, cfg, tier):
                _log({"event": "skip", "reason": "daily chore cap",
                      "count": state["count"]})
                return
            gate = _chore_gauge_ok(cfg, raw)
            if gate:
                _log({"event": "skip", "reason": gate, "gauge": reading})
                return

        binary = runner.binary(cfg)
        settings = _settings_path(cfg, tier)
        if not binary or not settings:
            _log({"event": "skip", "reason": f"{runner.name} binary or settings missing",
                  "tier": tier, "bin": binary, "settings": settings})
            return

        level = payload["level"] if tier == "pass" else payload.level
        graph_key = payload["graph"] if tier == "pass" else payload.graph
        ppath = payload["project_path"] if tier == "pass" else payload.project_path
        cwd = ppath or str(Path.home())    # names the graph in the prompt only
        if not Path(cwd).is_dir():
            _log({"event": "skip", "reason": "project gone", "project": cwd})
            return

        try:
            lessons = store.maintain_lessons()[:cfg.get("lessons_max", CHORE_LESSONS_MAX)]
        except Exception:
            lessons = []

        if tier == "pass":
            prompt = build_pass_prompt(level, cwd, payload["debt"], lessons=lessons,
                                       lessons_budget=CHORE_LESSONS_CHAR_BUDGET,
                                       held=payload.get("held", ()))
            job = {"tier": "pass", "kind": "pass", "targets": None,
                   "debt_before": (payload["debt"] or {}).get("score"),
                   "timeout_s": cfg.get("pass_timeout_s", PASS_TIMEOUT_SECONDS)}
            record = {"days_since_pass": payload["days_since_pass"],
                      "held": len(payload.get("held", ())),
                      "smeared": [x["term"] for x in (payload["debt"] or {}).get("smeared", [])]}
        else:
            prompt = build_chore_prompt(payload, cwd, lessons=lessons,
                                        lessons_budget=CHORE_LESSONS_CHAR_BUDGET)
            job = {"tier": "chore", "kind": payload.kind, "targets": payload.targets,
                   "debt_before": payload.debt,
                   "timeout_s": cfg.get("timeout_s", CHORE_TIMEOUT_SECONDS)}
            record = {"pool": payload.pool}
            if payload.kind == "lift":
                record["cluster"] = {"members": payload.targets,
                                     "evidence": payload.context.get("evidence")}
                job["lift_edges_before"] = _lift_edges(store, payload.graph,
                                                       payload.targets)
            elif payload.kind == "anchor":
                record["anchors"] = {
                    nid: [{k: r.get(k) for k in ("entry", "verdict", "replacement")}
                          for r in recs]
                    for nid, recs in (payload.context.get("anchors") or {}).items()}
        job.update({"level": level, "graph": graph_key, "project": cwd,
                    "project_path": ppath, "dispatched_ts": now,
                    "runner": runner.name, "bin": binary, "settings": settings})

        with _lock:
            if _running.is_set():
                return
            # Everything above decided on a snapshot taken before target
            # selection; another prompt may have dispatched since. Re-decide
            # on fresh state, or two runs land inside min_interval and the
            # second write loses the first one's count.
            state = _read_state()
            _roll_day(state, now)
            if _too_soon(state, cfg, now) or _cap_reached(state, cfg, tier):
                _log({"event": "skip", "reason": "a concurrent dispatch won",
                      "tier": tier, "graph": graph_key})
                return
            _running.set()
            state["last_ts"] = now
            if tier == "pass":
                state["pass_count"] = state.get("pass_count", 0) + 1
                state.setdefault("passes", {})[graph_key] = now
            else:
                state.setdefault("graphs", {})[graph_key] = now
                state["count"] = state.get("count", 0) + 1
            _write_state(state)

        _log({"event": "dispatch", "tier": tier, "runner": runner.name,
              "graph": graph_key, "level": level,
              "kind": job["kind"], "targets": job["targets"],
              "debt": job["debt_before"], "lessons": len(lessons),
              "gauge": reading, **record})
        _spawn(cfg, job, prompt, store)
    except Exception:
        logger.debug("dispatch failed", exc_info=True)
