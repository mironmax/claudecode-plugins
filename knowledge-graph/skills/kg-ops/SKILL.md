---
name: kg-ops
user-invocable: true
description: |
  Operations runbook for the knowledge-graph plugin: install and first run,
  plugin updates, server lifecycle (start/stop/restart/logs), autostart via
  systemd, connecting Claude Desktop/Cowork, configuration, the quota-gauge
  status line (reading your own 5h/7d limits), backup and restore, and
  troubleshooting (tools offline, -32000 errors, stale data, Desktop issues).
  Use when something needs setting up, breaks, or the user asks to manage the
  memory server or "read the docs and do what's needed".
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

## Session limits gauge (companion setup)

Claude Code sends `rate_limits` (rolling 5h/7d subscription usage + reset
epochs) **only** to the status-line command's stdin — never to the model, never
persisted. Without a status line that saves it, an agent cannot read its own
remaining budget.

- **Diagnose**: `jq . ~/.claude/last-limits.json` — missing file or stale
  `updated_at` means no status line is persisting the reading.
- **Act**: install `recommended-setup/statusline.sh` from the repo
  (`github.com/mironmax/claudecode-plugins`) to `~/.claude/statusline.sh`,
  `chmod +x`, and register it in `~/.claude/settings.json`:
  `"statusLine": {"type": "command", "command": "~/.claude/statusline.sh"}`.
  Needs `jq`. Then tell the agent the file exists — a KG node is the cheapest
  home (rides the preload); a short `~/.claude/CLAUDE.md` section also works.
- **Verify**: `jq . ~/.claude/last-limits.json` after one render — expect
  `five_hour_pct`, `seven_day_pct`, `*_resets_at` (epoch), `*_seen_at` (epoch),
  `context_pct`, `updated_at`.
- **Undo**: remove the `statusLine` key from settings.

Reading it: `five_hour_pct`/`seven_day_pct` are **account-global** (valid for
every session incl. background/scheduled); `context_pct` belongs to whichever
session rendered last, not necessarily this one. Gate each window on its own
`*_seen_at` — a frame carrying only one window keeps the other's previous value
with its original stamp, so `updated_at` alone can vouch for a stale number.
Headless/scheduled sessions don't reliably render a frame at all. Anchor
quota-sensitive scheduling to `five_hour_resets_at` (the window drifts with
first use), not to wall-clock times. Pace so the session ends on a checkpoint —
handover letter + KG writes cost budget too; stop near ~90%, not at 100%.

## Renaming nodes (and why never by hand)

An id is not a label — it is the key every edge, every version record and
every other graph refers to. Changing one is a graph-wide operation:

    kg_rename_node(session_id, old_id="<old>", new_id="<new>")

That carries the node's creation time, endorsements, archival state and
version history, re-keys its edges in both directions, follows cross-level
edges into project graphs that are **not currently loaded**, and updates live
sessions' seen/preload state. It refuses a target that already exists and one
over six words, and reports any project graph it could not rewrite.

**Never** emulate it with `kg_put_node` under a new name plus
`kg_delete_node` of the old one. That drops the timestamps, the endorsements
and the version history, and strips every edge. The damage that hurts most is
delayed and silent: cross-level edges live in PROJECT graphs pointing up to
user nodes, those graphs are not loaded during the write, and the next time
each one loads, its dangling edge is garbage-collected with only a log
warning. Measured 2026-08-28: 28 user nodes were referenced that way from 10
project graphs.

Bulk pass over a whole graph (server running, source of truth is memory —
editing the JSON under a live server is overwritten on the next save):

    curl -s -X POST localhost:8765/api/nodes/rename \
      -H 'Content-Type: application/json' \
      -d '{"old_id":"<old>","new_id":"<new>","level":"user"}'

Check the response's `skipped_graphs` — a non-empty list names project graphs
that were left alone because they own a node by that id, or already have one
named like the target.

## Documents point into memory, never the reverse

A handover letter, README or CHANGELOG that names a node id takes a
dependency from a stationary artifact on a moving one: nodes get renamed,
merged and archived, and the document rots without anyone noticing. Write
what the document means in its own words; put the document's path in the
node's `touches`. When a series of handovers covers one subject, keep ONE
node for that subject and re-point its `touches` at the current letter rather
than minting a dated node per letter.

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
  command by hand and read the traceback. Check `server/.last_start_error`
  first — a failed start records the classified cause, the time and the log
  path there, and the session-start hook reads it. Most common cause: OS
  Python upgrade broke the venv → `rm -rf` the plugin's `server/venv`,
  `kg-memory start` rebuilds it.
- **Server will not start after a dependency change** (`AttributeError` on a
  library object, `ImportError` at startup) → the venv resolved a version this
  server is not written against. Diagnose with the same smoke check the start
  script runs:
  `cd <plugin>/server && ./venv/bin/python -c 'import mcp_streamable_server as m; m.create_mcp_server()'`
  A version mismatch answers with a `KG PREFLIGHT:` line naming what is
  installed against what `requirements.txt` asks for. Remedy is `rm -rf
  server/venv` + `kg-memory start`; since 0.9.34 the dependency marker is
  keyed to the hash of `requirements.txt`, so a corrected pin re-resolves on
  the next start without needing a plugin update.
- **`restart` says "Server started" but `/health` reports the old version** →
  the PID file went stale while the real process kept listening, so the stop
  missed it and the new process died on a busy port. Confirm with
  `ss -tlnp | grep 8765` (or `lsof -ti:8765`) and compare against
  `server/.mcp_server.pid`; `kg-memory stop-port` clears the true owner.
  Fixed in 0.9.34 — restart now falls back to stopping by port, and a start
  only reports success if the process it launched is still alive.
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
