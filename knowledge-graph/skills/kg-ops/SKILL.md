---
name: kg-ops
user-invocable: true
description: |
  Operations runbook for kg-memory, the `kg` command and its plugins: install
  and first run (kg setup, kg doctor), updates (kg update), server lifecycle
  (kg start/stop/restart/logs), autostart via systemd, connecting Claude
  Desktop/Cowork, Codex CLI, Antigravity, maintenance chores and
  their runner, configuration, the quota-gauge
  status line (reading your own 5h/7d limits), backup and restore, and
  troubleshooting (tools offline, -32000 errors, stale data, Desktop issues).
  Use when something needs setting up, breaks, or the user asks to manage the
  memory server or "read the docs and do what's needed".
---

# Knowledge-graph operations runbook

Recipes for agents. Each: diagnose → act → verify → undo where it applies.

## Orientation — what runs where

- **`kg`** is the one command (PyPI package `kg-memory`, installed with
  `uv tool install kg-memory`). It runs the **shared HTTP MCP server** every
  harness uses: `http://127.0.0.1:8765/` (port: `KG_HTTP_PORT`). Health:
  `kg status`, or `curl -sf http://127.0.0.1:8765/health`.
- Harnesses connect through **`kg mcp`** (stdio): it starts the server when it
  is down and rides out restarts, so the tools stay connected.
- The plugins (Claude Code, Codex, Antigravity) are thin: hooks, skills, the
  `kg mcp` entry. They need `kg`; without it their SessionStart hook says how
  to install it.
- Logs and state: `~/.local/state/knowledge-graph/` (`mcp_server.log`,
  `server.pid`, `last_start_error` when a start failed, `backups/` from setup).
- Data: `~/.knowledge-graph/` (plain JSON — `user.json`,
  `projects/<slug>/graph.json`, `sessions.json`, plus `maintain.json`: the
  maintenance agent's own craft memory, never preloaded or searched). Survives
  uninstall.

## Install / first run

```bash
uv tool install kg-memory     # uv: https://docs.astral.sh/uv/
kg setup                      # asks before each change; --plan shows them first
```

`kg setup` checks every piece and fixes what the user accepts: the `kg`
command on PATH, the systemd user service (Linux), the server, and for each
installed harness its plugin, permissions and settings (Claude Code, Codex,
Antigravity, Claude Desktop). Every changed file is backed up first. Agents
pass `--yes` only for items the user agreed to in chat, e.g.
`kg setup --yes --only claude-desktop`. Then `kg doctor` must be all green.

Two steps stay with the user: in **Codex**, run `/hooks` and trust the
knowledge-graph hooks (Codex keeps plugin hooks off until approved; a first
`kg_read` hints when no hook has reported). In **Claude Desktop**, fully quit
and reopen after setup. Antigravity is experimental:
[the Antigravity guide](../../ANTIGRAVITY.md).

## Updates

```bash
kg update        # upgrades kg, restarts the server on it, updates every installed plugin
kg doctor        # verify
```

Open sessions keep their tools: `kg mcp` waits out the restart. In Codex,
trust changed hook definitions in `/hooks`; new skills and hooks load in a
new session.

## Server lifecycle

```bash
kg start | stop | restart | status | logs [-f] | commit
kg editor [stop]          # graph editor at http://localhost:8766
```

- With the systemd unit enabled, `kg start/stop/restart` go through
  `systemctl --user` (`kg-memory.service`); without it, `kg` manages the
  process itself. Concurrent starts are serialised: the second finds the
  first's server.
- A start that fails records why in `last_start_error`; hooks then report that
  cause instead of retrying. The next successful `kg start` clears it.
- Ask before restarting mid-work: live sessions survive it, but a
  half-finished write in another session is still that session's business.

## Autostart on boot

`kg setup` installs and enables the systemd user unit `kg-memory.service`
(Linux). Verify: `systemctl --user status kg-memory` and `kg status`. Undo:
`kg uninstall` (reverses everything setup did; memory stays).

## Connect Claude Desktop (and Cowork)

Desktop's "Add custom connector" dialog cannot reach a local server (those
connectors are contacted from Anthropic's cloud). `kg setup` writes Desktop's
config instead: `{"command": "~/.local/bin/kg", "args": ["mcp"]}` as an
absolute path, since Desktop spawns without a shell. An older entry that ran
the npx `mcp-remote` bridge is replaced; Node is no longer needed.

- The user then **fully quits** Desktop and reopens it. Cowork sessions
  receive the server through Desktop's own sandbox bridge.
- Caveats: Desktop chat has no SessionStart hook; its first `kg_read` opens
  user memory unless cwd names a project. Desktop's Code tab runs the Claude
  Code hooks.

## Configuration

Env vars (shell rc, or the systemd unit), then `kg restart`:
`KG_HTTP_PORT` (8765) · `KG_STORAGE_ROOT` (`~/.knowledge-graph`) ·
`KG_SAVE_INTERVAL` (30s) · `KG_AUTOCOMMIT_INTERVAL` (900s, 0 disables) ·
`KG_ORPHAN_GRACE_DAYS` (see `server/core/constants.py`).
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
  (`github.com/mironmax/kg-memory`) to `~/.claude/statusline.sh`,
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

**Budget notices (the plugin, every harness).** The server reads the
session's own gauge on the hooks it already serves and tells the agent once
per level per window: "plan the wrap-up" at 80% of five hours, "wrap up now"
at 90%; 90/95% for the week. Claude Code: this status-line file (no notices
without it). Codex: the session's own rollout, nothing to install. Antigravity:
a live `agy -p /usage` reading, cached five minutes. Off: `"budget_notices":
false` in `~/.knowledge-graph/chores.json`, or `KG_BUDGET_NOTICES=0` in the
server's environment. Antigravity's own self-read: `agy -p /usage
--output-format json` (no model call).

## Maintenance chores (activity-triggered gardening)

The server can run small maintenance chores while you work: one debt category,
one or two nodes it names itself, a detached headless agent, ~6 tool calls. It
fires on a prompt arriving, because that is the only signal that reliably means
"machine awake and this graph in use" — the systemd tick's gate needs the quota
gauge fresh AND usage low, and those two are almost never true together.

**Off unless switched on** — it spends your quota.

- **Enable**: `cp <plugin>/chores/chores.example.json ~/.knowledge-graph/chores.json`
  and set `"enabled": true`. `KG_CHORES=1` in the server's environment does the
  same. Config is re-read when the file changes; no restart needed.
- **Two tiers.** A *chore* is the small unit above. A *pass* is the full
  `/kg-maintain` runbook, dispatched the same way but on a different trigger:
  **time since the last stamped pass** (`pass_interval_days`, default 21) on a
  graph you are using — never on debt, because chores drive debt down to the
  formula's floor and a groomed graph would otherwise never qualify again.
  It is funded by the **weekly surplus**: `pace = seven_day_pct ÷ (100 ×
  fraction of the 7-day window elapsed)`, and it runs only when `pace ≤
  pass_pace_max` (1.0) — i.e. when the week is on course to leave quota
  unspent. Firings drift toward the weekly reset on their own; there is no
  day-of-week rule. Backstops: `pass_max_5h` 40, `pass_max_7d` 70,
  `pass_max_per_day` 1, `pass_timeout_s` 1500.
- **Permissions**: chores run under `<plugin>/chores/settings.json` — a scoped
  MCP allowlist (read/search/put_node/put_edge/rename_node/progress), with
  node deletion absent. Claude Code denies Bash, edits and web through its
  permissions file. Codex disables shell and hosted web, and its read-only
  sandbox blocks filesystem writes; other built-in tools can still appear.
  Passes use
  `chores/pass-settings.json`, which adds `kg_delete_node`/`kg_delete_edge`
  because merges need them — two files so the small, frequent unit stays
  strictly non-destructive. Both ship with the plugin on purpose: the previous
  dispatcher kept its settings in `~/.config`, never gained `kg_rename_node`
  when v0.9.35 added it, and its one id pass recorded `ids_renamed: 0`.
  Override with `"settings"` / `"pass_settings"` only if you must.
- **Runner** — which harness runs the agent, and whose quota it spends:
  `"runner": "auto"` (default: Claude Code if installed, else Codex, else
  Antigravity), `"claude"`, `"codex"` or `"antigravity"`. The quota gate reads the runner's own gauge:
  `~/.claude/last-limits.json` for Claude, the newest quota event across
  recently written Codex rollouts for Codex — including sessions resumed
  from old date directories. A new rollout with no quota event can use
  another session's fresh reading; stale or unreadable readings refuse.
  The server scans `${CODEX_HOME:-$HOME/.codex}/sessions`; custom `CODEX_HOME`
  must be set in the shared server's environment too. A Codex run is gated on the
  ChatGPT plan's 5h/weekly windows, never on Claude's, and a fresh reading
  exists only while someone uses that harness. Codex runs are `codex exec
  --ephemeral --ignore-user-config` with shell and hosted web off, a read-only
  filesystem sandbox, and MCP access limited to the tier's kg tools (taken
  from the same `chores/*settings.json`), pre-approved.
  `"codex_model"` picks the model (default: Codex's own),
  `"codex_reasoning_effort"` the effort (default low for chores, medium for
  passes). A configured `"claude_bin"`/`"codex_bin"`/`"antigravity_bin"`
  pins its runner.
  **Antigravity runs** (`"runner": "antigravity"`) are gated on a live
  `agy -p /usage` reading for the run's model group (`"antigravity_model"`,
  default the CLI's own Gemini; Claude/GPT models use the other bucket).
  Plans with weekly buckets only skip the 5h gate. agy takes no per-run
  settings, so a run uses your grants: it refuses unless
  `~/.gemini/antigravity-cli/settings.json` allows the tier's kg tools
  (`mcp(knowledge-graph_kg/kg_read)` etc., see ANTIGRAVITY.md), and refuses
  while `useG1Credits` could spend paid credits (`"antigravity_allow_credits":
  true` overrides). Leave `kg_delete_*` on Ask: headless agy soft-denies
  them, so an Antigravity run never deletes. The run is
  `server/mcp_http/agy_chore.py`: a tool-less `kg-maintainer` agent in
  `~/.knowledge-graph/agy-runner/`; it refuses if `/agents` does not list the
  agent and kills the run if agy logs a fallback to its default agent.
  `"antigravity_effort"` defaults low for chores, medium for passes.
- **Watch**: `tail -f ~/.knowledge-graph/chores.jsonl | jq .` — one line per
  decision, refusals included (`{"event":"skip","reason":"5h 71%"}`), so "why
  did nothing run" is always answerable. Dispatches log the targets; the
  `done` line logs the return code and the debt before/after. An anchor
  dispatch also logs each dangling entry with the server's verdict; a lift
  dispatch logs the cluster and its evidence, and its `done` line logs the
  `outcome` — the principle node, which members were edged to it, and the
  chore's own stamp — so a later audit can check how the touched nodes fared.
- **Tune**: `min_interval_s` (global spacing, default 45 min),
  `graph_cooldown_s` (6 h), `max_per_day` (8), `debt_floor` (0.12),
  `max_5h`/`max_7d` (55/80 — deliberately stricter than the scheduled pass,
  because a chore fires while you are working), `timeout_s` (420).
- **Safety**: a chore never renames a node a recently-active session is
  holding (that would turn its next read into a NOT FOUND; gist and edge work
  is safe and only demotes such nodes), never touches one an earlier pass
  recorded as `declined`, and never does entity consolidation or duplicate
  merges — those need a full pass's context. A node whose gist was rewritten
  more than twice in 30 days is left out of every chore that rewrites text in
  place (gist, notes, anchor): repeated rewriting of the same memory is the
  one maintenance pattern measured to degrade it.
  Hooks are suppressed inside a chore run via `KG_CHORE=1`, so a chore cannot
  preload a graph it does not need, nor dispatch another chore.
- **Disable**: set `"enabled": false` (or delete the config). A chore already
  running finishes.

The scheduled `kg-maintain.timer` keeps one job: dormant graphs, whose projects
nobody has opened, are never reached by an activity trigger. Note its prompt and
`~/.config/kg-maintain/settings.json` predate v0.9.35 — no id refinement, no
`kg_rename_node` in the allowlist — so if you keep it, bring them in line with
`chores/pass-settings.json` and the pass prompt in `core/chores.py`.

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

- Crash protection is built in: atomic writes + one rolling backup beside
  each graph, named by replacing the extension: `user.prev` for `user.json`,
  `graph.prev` for a project's `graph.json`.
  Restore: `cp ~/.knowledge-graph/user.prev ~/.knowledge-graph/user.json`
  (same pattern per project graph). Restart not required, but force a reload
  (below) if the server was up during the copy.
- Versioned history: `git init` inside `~/.knowledge-graph` (gitignore
  `*.prev`, `*.tmp`) — the server then auto-commits every 15 min and on
  shutdown; `kg commit` forces one.
- Off-machine: any file backup tool works on the JSON; borg dedups well.

## Troubleshooting

- **kg tools offline / connection refused** → `kg doctor`. `kg mcp` starts the
  server and retries on its own, so offline tools mean a start that fails:
  `kg start` prints the cause. A harness still on an old plugin (HTTP URL in
  its MCP config) needs `/mcp` → Reconnect in Claude Code, or a new Codex
  session, after a restart; `kg update` moves it to `kg mcp`.
- **Codex: tools work but no preload or recall** → check hook trust first.
  The **user** runs
  `/hooks` in Codex, trusts the knowledge-graph hooks, starts a new session.
  Verify: the next session opens with the KG MEMORY PRELOADED block.
  Trust is recorded per hook hash: a plugin update that changes the hooks
  (0.10.0 did) needs the approval again.
- **Codex: shell file recall missing** → check hook trust and explicit file
  operands (`cat`, `head`, `tail`, `less`, `sed -n`, `grep`, `jq`, `nl`, `rg`).
  Relative paths need an explicit absolute `workdir` or an exact completed
  command match in the local rollout. Missing/ambiguous directory evidence
  is skipped; use an absolute operand to diagnose it. Implicit directory
  searches and `rg --files` are not tracked.
- **`-32000` / "failed to reconnect"** → the server process died; the code
  is generic. Read `~/.local/state/knowledge-graph/last_start_error` (cause,
  time, log path), then `kg logs`. A `KG PREFLIGHT:` line names an
  incompatible dependency: `kg update` (or `uv tool install --reinstall
  kg-memory`) re-resolves the environment. An OS Python upgrade that broke
  the tool environment has the same remedy.
- **`kg status` reports another version than `kg version`** → the running
  server predates the installed kg: `kg restart`. `kg stop` also stops
  servers found by port that no PID file names.
- **Graph looks stale after direct disk edits** (scripts writing to
  `~/.knowledge-graph` while the server runs) → the server caches graphs in
  memory: `curl -s 'http://127.0.0.1:8765/api/graph/read?reload=true&project_path=<root>'`
  forces a disk reload.
- **Desktop shows no knowledge-graph server** → `kg doctor` checks its entry
  in `~/.config/Claude/claude_desktop_config.json` (Linux) /
  `~/Library/Application Support/Claude/` (macOS); `kg setup --only
  claude-desktop` repairs it. Then full quit + reopen.
- **Log lines that are fine**: `Healed N corrupt node(s) on load` (self-repair
  did its job) · `over budget but all N active nodes within grace —
  compaction deferred` (informational stall notice).

## Uninstall

`kg uninstall` (`--plan` first) reverses what setup did: the service,
permissions, settings and the Desktop entry, backing up each file it changes. Remove the
plugins in each harness (`/plugin uninstall knowledge-graph@maxim-plugins`;
`codex plugin remove knowledge-graph@maxim-plugins`), then `uv tool uninstall
kg-memory`. Shared data in `~/.knowledge-graph/` is preserved.
