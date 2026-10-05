"""kg doctor: every piece the memory depends on, one line each.

Read-only. A ✗ is something that silently weakens the memory (no hooks, a
stale plugin, no quota gauge); a – is a harness or option not in use. Each ✗
names its fix.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

try:
    from . import kg
except ImportError:  # run as a script from a checkout
    import kg

HOME = Path.home()
TOOLS = ("kg_read", "kg_search", "kg_put_node", "kg_put_edge", "kg_rename_node",
         "kg_sync", "kg_useful", "kg_progress", "kg_delete_node", "kg_delete_edge")
CODEX_HOOKS = ("session_start", "user_prompt_submit", "post_tool_use")
GAUGE_MAX_AGE = 6 * 3600


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def find_binary(name: str) -> str | None:
    found = shutil.which(name)
    local = HOME / ".local/bin" / name
    return found or (str(local) if local.exists() else None)


def age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 172800:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} days"


class Report:
    def __init__(self):
        self.failures = 0

    def section(self, title: str):
        print(f"\n{title}")

    def ok(self, text: str):
        print(f"  ✓ {text}")

    def bad(self, text: str, fix: str):
        self.failures += 1
        print(f"  ✗ {text}\n      → {fix}")

    def off(self, text: str):
        print(f"  – {text}")


def check_server(r: Report, want: str):
    r.section("Server")
    data = kg.health()
    if not data:
        cause = kg.BREADCRUMB.read_text().strip().splitlines()[1] if kg.BREADCRUMB.exists() else ""
        r.bad("not running" + (f" ({cause})" if cause else ""), "kg start")
        return
    running = data.get("version")
    if running == want:
        r.ok(f"running {running}, {data.get('active_sessions')} sessions")
    else:
        r.bad(f"running {running}, this kg is {want}", "kg restart")
    pid = kg.running_pid()
    args = kg.process_args(pid) if pid else ""
    if args and str(kg.SERVER_SCRIPT) not in args:
        r.bad(f"runs from another copy: {args.split()[-1]}",
              "kg restart (serves this kg's copy for every harness)")


def check_claude(r: Report, want: str):
    r.section("Claude Code")
    if not find_binary("claude"):
        r.off("not installed")
        return
    record = (load_json(HOME / ".claude/plugins/installed_plugins.json")
              .get("plugins", {}).get("knowledge-graph@maxim-plugins") or [{}])[0]
    have = record.get("version")
    if not have:
        r.bad("plugin not installed", "claude plugin install knowledge-graph@maxim-plugins")
        return
    if have == want:
        r.ok(f"plugin {have}")
    else:
        r.bad(f"plugin {have}, server {want}", "claude plugin update knowledge-graph@maxim-plugins")
    market = load_json(HOME / ".claude/plugins/known_marketplaces.json").get("maxim-plugins", {})
    if market.get("autoUpdate"):
        r.ok("marketplace auto-update on")
    else:
        r.bad("marketplace auto-update off: fixes never arrive",
              "/plugin → Marketplaces → maxim-plugins → Enable auto-update")
    perms = load_json(HOME / ".claude/settings.json").get("permissions") or {}
    allow = set(perms.get("allow") or [])
    prefix = "mcp__plugin_knowledge-graph_kg"
    if prefix in allow or f"{prefix}__*" in allow:
        missing = []
    else:
        missing = [t for t in TOOLS if f"{prefix}__{t}" not in allow]
    if not missing:
        r.ok("kg tools pre-approved for every project")
    elif perms.get("defaultMode") in ("auto", "bypassPermissions"):
        r.ok(f"kg tools approved by {perms['defaultMode']} mode ({len(missing)} not pre-approved for other modes)")
    else:
        r.bad(f"{len(missing)} kg tools ask on every call ({', '.join(missing[:3])}…)",
              "add them to permissions.allow in ~/.claude/settings.json (README: Enable Auto-Approval)")
    stale = sorted(a for a in allow if a.startswith(("mcp__plugin_memory_kg__", "mcp__memory__")))
    if stale:
        r.bad(f"{len(stale)} permissions name an old plugin ({stale[0]}…)",
              "remove them from ~/.claude/settings.json")
    gauge = load_json(HOME / ".claude/last-limits.json")
    seen = gauge.get("updated_at")
    if not seen:
        r.bad("no quota gauge: no budget notices, no Claude-run upkeep",
              "install the status line (recommended-setup/README.md)")
    elif time.time() - seen > GAUGE_MAX_AGE:
        r.ok(f"quota gauge last written {age(time.time() - seen)} ago (written while a session runs)")
    else:
        r.ok(f"quota gauge fresh ({gauge.get('five_hour_pct')}% 5h, {gauge.get('seven_day_pct')}% week)")


def check_codex(r: Report, want: str):
    r.section("Codex")
    if not find_binary("codex"):
        r.off("not installed")
        return
    cache = HOME / ".codex/plugins/cache/maxim-plugins/knowledge-graph"
    versions = sorted((p.name for p in cache.iterdir() if p.is_dir()), key=lambda v: [
        int(x) if x.isdigit() else 0 for x in v.split(".")]) if cache.is_dir() else []
    if not versions:
        r.bad("plugin not installed",
              "codex plugin marketplace add mironmax/claudecode-plugins; codex plugin add knowledge-graph@maxim-plugins")
        return
    have = versions[-1]
    if have == want:
        r.ok(f"plugin {have}")
    else:
        r.bad(f"plugin {have}, server {want}",
              "codex plugin marketplace upgrade maxim-plugins; codex plugin add knowledge-graph@maxim-plugins")
    try:
        config = (HOME / ".codex/config.toml").read_text()
    except OSError:
        config = ""
    untrusted = [h for h in CODEX_HOOKS
                 if f'"knowledge-graph@maxim-plugins:hooks/hooks.json:{h}:0:0"' not in config]
    if untrusted:
        r.bad(f"hooks not trusted: {', '.join(untrusted)} (no preload or recall)",
              "run /hooks in Codex and trust knowledge-graph, then start a new session")
    else:
        r.ok("hooks trusted (Codex asks again when their content changes)")


def check_antigravity(r: Report, want: str):
    r.section("Antigravity")
    if not find_binary("agy"):
        r.off("not installed")
        return
    installed = HOME / ".gemini/config/plugins/knowledge-graph"
    have = load_json(installed / ".claude-plugin/plugin.json").get("version")
    if not have:
        r.bad("plugin not installed", f"agy plugin install {kg.ROOT}")
        return
    if have == want:
        r.ok(f"plugin {have}")
    else:
        r.bad(f"plugin {have}, server {want}", f"agy plugin install {kg.ROOT}")
    allow = set((load_json(HOME / ".gemini/antigravity-cli/settings.json").get("permissions") or {})
                .get("allow") or [])
    if "mcp(*)" in allow or "mcp(knowledge-graph_kg/*)" in allow:
        missing = []
    else:
        missing = [t for t in TOOLS if not t.startswith("kg_delete")
                   and f"mcp(knowledge-graph_kg/{t})" not in allow]
    if missing:
        r.bad(f"{len(missing)} kg tools ask on every call; maintenance runs are refused",
              "add them to permissions.allow in ~/.gemini/antigravity-cli/settings.json (ANTIGRAVITY.md)")
    else:
        r.ok("kg tools granted")


def check_upkeep(r: Report):
    r.section("Maintenance")
    cfg = load_json(kg.STORAGE_ROOT / "chores.json")
    if not cfg.get("enabled"):
        r.off("background upkeep off (/kg-ops: Maintenance chores)")
        return
    last = None
    log = kg.STORAGE_ROOT / "chores.jsonl"
    try:
        for line in log.read_text().splitlines()[-2000:]:
            event = json.loads(line)
            if event.get("event") == "done":
                last = event
    except (OSError, ValueError):
        pass
    if last:
        r.ok(f"on, runner {cfg.get('runner', 'auto')}; last run {age(time.time() - last['ts'])} ago "
             f"({last.get('kind')} on {last.get('graph')}, rc {last.get('rc')})")
    else:
        r.ok(f"on, runner {cfg.get('runner', 'auto')}; no run recorded yet")


def check_storage(r: Report):
    r.section("Storage")
    root = kg.STORAGE_ROOT
    if not root.is_dir():
        r.off(f"{root} not created yet (first session creates it)")
        return
    if (root / ".git").is_dir():
        r.ok(f"{root}, versioned with git")
    else:
        r.off(f"{root}, no version history (README: Versioned history and external backups)")


def run() -> int:
    want = kg.version()
    print(f"kg {want} — {kg.ROOT}")
    r = Report()
    check_server(r, want)
    check_claude(r, want)
    check_codex(r, want)
    check_antigravity(r, want)
    check_upkeep(r)
    check_storage(r)
    print(f"\n{r.failures} issue(s)." if r.failures else "\nAll good.")
    return 1 if r.failures else 0
