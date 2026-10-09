# Antigravity CLI (experimental)

The plugin carries a native Antigravity CLI package next to its Claude Code
and Codex files. All three harnesses talk to the same local memory server, so
what one learns the others recall. Support is **experimental**: core memory
works and its known state defects are fixed, but some lifecycle cases have
not yet been checked in a signed-in session (see [Status](#status)).

| Capability | Antigravity CLI |
|---|---|
| MCP reads, search and writes | All ten KG tools, with eager schemas so the model sees their descriptions |
| Session preload | `SessionStart`; `PreInvocation` initializes a missing binding |
| Prompt recall | Once per human `USER_INPUT`, on `invocationNum: 0` |
| File recall | `view_file`, the three file-edit tools, explicit file reads in `run_command` (uses its `Cwd`) |
| Web capture nudges | `read_url_content` and `search_web` are mapped |
| Large replies | Queued and delivered by the next `PreInvocation` hook in UTF-8 chunks |
| Compaction | A new `CHECKPOINT` in the transcript resets what the session counts as in context and re-queues the preload |
| Budget notices | Wrap-up notices from a live `agy -p /usage` reading |
| Background maintenance | Prompts dispatch chores like Claude Code and Codex; `"runner": "antigravity"` runs them on agy (see [Maintenance runner](#maintenance-runner)) |
| History scouting | `/kg-scout` has an Antigravity recipe |
| IDE and desktop app | Not supported; their hook events are ignored |

## Install

Requires kg-memory 0.12.0 or later (the `kg` command; the server adapter
itself dates from 0.11.0). The simple route is `kg setup`: it installs the
plugin from the kg-memory package (`agy-plugin`) and offers the tool grants
below (`agy-permissions`); `kg update` refreshes both the plugin and the
server. A hook never replaces a running server: if an older one is running,
the session start says it lacks the adapter. Run `kg update`, or `kg restart`
if kg is already current (see [Server management](README.md#server-management)).

The CLI installs plugins from a local directory only: it refuses git URLs,
and third-party marketplaces cannot be registered. To install by hand, use a
clean checkout (a working tree with venvs or caches copies them too):

```bash
git clone https://github.com/mironmax/kg-memory
agy plugin install "$PWD/kg-memory/knowledge-graph"
agy -p /hooks
```

`/hooks` must list `SessionStart`, `PreInvocation` and `PostToolUse`. Then
start a new conversation. Use `plugin install`, not `plugin import`: import
converts the Claude Code files and drops every hook and the MCP entry.

The install is a copy in `~/.gemini/config/plugins/knowledge-graph/`. The
plugin needs the `kg` command (`uv tool install kg-memory`). Start a new
conversation after installing or updating. If no server is running, the first
`SessionStart` starts one through `kg`.

The [recommended Antigravity setup](../recommended-setup/antigravity.md)
adds the working style as a global rule and a quota status line; it is
independent of this plugin.

### Tool permissions

Interactive sessions ask before each MCP tool. For unattended runs, these go
into `permissions.allow` in `~/.gemini/antigravity-cli/settings.json`;
`kg setup` offers to add them (`agy-permissions`), or merge them by hand:

```json
[
  "mcp(knowledge-graph_kg/kg_read)",
  "mcp(knowledge-graph_kg/kg_search)",
  "mcp(knowledge-graph_kg/kg_put_node)",
  "mcp(knowledge-graph_kg/kg_put_edge)",
  "mcp(knowledge-graph_kg/kg_rename_node)",
  "mcp(knowledge-graph_kg/kg_sync)",
  "mcp(knowledge-graph_kg/kg_useful)",
  "mcp(knowledge-graph_kg/kg_progress)"
]
```

Node and edge deletion stay on Ask. The plugin itself grants no permissions
and changes no global settings. It registers no `PreToolUse` hook: in this CLI an
empty reply there denies the tool.

### Maintenance runner

With chores switched on (`/kg-ops`), a human prompt in Antigravity can
dispatch one, as in Claude Code and Codex; the configured runner spends the
quota. `"runner": "antigravity"` in `~/.knowledge-graph/chores.json` runs it on
agy itself. Because agy takes no per-run settings, the run uses your grants:
it is refused unless `permissions.allow` above holds the kg tools, and while
`useG1Credits` could spend paid credits (unless `"antigravity_allow_credits":
true`). Deletions stay on Ask, which headless
agy soft-denies, so an Antigravity run never deletes. The gate is a live
`agy -p /usage` reading for the run's model group; plans with weekly buckets
only skip the five-hour gate. The run is a tool-less `kg-maintainer` agent
in `~/.knowledge-graph/agy-runner/`, refused unless `agy -p /agents` lists it.

## How memory reaches the model

The CLI cuts MCP results short: eager tools deliver about 10 KB whole, but
40 KB and 60 KB results were truncated, and no supported output-size setting
exists. `PreInvocation` hooks delivered 40 KB intact. So:

- A KG reply up to **3,500 UTF-8 bytes** returns inline.
- A larger reply from **any** KG tool returns a short receipt, and the text
  is queued for this conversation. Each `PreInvocation` delivers up to
  **40,000 bytes** including framing. A partial delivery ends with an
  instruction to call `kg_sync`, which triggers the next hook. The queue holds
  1 MiB or 64 replies and refuses overflow without dropping earlier replies.
- Without hook evidence for this conversation, a large reply is refused with
  a setup hint. There is no pagination fallback.

Nothing a reply implies is recorded before the model has it. Session effects
(seen, full-read, preload, sync) and graph effects (the read timestamp, and
promotion of an archived node) commit with an inline reply, or when the hook
acknowledges the final chunk of a queued one. A refused or abandoned reply
changes nothing. Queued snapshots keep their original view time, so a
concurrent edit made after the render still blocks a stale overwrite. Queues
persist across server restarts.

Compaction fires no hook. Every hook therefore scans the transcript lines
appended since its last scan; a new `CHECKPOINT` row means the context was
replaced by a summary. The session then forgets what it counted as shown,
queues a fresh preload ahead of pending replies, and replays those replies
from their start. An acknowledgement for a packet sent before the checkpoint
is refused. Claude Code and Codex get the same reset from their
`SessionStart` hook with `source: compact`.

Conversation ids from MCP `_meta` and from hook payloads select the same KG
session; another conversation's session id is refused. The first workspace
path is the project; without one the session is user-only.

## Status

Verified:

- Native transport on `agy 1.2.15`, `1.2.16` and `1.2.17` with a mock model (the smoke
  below, 11 checks): hooks, rule and eager schemas load; full graph, large
  Unicode node and file recall reach the next model input whole.
- Signed-in `agy 1.2.16` sessions on a real graph: read, search, sync,
  write and endorsement calls, prompt and file recall, queued delivery.
- HTTP boundary tests for identity, deferred effects, refused reads,
  checkpoints during delivery, stale writes, restarts and queue bounds.
- The maintenance runner on `agy 1.2.16` and `1.2.17` with a mock model (the chore
  smoke below, 7 checks).
- Checkpoint detection against the signed-in transcript whose summary lost
  27 of 28 preloaded memories.

Not yet verified in a signed-in session: compaction after this fix,
`--conversation` resume, `/fork`, `/clear`, a restart during a pending read,
quota exhaustion, and a real maintenance run with the kg tools granted (the
runner is verified with a mock model only).

## Development and verification

Use a separate port and storage root so tests never touch the shared server.
In one terminal, from a repository checkout (`knowledge-graph/cli/kg-dev`
builds the venv at `knowledge-graph/server/venv` on first use):

```bash
export KG_HTTP_PORT=8767 KG_STORAGE_ROOT="$PWD/devdocs/antigravity-memory" KG_AUTOCOMMIT_INTERVAL=0
knowledge-graph/cli/kg-dev serve
```

In another, stage a copy whose `kg mcp` entry carries that port
(`KG_HTTP_PORT` in its env) and install it:

```bash
export KG_HTTP_PORT=8767 KG_STORAGE_ROOT="$PWD/devdocs/antigravity-memory"
agy plugin install "$(python3 docs/harnesses/tools/antigravity-probe/stage_plugin.py --port 8767)"
agy
```

The exported port steers the hooks and the staged env steers the tools; they
must agree. The staged entry still runs whichever `kg` is on PATH, so keep the
development server running on that port before opening a conversation. Reinstall a fresh staged copy each iteration, restart the server
after Python changes, and open a new conversation.

Boundary tests (the adapter, then the budget gauge and maintenance runner):

```bash
knowledge-graph/server/venv/bin/python knowledge-graph/server/tests/test_antigravity.py
knowledge-graph/server/venv/bin/python knowledge-graph/server/tests/test_budget_and_agy_runner.py
```

Native smoke (Linux, bubblewrap). It overlays a scratch home, so the venv
must live inside the checkout, as above:

```bash
knowledge-graph/server/venv/bin/python docs/harnesses/tools/antigravity-probe/kg_smoke.py --agy /path/to/agy
```

It uses dummy credentials, a scripted model and a native install, and checks
transport and bookkeeping, not how a real model uses memory.

Maintenance-runner smoke: the chore wrapper against a real agy, a mock model
and a real server (7 checks, among them: the agent loads, only MCP tools are
offered, `kg_read` reaches the server, hooks stand down, the run stays in its
own workspace). The agy binary must also live outside `$HOME`:

```bash
knowledge-graph/server/venv/bin/python docs/harnesses/tools/antigravity-probe/chore_smoke.py --agy /path/outside/home/agy
```

Signed-in checklist before calling support stable:

1. Fresh conversation on a mature graph: preload, one full read, the memory
   announcement, and a question answered from a node.
2. A large node or batch: every part arrives, and the model follows the
   continuation instruction before writing.
3. File recall after creating a node that touches a file, including
   `run_command` in a nested directory.
4. Two conversations in one project keep separate sessions and queues;
   restart the server during a pending read.
5. Resume with `--conversation`, `/fork`, `/clear`, and a compaction:
   record the hook payloads and check that the preload returns after the
   checkpoint.
6. Web tools and a denied tool: pending context survives the next prompt,
   and the CLI log shows no rejected hook reply.

Research behind this adapter, as dated records: the
[mapping](../docs/harnesses/antigravity-mapping.md) from the Codex
integration, the [harness card](../docs/harnesses/cards/antigravity.md) and the
[probe tools](../docs/harnesses/tools/antigravity-probe/README.md).
