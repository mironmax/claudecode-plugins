# Knowledge Graph memory for Claude Code and Codex

Persistent memory for coding agents: the agent remembers across sessions as a graph of nodes and typed relationships, not flat notes. It captures insights as you work, preloads them next session, and brings back the right memory when a prompt or a file needs it. The graph also tracks when it needs tending, and can tend itself in the background. Claude Code and Codex CLI share one memory, so what one learns the other knows.

![Knowledge Graph in action](docs/knowledge-graph-demo.gif)

## Install

**Claude Code**

```bash
/plugin marketplace add mironmax/claudecode-plugins
/plugin install knowledge-graph@maxim-plugins
```

Restart Claude Code. The plugin starts its local memory server by itself; the very first session sets up a Python environment (~1 minute). If Claude reports the memory tools offline, run `/mcp` → `plugin:knowledge-graph:kg` → **Reconnect** once it is up.

**Codex CLI**

```bash
codex plugin marketplace add mironmax/claudecode-plugins
codex plugin add knowledge-graph@maxim-plugins
```

Then, in Codex, run `/hooks` and trust the knowledge-graph hooks. Codex keeps a plugin's hooks off until you approve them, and without them there is no preload or recall. Start a new session.

See the [Codex support table and update steps](knowledge-graph/README.md#codex-cli), including the shell-path, history-scout and visual-editor limits.

Requires Python 3.10+. No databases, no API keys; everything stays on your machine.

**[Full documentation →](knowledge-graph/README.md)** · **[Wiki →](https://github.com/mironmax/claudecode-plugins/wiki)** · ⭐ Star if useful — it helps others find this

## Your first five minutes

You don't operate the graph; the agent does. After install:

1. **Start any session.** The memory preload arrives with it, and after its first full read the agent says *"I have recalled KG Memories"*. Empty at first; that's normal.
2. **Just work.** The agent captures insights as you go: decisions, preferences, debugging discoveries, how your codebase fits together.
3. **Seed it faster (optional):** `/kg-extract` maps your codebase architecture into the graph; `/kg-scout` mines your past Claude Code sessions for knowledge you've already paid for.
4. **Next session, ask:** *"What do you remember about this project?"* That's the moment it clicks.

**Also recommended:**
- Turn off the harness's own memory, or two memory systems write conflicting entries. Claude Code: ⚙ Settings → Memory → Auto-memory **off**. Codex: its `memories` feature is off by default; leave it off.
- Enable plugin auto-updates in Claude Code: `/plugin` → **Marketplaces** → `maxim-plugins` → **Enable auto-update** (off by default for third-party marketplaces).
- Adopt the **[recommended user-level setup](recommended-setup/)** for Claude Code: an output style carrying a working agreement, and a status line, which pair well with graph memory. Its parts were benchmarked at better answer quality with −27% output tokens; it sets a collaboration tone worth remembering, and the quota gauge lets the agent pace long sessions instead of stopping mid-edit. For Codex, the **[Codex setup](recommended-setup/codex.md)** loads the same style as developer instructions and sets up the footer for limits and context.

---

## More plugins

This is the `maxim-plugins` marketplace. Knowledge Graph is the first plugin; more will follow.

## Contributing

Have a plugin to add? Open a PR with updates to `.claude-plugin/marketplace.json`. Every pull request runs the test suite on Python 3.10 and the newest release.

## License

Each plugin has its own license. See individual plugin directories for details.
