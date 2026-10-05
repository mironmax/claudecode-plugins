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
| Background maintenance, history scouting, quota gate | Not available: Antigravity hooks never dispatch a runner |
| IDE and desktop app | Not supported; their hook events are ignored |

## Install

Requires memory server 0.11.0 or later. A hook never
replaces a running server: if an older one is running, the session start
says it lacks the adapter. Restart it from the updated plugin
(`kg-memory restart`, see [Server Management](README.md#server-management)).

The CLI installs plugins from a local directory only: it refuses git URLs,
and third-party marketplaces cannot be registered. Install from a checkout,
or from the copy Claude Code or Codex already installed:

```bash
git clone https://github.com/mironmax/claudecode-plugins
agy plugin install "$PWD/claudecode-plugins/knowledge-graph"
agy -p /hooks
```

`/hooks` must list `SessionStart`, `PreInvocation` and `PostToolUse`. Then
start a new conversation. Use `plugin install`, not `plugin import`: import
converts the Claude Code files and drops every hook and the MCP URL.

The install is a copy in `~/.gemini/config/plugins/knowledge-graph/`. To
update, pull the checkout and install again, then start a new conversation.
If no server is running, the first `SessionStart` starts one from that copy.
The optional shell helpers: `bash ~/.gemini/config/plugins/knowledge-graph/install_command.sh`.

The [recommended Antigravity setup](../recommended-setup/antigravity.md)
adds the working style as a global rule and a quota status line; it is
independent of this plugin.

### Tool permissions

Interactive sessions ask before each MCP tool. For unattended runs, merge
these into `permissions.allow` in `~/.gemini/antigravity-cli/settings.json`:

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

Node and edge deletion stay on Ask. The plugin grants no permissions and
changes no global settings. It registers no `PreToolUse` hook: in this CLI an
empty reply there denies the tool.

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

- Native transport on `agy 1.2.15` and `1.2.16` with a mock model (the smoke
  below, 11 checks): hooks, rule and eager schemas load; full graph, large
  Unicode node and file recall reach the next model input whole.
- Signed-in `agy 1.2.16` sessions on a real graph: read, search, sync,
  write and endorsement calls, prompt and file recall, queued delivery.
- HTTP boundary tests for identity, deferred effects, refused reads,
  checkpoints during delivery, stale writes, restarts and queue bounds.
- Checkpoint detection against the signed-in transcript whose summary lost
  27 of 28 preloaded memories.

Not yet verified in a signed-in session: compaction after this fix,
`--conversation` resume, `/fork`, `/clear`, a restart during a pending read,
and quota exhaustion. Antigravity has no maintenance runner, quota gate or
history-scout recipe.

## Development and verification

Use a separate port and storage root so tests never touch the shared server.
In one terminal, from a repository checkout with the server venv prepared:

```bash
export KG_HTTP_PORT=8767 KG_STORAGE_ROOT="$PWD/devdocs/antigravity-memory" KG_AUTOCOMMIT_INTERVAL=0
knowledge-graph/server/venv/bin/python knowledge-graph/server/mcp_streamable_server.py
```

In another, stage a copy whose MCP URL points at that port and install it:

```bash
export KG_HTTP_PORT=8767 KG_STORAGE_ROOT="$PWD/devdocs/antigravity-memory"
agy plugin install "$(python3 docs/harnesses/tools/antigravity-probe/stage_plugin.py --port 8767)"
agy
```

The port variable steers the hooks and the staged URL steers the tools; they
must agree. Reinstall a fresh staged copy each iteration, restart the server
after Python changes, and open a new conversation.

Boundary tests:

```bash
knowledge-graph/server/venv/bin/python knowledge-graph/server/tests/test_antigravity.py
```

Native smoke (Linux, bubblewrap). It overlays a scratch home, so the venv
must live inside the checkout, as above:

```bash
knowledge-graph/server/venv/bin/python docs/harnesses/tools/antigravity-probe/kg_smoke.py --agy /path/to/agy
```

It uses dummy credentials, a scripted model and a native install, and checks
transport and bookkeeping, not how a real model uses memory.

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
