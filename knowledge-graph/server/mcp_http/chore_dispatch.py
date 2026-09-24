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
import subprocess
import threading
import time
from pathlib import Path

from core.chores import build_chore_prompt, build_pass_prompt, pick_chore
from core.constants import (
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


def _claude_bin(cfg: dict) -> str | None:
    explicit = cfg.get("claude_bin")
    if explicit:
        return explicit if Path(explicit).exists() else None
    default = Path.home() / ".local/bin/claude"
    if default.exists():
        return str(default)
    return shutil.which("claude")


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


def chore_command(claude_bin: str, model: str, settings: str) -> list[str]:
    """A chore is system-wide: user settings only (the plugin lives there),
    nothing from any project's .claude/. The --settings allowlist applies
    on top regardless of sources."""
    return [claude_bin, "-p", "--model", model, "--setting-sources", "user",
            "--settings", settings]


def chore_env(base: dict | None = None) -> dict:
    """The server's environment minus the session that launched it; user
    settings re-apply anything legitimate. KG_CHORE=1 stands this plugin's
    own hooks down inside the run (no preload, no recall, no re-dispatch).
    """
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items()
           if not k.startswith(_LAUNCHER_ENV_PREFIX) and k not in _LAUNCHER_ENV_KEYS}
    env["KG_CHORE"] = "1"
    return env


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


def _gauge_read(cfg: dict, now: float) -> tuple[dict, dict | None, str]:
    """(reading, raw, error). Both tiers need a FRESH gauge.

    The tick script tolerates a stale reading because waiting for a fresh one
    overnight means never running. An activity-triggered run has the opposite
    problem: it fires while someone is working, so a fresh reading is the
    normal case and a stale one means the statusline is not rendering — which
    is precisely when spending blind would land on the user's own session.
    """
    path = Path(os.path.expanduser(cfg.get("limits", "~/.claude/last-limits.json")))
    try:
        data = json.loads(path.read_text())
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


def _graph_debt(store, graph_key: str, now: float) -> tuple[dict, list, list]:
    """(debt, nodes, edges) for a loaded graph. Caller holds no lock."""
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
                        activity_days(ts_pool, now=now), now=now)
    return debt, nodes, edges


def _trails(store, graph_key: str) -> tuple[list, list]:
    with store.lock:
        progress = store._progress.get(graph_key, {})
    chore = (progress.get(CHORE_TASK_ID) or {}).get(PROGRESS_TRAIL_KEY) or []
    maintain = (progress.get(MAINTAIN_TASK_ID) or {}).get(PROGRESS_TRAIL_KEY) or []
    return list(chore), list(maintain)


def _live_seen(session_manager, max_age_s: int = 4 * 3600) -> set:
    """Node ids any recently-active session holds in context.

    Not a blanket veto — see core.chores: a session that has done the loud
    full read holds EVERY active node, so barring all of them would refuse
    every chore in the project actually being worked on. It bars renames
    (which turn the session's id into a NOT FOUND) and demotes the rest.
    """
    try:
        return session_manager.recently_seen_ids(max_age_s)
    except Exception:
        return set()


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
    cmd = chore_command(job["claude_bin"], cfg.get("model", CHORE_MODEL), job["settings"])
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
            debt_after, _n, _e = _graph_debt(store, job["graph"], time.time())
        except Exception:
            debt_after = {}
        _log({
            "event": "done", "tier": job["tier"], "graph": job["graph"],
            "level": job["level"], "kind": job.get("kind"),
            "targets": job.get("targets"), "rc": rc,
            "elapsed_s": round(time.time() - started, 1),
            "debt_before": job["debt_before"], "debt_after": debt_after.get("score"),
            "tail": tail.strip().replace("\n", " ")[-400:],
        })

    threading.Thread(target=watch, daemon=True, name="kg-run-watch").start()


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

    # The pass tier goes first: it is rarer, it is the only thing that does the
    # structural categories, and it resets the staleness the chores cannot.
    for _level, _graph_key, ppath in candidates:
        _ensure_loaded(store, ppath)

    due = []
    for level, graph_key, ppath in candidates:
        debt, nodes, _edges = _graph_debt(store, graph_key, now)
        # A graph too small to have structure does not repay a full pass.
        if not debt or debt.get("active_nodes", 0) < PASS_MIN_ACTIVE_NODES:
            continue
        age = _days_since_pass(store, graph_key, now)
        if age >= interval_days:
            due.append((age, level, graph_key, ppath, debt))
    if due:
        due.sort(key=lambda r: -r[0])
        age, level, graph_key, ppath, debt = due[0]
        return "pass", {"level": level, "graph": graph_key, "project_path": ppath,
                        "debt": debt,
                        "days_since_pass": None if age == float("inf") else round(age, 1)}

    scored = []
    for level, graph_key, ppath in candidates:
        last = (state.get("graphs") or {}).get(graph_key, 0)
        if now - last < cooldown:
            continue
        debt, nodes, edges = _graph_debt(store, graph_key, now)
        if not debt or debt["score"] < floor:
            continue
        scored.append((debt["score"], level, graph_key, ppath, debt, nodes, edges))
    if not scored:
        return None, "no graph above floor or all on cooldown"

    scored.sort(key=lambda r: -r[0])
    _score, level, graph_key, ppath, debt, nodes, edges = scored[0]
    chore_trail, maintain_trail = _trails(store, graph_key)
    chore = pick_chore(nodes, edges, in_context=_live_seen(session_manager),
                       chore_trail=chore_trail, maintain_trail=maintain_trail,
                       now=now)
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
        if now - (state.get("last_ts") or 0) < cfg.get(
                "min_interval_s", CHORE_MIN_INTERVAL_SECONDS):
            return

        # Past this point the moment is eligible, so refusals are worth a line:
        # they are the record of WHY nothing ran, which is the question the old
        # dispatcher could not answer for weeks.
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        if state.get("day") != today:
            state["day"], state["count"], state["pass_count"] = today, 0, 0

        reading, raw, err = _gauge_read(cfg, now)
        if err:
            _log({"event": "skip", "reason": err, "gauge": reading})
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
            if state.get("pass_count", 0) >= cfg.get("pass_max_per_day", PASS_MAX_PER_DAY):
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
            if state.get("count", 0) >= cfg.get("max_per_day", CHORE_MAX_PER_DAY):
                _log({"event": "skip", "reason": "daily chore cap",
                      "count": state["count"]})
                return
            gate = _chore_gauge_ok(cfg, raw)
            if gate:
                _log({"event": "skip", "reason": gate, "gauge": reading})
                return

        claude_bin = _claude_bin(cfg)
        settings = _settings_path(cfg, tier)
        if not claude_bin or not settings:
            _log({"event": "skip", "reason": "claude binary or settings missing",
                  "tier": tier, "claude_bin": claude_bin, "settings": settings})
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
                                       lessons_budget=CHORE_LESSONS_CHAR_BUDGET)
            job = {"tier": "pass", "kind": "pass", "targets": None,
                   "debt_before": (payload["debt"] or {}).get("score"),
                   "timeout_s": cfg.get("pass_timeout_s", PASS_TIMEOUT_SECONDS)}
            record = {"days_since_pass": payload["days_since_pass"],
                      "smeared": [x["term"] for x in (payload["debt"] or {}).get("smeared", [])]}
        else:
            prompt = build_chore_prompt(payload, cwd, lessons=lessons,
                                        lessons_budget=CHORE_LESSONS_CHAR_BUDGET)
            job = {"tier": "chore", "kind": payload.kind, "targets": payload.targets,
                   "debt_before": payload.debt,
                   "timeout_s": cfg.get("timeout_s", CHORE_TIMEOUT_SECONDS)}
            record = {"pool": payload.pool}
        job.update({"level": level, "graph": graph_key, "project": cwd,
                    "claude_bin": claude_bin, "settings": settings})

        with _lock:
            if _running.is_set():
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

        _log({"event": "dispatch", "tier": tier, "graph": graph_key, "level": level,
              "kind": job["kind"], "targets": job["targets"],
              "debt": job["debt_before"], "lessons": len(lessons),
              "gauge": reading, **record})
        _spawn(cfg, job, prompt, store)
    except Exception:
        logger.debug("dispatch failed", exc_info=True)
