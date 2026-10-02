# Antigravity CLI — experimental first iteration

This branch adds the native CLI adapter on top of PR #39's research. It has
been exercised with `agy 1.2.15`, a local mock Gemini model and scratch graphs.
It is ready for local development and signed-in testing; it is not a tagged
release. Claude Code and Codex continue using their existing plugin files.

| Capability | First iteration |
|---|---|
| MCP reads, search and writes | All ten KG tools; native eager schemas expose their descriptions |
| Session preload | `SessionStart`; `PreInvocation` can initialize a missing binding |
| Prompt recall | Once per actual `USER_INPUT`, on `invocationNum: 0` |
| File recall | `view_file`, three file edit tools, explicit file reads via `run_command.Cwd` |
| Web capture | `read_url_content` and `search_web` mapped; real-model use still to check |
| Large replies | Same-conversation hook delivery with UTF-8 chunking and deferred read/sync state |
| Maintenance and Antigravity history scouting | Deferred; Antigravity hooks never trigger a background runner |
| Desktop, IDE, subagent lifecycle, compaction, fork and clear | Not validated by this CLI iteration |

## Local development without occupying the shared server

The development branch is `codex/antigravity-cli`. Its worktree can stay open
while other branches advance. Use a separate port and storage root for these
iterations. Commands below start in this worktree's repository root.

Prepare the server environment (already prepared in the initial worktree):

```bash
python3 -m venv knowledge-graph/server/venv
knowledge-graph/server/venv/bin/python -m pip install -r knowledge-graph/server/requirements.txt
```

The initial worktree also contains the verified `agy 1.2.15` binary at
`devdocs/bin/agy`. To use it for the commands below in either terminal:

```bash
export PATH="$PWD/devdocs/bin:$PATH"
```

In one terminal, run the branch's server in the foreground:

```bash
export KG_HTTP_PORT=8767
export KG_STORAGE_ROOT="$PWD/devdocs/antigravity-memory"
export KG_AUTOCOMMIT_INTERVAL=0
knowledge-graph/server/venv/bin/python knowledge-graph/server/mcp_streamable_server.py
```

In another terminal in the same repository, stage and install a native copy
whose MCP URL points at port 8767:

```bash
export KG_HTTP_PORT=8767
export KG_STORAGE_ROOT="$PWD/devdocs/antigravity-memory"
kg_agy_plugin_dir=$(python3 docs/harnesses/tools/antigravity-probe/stage_plugin.py --port 8767)
agy plugin install "$kg_agy_plugin_dir"
agy -p /hooks
agy
```

`agy plugin install` copies into `~/.gemini/config/plugins/knowledge-graph/`.
Check that `/hooks` lists `SessionStart`, `PreInvocation` and `PostToolUse`.
The port environment controls the hooks; the staged MCP URL controls the
tools, so both must agree. Every iteration should reinstall a fresh staged
copy, restart the foreground server if its Python code changed, and open a
new CLI conversation. The staging command leaves previous copies available
under the ignored `devdocs/antigravity-plugins/` directory.

For a normal port-8765 installation, install `knowledge-graph/` directly with
`agy plugin install /absolute/path/to/knowledge-graph`. Start the server from
this branch before opening the CLI. A healthy older shared server is left
running by hooks and does not acquire this adapter merely by installing it.
The optional shell helpers can be installed with
`bash ~/.gemini/config/plugins/knowledge-graph/install_command.sh`.

## Tool permissions

Interactive CLI sessions ask for MCP permission. Headless tests need explicit
grants. For unattended local tests, merge the following entries into
`permissions.allow` in `~/.gemini/antigravity-cli/settings.json`, keeping any
other settings and grants:

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

This example leaves node and edge deletion on Ask. The plugin neither grants
permissions nor changes global settings itself. It does not need a
`PreToolUse` hook; an empty result there would deny tools in this CLI version.

## Verification

Run the HTTP MCP/REST boundary tests:

```bash
knowledge-graph/server/venv/bin/python knowledge-graph/server/tests/test_antigravity.py
```

Run the native CLI smoke on Linux with bubblewrap installed:

```bash
knowledge-graph/server/venv/bin/python docs/harnesses/tools/antigravity-probe/kg_smoke.py --agy /path/to/agy
```

This uses a scratch home overlay, dummy API credentials, a local mock model,
the branch's real server and a native plugin install. It checks hook loading,
rule/schema loading, full-graph delivery, a large Unicode node, continuation
and file recall in the next model input. It prints the retained `/tmp/`
artifact directory. The model is scripted, so this checks transport and
bookkeeping rather than a real model's memory use. CI still runs every
`knowledge-graph/server/tests/test_*.py` on Python 3.10 and the latest Python.

Signed-in local checklist before calling the adapter stable:

1. Start a fresh conversation in a project with a mature graph. Verify the
   preload, one full read, and the memory announcement; ask a question whose
   answer is in a node and check that the model uses it.
2. Read a large node or batch. Verify that every part arrives and that the
   model follows the continuation instruction before writing an update.
3. Create a node touching a file after the full read, then have the CLI read
   and edit that file. Verify recall; test `run_command` in a nested directory.
4. Open two conversations in one project. Verify each keeps its own session
   id and queued replies. Restart the development server during a pending read.
5. Resume with `--conversation`, then separately exercise `/fork`, `/clear`
   and compaction. Record their hook payloads and whether preload/read state
   actually survives; these lifecycle cases remain release gates.
6. Exercise web tools and a denied tool. Confirm that pending context survives
   the next prompt, and that the CLI log reports no rejected hook result.

## Read-path contract

`tools: {kg_read: {eager: true}, ...}` is the per-tool MCP configuration. Eager
mode exposes descriptions and delivered a 10 KB result whole in the probe,
but 40 KB and 60 KB results still truncated. No supported CLI output-token
setting was found in `/config` or the official settings docs. Internal
`max_output_bytes` protobuf fields are not evidence of a user-facing override.

The adapter uses a conservative 3,500-byte inline ceiling. Larger output
from **any** KG tool becomes a small receipt plus persistent queued context.
Hooks deliver at most 40,000 UTF-8 bytes including framing per invocation.
The queue holds at most 1 MiB/64 replies and refuses overflow without
discarding earlier replies. Continuation asks for `kg_sync` to trigger the
next hook. A new prompt does not discard a pending reply.

Each queued snapshot carries its original read timestamp. Seen, full-read,
promotion, preload and sync changes commit after the adapter writes the
corresponding final chunk to stdout and acknowledges it. A failed round trip
retries the same packet. A later concurrent edit therefore remains newer than
an old snapshot even when that snapshot was delivered late. Queues and their
effects persist with the session across server restarts.

Conversation ids from MCP `_meta` and hook payloads select the same KG
binding. Another conversation's session id is refused. Hooks are required
for large output: with no hook evidence for this conversation, a large reply
returns a setup hint and stays unread/unsynced. There is no pagination fallback
in v1. Workspace selection uses the first path; hook process cwd is never
used as the project. Folderless sessions are user-only.

The [mapping](../docs/harnesses/antigravity-mapping.md) and
[card](../docs/harnesses/cards/antigravity.md) retain
the original 1.2.14 observations. This guide records the implemented decisions
and the 1.2.15 follow-up. Maintenance, quota, paid-credit policy and real-model
lifecycle validation remain separate work.
