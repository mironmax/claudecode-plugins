# Installing kg-memory (for agents)

Follow this when a user asks you to install or repair the knowledge-graph memory.

1. Install the command. With `uv` present: `uv tool install kg-memory`.
   Without it, ask the user before running the bootstrap, which installs uv
   first: `curl -LsSf https://raw.githubusercontent.com/mironmax/kg-memory/main/install.sh | sh`.
2. See what setup would change: `kg setup --plan`. Each line has a key in
   brackets. Show the list to the user and ask which items to apply.
3. Apply only what the user agreed to: `kg setup --yes --only KEY,KEY`. Every
   file it edits is backed up under `~/.local/state/knowledge-graph/backups/`.
   If your harness refuses to run it (it edits harness settings), ask the user
   to run `kg setup` in their own terminal.
4. Verify: `kg doctor` lists every piece; fix what it marks, then rerun it.
5. Tell the user the steps only they can take: in Codex, `/hooks` → trust the
   knowledge-graph hooks; fully quit and reopen Claude Desktop; then start a
   new session in each harness.

Updates: `kg update`. Removal: `kg uninstall --plan`, then `kg uninstall`
(memory in `~/.knowledge-graph/` is kept). Operations and troubleshooting:
the `kg-ops` skill.
