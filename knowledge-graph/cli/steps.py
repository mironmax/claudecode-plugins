"""The pieces the memory depends on, each with a check and, where it can be
automated, a fix. `kg doctor` prints the checks; `kg setup` offers the fixes.

States: OK; FIX (a problem, offered by setup); SUGGEST (works, but setup
recommends a change); MANUAL (a problem only the user can fix: the line names
how); OFF (not installed or not applicable).
"""

import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

try:
    from . import kg
except ImportError:  # run as a script from a checkout
    import kg

OK, FIX, SUGGEST, MANUAL, OFF = "ok", "fix", "suggest", "manual", "off"
HOME = Path.home()
MARKET = "maxim-plugins"
PLUGIN = f"knowledge-graph@{MARKET}"
REPO = "mironmax/kg-memory"
TOOLS = ("kg_read", "kg_search", "kg_put_node", "kg_put_edge", "kg_rename_node",
         "kg_sync", "kg_useful", "kg_progress", "kg_delete_node", "kg_delete_edge")
CLAUDE_PREFIX = "mcp__plugin_knowledge-graph_kg"
CLAUDE_LEGACY = "mcp__plugin_memory_kg__"   # this plugin's name before the rename
AGY_SERVER = "knowledge-graph_kg"
CODEX_HOOKS = ("session_start", "user_prompt_submit", "post_tool_use")
UNIT = "kg-memory.service"
LEGACY_UNIT = "memory-mcp.service"
GAUGE_FRESH_DAYS = 7


# ── small helpers ────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def find_binary(name: str) -> str | None:
    local = HOME / ".local/bin" / name
    return shutil.which(name) or (str(local) if os.access(local, os.X_OK) else None)


def run(cmd: list[str], timeout: int = 300) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError(f"{' '.join(cmd)} failed: {(out.stderr or out.stdout).strip()[-300:]}")
    return out.stdout


def age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 172800:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} days"


def version_key(text: str) -> list[int]:
    return [int(x) if x.isdigit() else 0 for x in text.split(".")]


def in_checkout() -> bool:
    return (kg.ROOT.parent / ".git").exists()


def install_kind() -> str:
    """How this kg was installed, which decides how it updates itself."""
    if in_checkout():
        return "checkout"
    if "/pipx/venvs/" in str(kg.ROOT):
        return "pipx"
    if shutil.which("uv"):
        tools = subprocess.run(["uv", "tool", "dir"], capture_output=True, text=True).stdout.strip()
        if tools and str(kg.ROOT).startswith(tools):
            return "uv"
    return "other"


class Context:
    """What a setup run shares: the version wanted and one backup directory."""

    def __init__(self):
        self.want = kg.version()
        self.backups = kg.STATE_DIR / "backups" / time.strftime("%Y%m%d-%H%M%S")

    def backup(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            target = self.backups / path.relative_to(HOME)
        except ValueError:
            target = self.backups / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    def write_json(self, path: Path, data: dict) -> None:
        self.backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".kg-tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    def write_text(self, path: Path, text: str, mode: int | None = None) -> None:
        self.backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        path.write_text(text)
        if mode:
            path.chmod(mode)


class Step:
    key = ""
    group = ""
    title = ""
    # Opt-in: asked with No as the default, and skipped by --yes unless named
    # in --only. For what spends the user's quota.
    opt_in = False

    def check(self, ctx: Context) -> tuple[str, str]:
        raise NotImplementedError

    def apply(self, ctx: Context) -> str:
        raise NotImplementedError

    def undo_plan(self, ctx: Context) -> str | None:
        """What `kg uninstall` would reverse here, or None."""
        return None

    def undo(self, ctx: Context) -> str:
        raise NotImplementedError


# ── the kg command and the server ────────────────────────────────────────────

def kg_command() -> str | None:
    return find_binary("kg")


class Command(Step):
    key, group, title = "command", "kg", "the kg command on PATH"

    def check(self, ctx):
        found = kg_command()
        if found:
            return OK, f"kg at {found}"
        if in_checkout():
            return FIX, "kg is not on PATH"
        return MANUAL, "kg is not on PATH: run `uv tool update-shell` and open a new terminal"

    def apply(self, ctx):
        link = HOME / ".local/bin/kg"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            ctx.backup(link)
            link.unlink()
        link.symlink_to(kg.ROOT / "cli" / "kg-dev")
        return f"linked {link} → this checkout (development copy)"

    def undo_plan(self, ctx):
        link = HOME / ".local/bin/kg"
        if link.is_symlink() and link.resolve() == (kg.ROOT / "cli" / "kg-dev").resolve():
            return "remove the development kg link"
        return None

    def undo(self, ctx):
        (HOME / ".local/bin/kg").unlink()
        return "kg link removed"


class LegacyCommands(Step):
    key, group, title = "legacy-commands", "kg", "old commands lead to kg"

    def visual(self) -> Path | None:
        link = HOME / ".local/bin/kg-visual"
        return link if link.is_symlink() and link.resolve().name == "manage_visual.sh" else None

    def check(self, ctx):
        old = HOME / ".local/bin/kg-memory"
        if old.is_symlink() and old.resolve().name == "manage_server.sh":
            return FIX, f"kg-memory still runs {old.resolve()}"
        if self.visual():
            return FIX, "kg-visual is replaced by `kg editor`"
        return OK, "kg-memory → kg" if old.exists() else "no legacy kg-memory command"

    def apply(self, ctx):
        done = []
        old = HOME / ".local/bin/kg-memory"
        if old.is_symlink() and old.resolve().name == "manage_server.sh":
            ctx.write_text(old, "#!/bin/sh\n# Kept for old docs and units: kg is the command now.\n"
                                'case "$1" in stop-port) set -- stop ;; esac\n'
                                'exec kg "$@"\n', 0o755)
            done.append("kg-memory now runs kg")
        if self.visual():
            self.visual().unlink()
            done.append("kg-visual removed (use `kg editor`)")
        return "; ".join(done)

    def undo_plan(self, ctx):
        old = HOME / ".local/bin/kg-memory"
        if old.is_file() and not old.is_symlink() and 'exec kg "$@"' in old.read_text():
            return "remove the kg-memory shim"
        return None

    def undo(self, ctx):
        ctx.backup(HOME / ".local/bin/kg-memory")
        (HOME / ".local/bin/kg-memory").unlink()
        return "kg-memory removed"


def systemd_user() -> bool:
    if platform.system() != "Linux" or not shutil.which("systemctl"):
        return False
    probe = subprocess.run(["systemctl", "--user", "is-system-running"],
                           capture_output=True, text=True)
    return probe.stdout.strip() in ("running", "degraded", "starting")


def unit_text(command: str) -> str:
    # A user service starts with systemd's minimal environment, but maintenance
    # runs spawn claude/codex/agy, which may live anywhere on the user's PATH.
    # XDG_STATE_HOME keeps the server's start record where kg looks for it.
    keep = {k: v for k, v in os.environ.items()
            if k in ("PATH", "CODEX_HOME", "XDG_STATE_HOME") or k.startswith("KG_")}
    if "PATH" in keep:
        keep["PATH"] = os.pathsep.join(dict.fromkeys(keep["PATH"].split(os.pathsep)))
    env = "".join(f'Environment="{k}={v.replace("%", "%%")}"\n' for k, v in sorted(keep.items()))
    return ("[Unit]\nDescription=Knowledge-graph memory server (kg)\n\n"
            f"[Service]\nExecStart={command} serve\n{env}Restart=on-failure\nRestartSec=5\n\n"
            "[Install]\nWantedBy=default.target\n")


class Service(Step):
    key, group, title = "service", "kg", "server starts with your login session"

    def check(self, ctx):
        if not systemd_user():
            return OFF, "no systemd user session: the session hook starts the server"
        unit = HOME / ".config/systemd/user" / UNIT
        command = kg_command()
        enabled = subprocess.run(["systemctl", "--user", "is-enabled", UNIT],
                                 capture_output=True, text=True).stdout.strip() == "enabled"
        if enabled and command and unit.exists() and f"ExecStart={command} serve\n" in unit.read_text():
            return OK, f"{UNIT} enabled"
        if (HOME / ".config/systemd/user" / LEGACY_UNIT).exists():
            return FIX, f"old {LEGACY_UNIT} found; {UNIT} replaces it"
        return SUGGEST, f"{UNIT} not installed (the session hook starts the server meanwhile)"

    def apply(self, ctx):
        command = kg_command()
        if not command:
            raise RuntimeError("install the kg command first")
        units = HOME / ".config/systemd/user"
        legacy = units / LEGACY_UNIT
        if legacy.exists():
            subprocess.run(["systemctl", "--user", "disable", "--now", LEGACY_UNIT], capture_output=True)
            ctx.backup(legacy)
            legacy.unlink()
        ctx.write_text(units / UNIT, unit_text(command))
        run(["systemctl", "--user", "daemon-reload"])
        kg.stop()
        run(["systemctl", "--user", "enable", "--now", UNIT])
        if not kg.wait(lambda: kg.health() is not None, 20):
            raise RuntimeError(f"{UNIT} started but the server does not answer: journalctl --user -u {UNIT}")
        return f"{UNIT} enabled and running"

    def undo_plan(self, ctx):
        if (HOME / ".config/systemd/user" / UNIT).exists():
            return f"disable and remove {UNIT}"
        return None

    def undo(self, ctx):
        unit = HOME / ".config/systemd/user" / UNIT
        subprocess.run(["systemctl", "--user", "disable", "--now", UNIT], capture_output=True)
        ctx.backup(unit)
        unit.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        return f"{UNIT} removed"


class Server(Step):
    key, group, title = "server", "kg", "one server, this version, this copy"

    def check(self, ctx):
        data = kg.health()
        if not data:
            return FIX, "not running"
        pid = kg.running_pid()
        args = kg.process_args(pid) if pid else ""
        if data.get("version") != ctx.want:
            return FIX, f"running {data.get('version')}, this kg is {ctx.want}"
        if args and str(kg.SERVER_SCRIPT) not in args:
            return FIX, f"running {ctx.want} from another copy ({args.split()[-1]})"
        return OK, f"running {ctx.want}, {data.get('active_sessions')} sessions"

    def apply(self, ctx):
        if kg.restart():
            raise RuntimeError(f"the server did not come up: kg status, {kg.LOG_FILE}")
        return "server restarted on this copy (sessions reconnect on their next call)"

    def undo_plan(self, ctx):
        return "stop the memory server" if kg.health() else None

    def undo(self, ctx):
        kg.stop()
        return "server stopped"


# ── Claude Code ──────────────────────────────────────────────────────────────

CLAUDE_SETTINGS = HOME / ".claude/settings.json"
CLAUDE_PLUGINS = HOME / ".claude/plugins"


def claude_plugin_version() -> str | None:
    records = load_json(CLAUDE_PLUGINS / "installed_plugins.json").get("plugins", {}).get(PLUGIN) or [{}]
    return records[0].get("version")


class ClaudePlugin(Step):
    key, group, title = "claude-plugin", "Claude Code", "plugin installed and current"

    def check(self, ctx):
        if not find_binary("claude"):
            return OFF, "not installed"
        have = claude_plugin_version()
        if not have:
            return FIX, "plugin not installed"
        if have != ctx.want:
            return FIX, f"plugin {have}, server {ctx.want}"
        return OK, f"plugin {have}"

    def apply(self, ctx):
        claude = find_binary("claude")
        if MARKET not in load_json(CLAUDE_PLUGINS / "known_marketplaces.json"):
            run([claude, "plugin", "marketplace", "add", REPO])
        else:
            run([claude, "plugin", "marketplace", "update", MARKET])
        if claude_plugin_version():
            run([claude, "plugin", "update", PLUGIN])
        else:
            run([claude, "plugin", "install", PLUGIN])
        return f"plugin {claude_plugin_version()} (restart Claude Code sessions to load it)"

    def undo_plan(self, ctx):
        if find_binary("claude") and claude_plugin_version():
            return "uninstall the Claude Code plugin"
        return None

    def undo(self, ctx):
        run([find_binary("claude"), "plugin", "uninstall", PLUGIN])
        return "Claude Code plugin uninstalled"


class ClaudeAutoUpdate(Step):
    key, group, title = "claude-autoupdate", "Claude Code", "marketplace updates itself"

    def check(self, ctx):
        if not find_binary("claude"):
            return OFF, "not installed"
        market = load_json(CLAUDE_PLUGINS / "known_marketplaces.json").get(MARKET)
        if market is None:
            return OFF, "marketplace not added yet (the plugin step adds it)"
        if market.get("autoUpdate"):
            return OK, "auto-update on"
        return FIX, "auto-update off: fixes never arrive"

    def apply(self, ctx):
        path = CLAUDE_PLUGINS / "known_marketplaces.json"
        data = load_json(path)
        data[MARKET]["autoUpdate"] = True
        ctx.write_json(path, data)
        return "auto-update on"


class ClaudePermissions(Step):
    key, group, title = "claude-permissions", "Claude Code", "kg tools pre-approved"

    def state(self):
        perms = load_json(CLAUDE_SETTINGS).get("permissions") or {}
        allow = list(perms.get("allow") or [])
        whole = CLAUDE_PREFIX in allow or f"{CLAUDE_PREFIX}__*" in allow
        missing = [] if whole else [t for t in TOOLS if f"{CLAUDE_PREFIX}__{t}" not in allow]
        stale = [a for a in allow if a.startswith(CLAUDE_LEGACY)]
        return perms, missing, stale

    def check(self, ctx):
        if not find_binary("claude"):
            return OFF, "not installed"
        perms, missing, stale = self.state()
        if stale:
            return FIX, f"{len(stale)} permissions name this plugin's old name ({stale[0]}…)"
        if not missing:
            return OK, "kg tools pre-approved for every project"
        mode = perms.get("defaultMode")
        if mode in ("auto", "bypassPermissions"):
            return SUGGEST, f"approved by {mode} mode today; other modes would ask on every call"
        return FIX, f"{len(missing)} kg tools ask on every call"

    def apply(self, ctx):
        data = load_json(CLAUDE_SETTINGS)
        perms = data.setdefault("permissions", {})
        allow = [a for a in perms.get("allow") or [] if not a.startswith(CLAUDE_LEGACY)]
        allow += [f"{CLAUDE_PREFIX}__{t}" for t in TOOLS if f"{CLAUDE_PREFIX}__{t}" not in allow]
        perms["allow"] = allow
        ctx.write_json(CLAUDE_SETTINGS, data)
        return "kg tools pre-approved; old-name entries removed"

    def undo_plan(self, ctx):
        allow = (load_json(CLAUDE_SETTINGS).get("permissions") or {}).get("allow") or []
        return "remove the kg tool permissions" if any(a.startswith(CLAUDE_PREFIX) for a in allow) else None

    def undo(self, ctx):
        data = load_json(CLAUDE_SETTINGS)
        perms = data["permissions"]
        perms["allow"] = [a for a in perms["allow"] if not a.startswith(CLAUDE_PREFIX)]
        ctx.write_json(CLAUDE_SETTINGS, data)
        return "kg tool permissions removed"


class ClaudeAutoMemory(Step):
    key, group, title = "claude-automemory", "Claude Code", "built-in auto-memory off"

    def check(self, ctx):
        if not find_binary("claude"):
            return OFF, "not installed"
        if load_json(CLAUDE_SETTINGS).get("autoMemoryEnabled") is False:
            return OK, "auto-memory off"
        return FIX, "built-in auto-memory on: two memories write conflicting entries"

    def apply(self, ctx):
        data = load_json(CLAUDE_SETTINGS)
        data["autoMemoryEnabled"] = False
        ctx.write_json(CLAUDE_SETTINGS, data)
        return "auto-memory off"

    def undo_plan(self, ctx):
        if load_json(CLAUDE_SETTINGS).get("autoMemoryEnabled") is False:
            return "turn Claude Code's built-in auto-memory back on"
        return None

    def undo(self, ctx):
        data = load_json(CLAUDE_SETTINGS)
        data.pop("autoMemoryEnabled", None)
        ctx.write_json(CLAUDE_SETTINGS, data)
        return "built-in auto-memory back to its default (on)"


class ClaudeGauge(Step):
    key, group, title = "claude-gauge", "Claude Code", "quota gauge for budget notices and upkeep"

    def check(self, ctx):
        if not find_binary("claude"):
            return OFF, "not installed"
        seen = load_json(HOME / ".claude/last-limits.json").get("updated_at")
        if seen and time.time() - seen < GAUGE_FRESH_DAYS * 86400:
            return OK, f"written {age(time.time() - seen)} ago by your status line"
        line = (load_json(CLAUDE_SETTINGS).get("statusLine") or {}).get("command") or ""
        if " gauge" in line and Path(line.split()[0]).name in ("kg", "kg-dev"):
            return OK, "kg gauge installed (written on the next status-line render)"
        return FIX, "no quota gauge: no budget notices, no Claude-run upkeep"

    def apply(self, ctx):
        command = kg_command() or "kg"
        data = load_json(CLAUDE_SETTINGS)
        line = data.get("statusLine") or {}
        current = line.get("command")
        wrapped = f"{command} gauge --wrap {shlex.quote(current)}" if current else f"{command} gauge"
        data["statusLine"] = {**line, "type": "command", "command": wrapped}
        ctx.write_json(CLAUDE_SETTINGS, data)
        return ("your status line now passes through kg gauge, unchanged" if current
                else "status line set to kg gauge (a minimal quota line)")

    def kg_line(self) -> list[str] | None:
        line = (load_json(CLAUDE_SETTINGS).get("statusLine") or {}).get("command") or ""
        try:
            words = shlex.split(line)
        except ValueError:
            return None
        if len(words) > 1 and words[1] == "gauge" and Path(words[0]).name in ("kg", "kg-dev"):
            return words
        return None

    def undo_plan(self, ctx):
        words = self.kg_line()
        if not words:
            return None
        return "restore your own status line" if "--wrap" in words else "remove the kg status line"

    def undo(self, ctx):
        words = self.kg_line()
        data = load_json(CLAUDE_SETTINGS)
        if "--wrap" in words:
            data["statusLine"]["command"] = words[words.index("--wrap") + 1]
        else:
            data.pop("statusLine")
        ctx.write_json(CLAUDE_SETTINGS, data)
        return "status line restored"


# ── Codex ────────────────────────────────────────────────────────────────────

def desktop_config() -> Path:
    if platform.system() == "Darwin":
        return HOME / "Library/Application Support/Claude/claude_desktop_config.json"
    return HOME / ".config/Claude/claude_desktop_config.json"


DESKTOP_KEY = "knowledge-graph"
DESKTOP_BRIDGE = HOME / ".local/bin/kg-desktop-bridge"   # the npx mcp-remote bridge before kg


class ClaudeDesktop(Step):
    key, group, title = "claude-desktop", "Claude Desktop", "memory tools in Claude Desktop"

    def entry(self) -> dict:
        # Desktop spawns commands without a shell or the user's PATH.
        kg_bin = HOME / ".local/bin/kg"
        return {"command": str(kg_bin) if kg_bin.exists() else (kg_command() or "kg"),
                "args": ["mcp"]}

    def check(self, ctx):
        if not desktop_config().parent.is_dir():
            return OFF, "not installed"
        current = (load_json(desktop_config()).get("mcpServers") or {}).get(DESKTOP_KEY)
        if current == self.entry():
            return OK, "connected through kg mcp"
        if current:
            return FIX, f"connected through {current.get('command')}: switch to kg mcp (no Node needed)"
        return FIX, "not connected"

    def apply(self, ctx):
        data = load_json(desktop_config())
        data.setdefault("mcpServers", {})[DESKTOP_KEY] = self.entry()
        ctx.write_json(desktop_config(), data)
        if DESKTOP_BRIDGE.is_symlink():
            ctx.backup(DESKTOP_BRIDGE)
            DESKTOP_BRIDGE.unlink()
        return "connected through kg mcp: restart Claude Desktop to load it"

    def undo_plan(self, ctx):
        current = (load_json(desktop_config()).get("mcpServers") or {}).get(DESKTOP_KEY)
        return "disconnect Claude Desktop from the memory" if current == self.entry() else None

    def undo(self, ctx):
        data = load_json(desktop_config())
        del data["mcpServers"][DESKTOP_KEY]
        ctx.write_json(desktop_config(), data)
        return "Claude Desktop disconnected"


CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")


def codex_plugin_version() -> str | None:
    cache = CODEX_HOME / "plugins/cache" / MARKET / "knowledge-graph"
    versions = [p.name for p in cache.iterdir() if p.is_dir()] if cache.is_dir() else []
    return max(versions, key=version_key) if versions else None


class CodexPlugin(Step):
    key, group, title = "codex-plugin", "Codex", "plugin installed and current"

    def check(self, ctx):
        if not find_binary("codex"):
            return OFF, "not installed"
        have = codex_plugin_version()
        if not have:
            return FIX, "plugin not installed"
        if have != ctx.want:
            return FIX, f"plugin {have}, server {ctx.want}"
        return OK, f"plugin {have}"

    def apply(self, ctx):
        codex = find_binary("codex")
        if MARKET in run([codex, "plugin", "marketplace", "list"]):
            run([codex, "plugin", "marketplace", "upgrade", MARKET])
        else:
            run([codex, "plugin", "marketplace", "add", REPO])
        run([codex, "plugin", "add", PLUGIN])
        return (f"plugin {codex_plugin_version()}; Codex will ask you to trust its hooks: "
                "run /hooks in Codex once")

    def undo_plan(self, ctx):
        if find_binary("codex") and codex_plugin_version():
            return "remove the Codex plugin"
        return None

    def undo(self, ctx):
        run([find_binary("codex"), "plugin", "remove", PLUGIN])
        return "Codex plugin removed"


class CodexHooks(Step):
    key, group, title = "codex-hooks", "Codex", "hooks trusted"

    def check(self, ctx):
        if not find_binary("codex") or not codex_plugin_version():
            return OFF, "not installed"
        try:
            config = (CODEX_HOME / "config.toml").read_text()
        except OSError:
            config = ""
        untrusted = [h for h in CODEX_HOOKS if f'"{PLUGIN}:hooks/hooks.json:{h}:0:0"' not in config]
        if untrusted:
            return MANUAL, (f"not trusted: {', '.join(untrusted)} (no preload or recall): "
                            "run /hooks in Codex, trust knowledge-graph, start a new session")
        return OK, "trusted (Codex asks again when their content changes)"


# ── Antigravity ──────────────────────────────────────────────────────────────

AGY_PLUGIN = HOME / ".gemini/config/plugins/knowledge-graph"
AGY_SETTINGS = HOME / ".gemini/antigravity-cli/settings.json"


def tracked_copy() -> Path:
    """The plugin's tracked files with their working contents: a checkout
    also holds venvs and caches that must not be installed."""
    files = run(["git", "-C", str(kg.ROOT), "ls-files", "-z"]).split("\0")
    target = Path(tempfile.mkdtemp(prefix="kg-agy-plugin-")) / "knowledge-graph"
    for name in filter(None, files):
        source = kg.ROOT / name
        if source.is_file():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / name)
    return target


class AgyPlugin(Step):
    key, group, title = "agy-plugin", "Antigravity", "plugin installed and current"

    def check(self, ctx):
        if not find_binary("agy"):
            return OFF, "not installed"
        have = load_json(AGY_PLUGIN / ".claude-plugin/plugin.json").get("version")
        if not have:
            return FIX, "plugin not installed"
        if have != ctx.want:
            return FIX, f"plugin {have}, server {ctx.want}"
        return OK, f"plugin {have}"

    def apply(self, ctx):
        source = tracked_copy() if in_checkout() else kg.ROOT
        run([find_binary("agy"), "plugin", "install", str(source)])
        if in_checkout():
            shutil.rmtree(source.parent, ignore_errors=True)
        return "plugin installed; start a new Antigravity conversation"

    def undo_plan(self, ctx):
        if find_binary("agy") and AGY_PLUGIN.exists():
            return "uninstall the Antigravity plugin"
        return None

    def undo(self, ctx):
        run([find_binary("agy"), "plugin", "uninstall", "knowledge-graph"])
        return "Antigravity plugin uninstalled"


class AgyPermissions(Step):
    key, group, title = "agy-permissions", "Antigravity", "kg tools granted"

    def missing(self):
        allow = (load_json(AGY_SETTINGS).get("permissions") or {}).get("allow") or []
        if "mcp(*)" in allow or f"mcp({AGY_SERVER}/*)" in allow:
            return []
        return [t for t in TOOLS if not t.startswith("kg_delete") and f"mcp({AGY_SERVER}/{t})" not in allow]

    def check(self, ctx):
        if not find_binary("agy"):
            return OFF, "not installed"
        missing = self.missing()
        if missing:
            return FIX, f"{len(missing)} kg tools ask on every call; maintenance runs are refused"
        return OK, "kg tools granted (deletions still ask)"

    def apply(self, ctx):
        data = load_json(AGY_SETTINGS)
        perms = data.setdefault("permissions", {})
        perms["allow"] = list(perms.get("allow") or []) + [f"mcp({AGY_SERVER}/{t})" for t in self.missing()]
        ctx.write_json(AGY_SETTINGS, data)
        return "kg tools granted; deletions stay on Ask"

    def undo_plan(self, ctx):
        allow = (load_json(AGY_SETTINGS).get("permissions") or {}).get("allow") or []
        return "remove the kg tool grants" if any(f"mcp({AGY_SERVER}/" in a for a in allow) else None

    def undo(self, ctx):
        data = load_json(AGY_SETTINGS)
        perms = data["permissions"]
        perms["allow"] = [a for a in perms["allow"] if f"mcp({AGY_SERVER}/" not in a]
        ctx.write_json(AGY_SETTINGS, data)
        return "kg tool grants removed"


# ── maintenance and storage ──────────────────────────────────────────────────

class Upkeep(Step):
    key, group, title = "upkeep", "Memory", "background upkeep"
    opt_in = True   # it spends quota

    def check(self, ctx):
        cfg = load_json(kg.STORAGE_ROOT / "chores.json")
        if not cfg.get("enabled"):
            return SUGGEST, "off: the graph is tended only when you run /kg-maintain"
        last = None
        try:
            for line in (kg.STORAGE_ROOT / "chores.jsonl").read_text().splitlines()[-2000:]:
                event = json.loads(line)
                if event.get("event") == "done":
                    last = event
        except (OSError, ValueError):
            pass
        if last:
            return OK, (f"on; last run {age(time.time() - last['ts'])} ago "
                        f"({last.get('kind')} on {last.get('graph')}, rc {last.get('rc')})")
        return OK, "on; no run recorded yet"

    def apply(self, ctx):
        path = kg.STORAGE_ROOT / "chores.json"
        data = load_json(path) or load_json(kg.ROOT / "chores/chores.example.json")
        data["enabled"] = True
        ctx.write_json(path, data)
        return "on, within the quota gates in chores.json (runs only while quota is spare)"

    def undo_plan(self, ctx):
        return "switch background upkeep off" if load_json(kg.STORAGE_ROOT / "chores.json").get("enabled") else None

    def undo(self, ctx):
        path = kg.STORAGE_ROOT / "chores.json"
        data = load_json(path)
        data["enabled"] = False
        ctx.write_json(path, data)
        return "background upkeep off"


class Storage(Step):
    key, group, title = "storage", "Memory", "storage and history"

    def check(self, ctx):
        root = kg.STORAGE_ROOT
        if not root.is_dir():
            return OFF, f"{root} not created yet (the first session creates it)"
        if (root / ".git").is_dir():
            return OK, f"{root}, versioned with git"
        return OFF, f"{root}, no version history (README: Versioned history and external backups)"


STEPS = [Command(), LegacyCommands(), Service(), Server(),
         ClaudePlugin(), ClaudeAutoUpdate(), ClaudePermissions(), ClaudeAutoMemory(), ClaudeGauge(), ClaudeDesktop(),
         CodexPlugin(), CodexHooks(), AgyPlugin(), AgyPermissions(), Upkeep(), Storage()]
