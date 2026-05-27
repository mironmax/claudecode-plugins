# Knowledge Graph for Claude Code

Persistent memory for Claude Code — Claude remembers across sessions as a graph of nodes and typed relationships, not flat notes. Distills insights as you work, recalls the right context automatically next session.

![Knowledge Graph in action](docs/knowledge-graph-demo.gif)

## Install

```bash
/plugin marketplace add mironmax/claudecode-plugins
/plugin install knowledge-graph@maxim-plugins
```

Restart Claude Code. Done.

**[Full documentation →](knowledge-graph/README.md)** · **[Wiki →](https://github.com/mironmax/claudecode-plugins/wiki)** · ⭐ Star if useful — it helps others find this

**Also recommended:**
- Disable built-in auto-memory — ⚙ Settings → Memory → toggle Auto-memory **off**. Otherwise two memory systems run in parallel and write conflicting entries.
- Enable plugin auto-updates — run `/plugin`, pick **Marketplaces** → `maxim-plugins` → **Enable auto-update**. Off by default for third-party marketplaces.

---

## More plugins

This is the `maxim-plugins` marketplace. Knowledge Graph is the first plugin; more will follow.

## Contributing

Have a plugin to add? Open a PR with updates to `.claude-plugin/marketplace.json`.

## License

Each plugin has its own license. See individual plugin directories for details.
