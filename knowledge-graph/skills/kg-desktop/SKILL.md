---
name: kg-desktop
user-invocable: true
description: |
  Connect Claude Desktop (including Cowork) to the same knowledge-graph memory
  this plugin serves in Claude Code. Registers the server in
  claude_desktop_config.json via a stdio bridge — Desktop's connector UI only
  accepts public https URLs (those connect from Anthropic's cloud), so the
  config file is the correct path for a local server. Use when the user wants
  KG memory in Claude Desktop, or asks why the connector dialog rejects the
  local URL.
---

# Connect Claude Desktop to the knowledge graph

Desktop is just another client of the shared HTTP server — same graph, same
memory as every Claude Code session. The bridge auto-starts the server when
Desktop launches first.

## Steps

1. Run the setup script:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/setup_desktop.py"
   ```

   It resolves absolute paths for this machine (Desktop spawns commands
   without a shell — no `~`, unreliable PATH), backs up any existing config,
   and merges the entry idempotently. Non-default port: `--port N`.

2. If it exits with "npx not found": the bridge runs on `mcp-remote`, which
   needs Node.js ≥ 18. Have the user install Node, then rerun.

3. Verify the bridge works before involving Desktop (expect
   "Proxy established" within a few seconds, then kill it):

   ```bash
   timeout 12 npx -y mcp-remote http://127.0.0.1:8765/ --allow-http 2>&1 | head -10
   ```

4. Tell the user: **fully quit** Claude Desktop (not just the window) and
   reopen. The `knowledge-graph` server with `kg_*` tools appears in the chat
   tool list. Cowork sessions receive config-file servers through Desktop's
   own bridge into the sandbox.

5. Removal: `python3 "${CLAUDE_PLUGIN_ROOT}/setup_desktop.py" --remove`.

## Caveats

- Desktop sessions have no SessionStart hook — memory is NOT preloaded there.
  The first `kg_read` call does the orientation instead.
- Windows: the auto-start wrapper is skipped (no bash); the server must
  already be running — any Claude Code session starts it.
- The Desktop config points at the stable `~/.local/bin/kg-desktop-bridge`
  symlink (plugin cache dirs are versioned). `install_command.sh` refreshes
  it after plugin updates; rerunning this skill does too.
