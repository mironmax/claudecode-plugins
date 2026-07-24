# Knowledge Graph for Claude Code

Persistent memory for Claude Code — Claude remembers across sessions as a graph of nodes and typed relationships, not flat notes. Distills insights as you work, preloads them next session, and surfaces the right memory at the moment each prompt needs it — the graph even tells Claude when it needs tending.

![Knowledge Graph in action](docs/knowledge-graph-demo.gif)

## Install

```bash
/plugin marketplace add mironmax/claudecode-plugins
/plugin install knowledge-graph@maxim-plugins
```

Restart Claude Code. Done — the plugin starts its local memory server automatically (the very first session sets up a Python environment, ~1 minute; if Claude reports the memory tools offline, run `/mcp` → `plugin:knowledge-graph:kg` → **Reconnect** once it's up).

Requires Python 3.10+. No databases, no API keys, everything stays on your machine.

**[Full documentation →](knowledge-graph/README.md)** · **[Wiki →](https://github.com/mironmax/claudecode-plugins/wiki)** · ⭐ Star if useful — it helps others find this

## Your first five minutes

You don't operate the graph — Claude does. After install:

1. **Start any session.** Claude calls the graph and says *"I have recalled KG Memories"* — empty at first, that's normal.
2. **Just work.** Claude captures insights as you go: decisions, preferences, debugging discoveries, how your codebase fits together.
3. **Seed it faster (optional):** `/kg-extract` maps your codebase architecture into the graph; `/kg-scout` mines your past Claude Code sessions for knowledge you've already paid for.
4. **Next session, ask:** *"What do you remember about this project?"* — that's the moment it clicks.

**Also recommended:**
- Disable built-in auto-memory — ⚙ Settings → Memory → toggle Auto-memory **off**. Otherwise two memory systems run in parallel and write conflicting entries.
- Enable plugin auto-updates — run `/plugin`, pick **Marketplaces** → `maxim-plugins` → **Enable auto-update**. Off by default for third-party marketplaces.
- Adopt the **[recommended user-level setup](recommended-setup/)** — a benchmarked `~/.claude/CLAUDE.md` working agreement + output style that pair well with graph memory: better answer quality at −27% output tokens, and a collaboration tone worth remembering.

---

## More plugins

This is the `maxim-plugins` marketplace. Knowledge Graph is the first plugin; more will follow.

## Contributing

Have a plugin to add? Open a PR with updates to `.claude-plugin/marketplace.json`.

## License

Each plugin has its own license. See individual plugin directories for details.
