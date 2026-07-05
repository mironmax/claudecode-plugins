---
name: kg-ops
user-invocable: true
description: |
  Operations runbook for the knowledge-graph plugin: install and first run,
  plugin updates, server lifecycle (start/stop/restart/logs), autostart via
  systemd, connecting Claude Desktop/Cowork, configuration, backup and
  restore, and troubleshooting (tools offline, -32000 errors, stale data,
  Desktop issues). Use when something needs setting up, breaks, or the user
  asks to manage the memory server or "read the docs and do what's needed".
---

# Knowledge-graph operations runbook

Recipes for agents. Each: diagnose → act → verify → undo where it applies.

## Orientation — what runs where

- One **shared HTTP MCP server** serves every session: `http://127.0.0.1:8765/`
  (override port with `KG_HTTP_PORT`). Health: `curl -sf http://127.0.0.1:8765/health`.
- Logs: `~/.local/state/knowledge-graph/mcp_server.log`. PID file:
  `server/.mcp_server.pid` next to `manage_server.sh`.
- Data: `~/.knowledge-graph/` (plain JSON — `user.json`,
  `projects/<slug>/graph.json`, `sessions.json`). Survives uninstall.
- Plugin cache dirs are **versioned** (`~/.claude/plugins/cache/maxim-plugins/knowledge-graph/<version>/`)
  and change on every update. Anything that must survive updates goes through
  the stable shims in `~/.local/bin/`: `kg-memory`, `kg-visual`,
  `kg-desktop-bridge`. Never hardcode a versioned cache path into configs.
- A SessionStart hook auto-starts the server when it's down. It never stops or
  restarts a running one.

## Install / first run

1. User installs via `/plugin marketplace add mironmax/claudecode-plugins` →
   `/plugin install knowledge-graph@maxim-plugins` → restart Claude Code.
2. The start script builds its own Python venv on first run (and rebuilds
   after updates) — first start can take ~1 min. No manual pip steps.
3. Optional shell commands:

   ```bash
   bash "$(find ~/.claude/plugins/cache/maxim-plugins/knowledge-graph -name install_command.sh | sort -V | tail -1)"
   ```

   Symlinks `kg-memory` + `kg-visual` (and refreshes `kg-desktop-bridge` if
   present) into `~/.local/bin/` — which must be on PATH.
4. Verify: health curl above returns JSON; `kg_read` works in a session.

## After a plugin update

1. Rerun `install_command.sh` (recipe above) — repoints all `~/.local/bin`
   shims at the new version dir.
2. The running server still executes the OLD code until restarted:
   `kg-memory restart`.
3. Every open session's MCP connection is now stale — the **user** must run
   `/mcp` → `plugin:knowledge-graph:kg` → Reconnect (agents cannot do this).

## Server lifecycle

```bash
kg-memory start|stop|restart|status|logs|commit
kg-visual start|stop|status|logs        # graph editor at http://localhost:8766
```

- Restarting disconnects all live sessions → each needs the `/mcp` Reconnect
  (ask the user; don't restart casually mid-work).
- `stop` validates the PID actually belongs to the MCP server before killing
  (stale-PID protection) — trust it over manual `kill`.

## Autostart on boot (Linux, systemd user unit)

Prerequisite: `install_command.sh` run once (unit invokes the `kg-memory` shim).

```bash
mkdir -p ~/.config/systemd/user
cp "$(find ~/.claude/plugins/cache/maxim-plugins/knowledge-graph -name memory-mcp.service | sort -V | tail -1)" \
   ~/.config/systemd/user/memory-mcp.service
systemctl --user enable --now memory-mcp.service
```

Verify: `systemctl --user status memory-mcp` + health curl.
Undo: `systemctl --user disable --now memory-mcp.service`.

## Connect Claude Desktop (and Cowork)

Desktop's "Add custom connector" dialog cannot work for a local server — those
connectors are contacted from Anthropic's cloud and require a public https
URL. The local route is Desktop's config file, automated here:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/setup_desktop.py"          # add (idempotent, backs up config)
python3 "${CLAUDE_PLUGIN_ROOT}/setup_desktop.py" --remove # undo
```

The entry points Desktop at the stable `~/.local/bin/kg-desktop-bridge`
symlink, which auto-starts the server if needed and proxies stdio↔HTTP via
`mcp-remote` (needs Node.js ≥ 18; script fails loudly if `npx` is missing).

- Verify the bridge before involving Desktop (expect "Proxy established"):

  ```bash
  timeout 12 npx -y mcp-remote http://127.0.0.1:8765/ --allow-http 2>&1 | head -5
  ```

- Then have the user **fully quit** Desktop (not just the window) and reopen.
  Cowork sessions receive the server through Desktop's own sandbox bridge.
- Caveats: Desktop sessions get no SessionStart preload (Claude Code hook) —
  the first `kg_read` call orients instead. On Windows the auto-start wrapper
  is skipped (no bash); a Claude Code session must have started the server.

## Configuration

Env vars (shell rc, or the systemd unit), then `kg-memory restart`:
`KG_HTTP_PORT` (8765) · `KG_STORAGE_ROOT` (`~/.knowledge-graph`) ·
`KG_SAVE_INTERVAL` (30s) · `KG_AUTOCOMMIT_INTERVAL` (900s, 0 disables) ·
`KG_GRACE_PERIOD_DAYS` / `KG_ORPHAN_GRACE_DAYS` (see `server/core/constants.py`).
Render budgets are fixed by design — no knob. Don't edit the bundled
`.mcp.json` (overwritten on update).

## Backup and restore

- Crash protection is built in: atomic writes + one rolling `<file>.prev`.
  Restore: `cp ~/.knowledge-graph/user.json.prev ~/.knowledge-graph/user.json`
  (same pattern per project graph). Restart not required, but force a reload
  (below) if the server was up during the copy.
- Versioned history: `git init` inside `~/.knowledge-graph` (gitignore
  `*.prev`, `*.tmp`) — the server then auto-commits every 15 min and on
  shutdown; `kg-memory commit` forces one.
- Off-machine: any file backup tool works on the JSON; borg dedups well.

## Troubleshooting

- **kg tools offline / connection refused** → health curl. Down: `kg-memory
  start` (first run builds venv, ~1 min), then user runs `/mcp` → Reconnect.
- **`-32000` / "failed to reconnect"** → the server-side process died; the
  code is generic. Get the real error: `kg-memory logs`, or run the start
  command by hand and read the traceback. Most common cause: OS Python
  upgrade broke the venv → `rm -rf` the plugin's `server/venv`, `kg-memory
  start` rebuilds it.
- **Graph looks stale after direct disk edits** (scripts writing to
  `~/.knowledge-graph` while the server runs) → the server caches graphs in
  memory: `curl -s 'http://127.0.0.1:8765/api/graph/read?reload=true&project_path=<root>'`
  forces a disk reload.
- **Desktop shows no knowledge-graph server** → run the bridge verify command
  above. Bridge OK → the config entry: `~/.config/Claude/claude_desktop_config.json`
  (Linux) / `~/Library/Application Support/Claude/` (macOS) — rerun
  `setup_desktop.py`, then full quit + reopen.
- **Log lines that are fine**: `Healed N corrupt node(s) on load` (self-repair
  did its job) · `over budget but all N active nodes within grace —
  compaction deferred` (informational stall notice).

## Uninstall

`setup_desktop.py --remove` first (frees the Desktop config), then
`/plugin uninstall knowledge-graph@maxim-plugins`. Optionally remove the
`~/.local/bin` shims and the systemd unit. `~/.knowledge-graph/` is preserved.
