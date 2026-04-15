# Maxim's Claude Code Plugins

A marketplace of Claude Code plugins for persistent memory and enhanced workflows.

## Available Plugins

### Knowledge Graph

Gives Claude a persistent memory that survives across sessions — not just flat notes, but a graph of nodes and relationships. Claude distills insights as you work and recalls them automatically next session.

**Location:** `knowledge-graph/` · **[Full documentation →](knowledge-graph/README.md)**

---

## Quick Install

```
/plugin marketplace add mironmax/claudecode-plugins
/plugin install knowledge-graph@maxim-plugins
bash ~/.claude/plugins/knowledge-graph/install_command.sh
```

Restart Claude Code. Done.

**Also recommended:** disable built-in auto-memory — ⚙ Settings → Memory → toggle Auto-memory **off**. Otherwise two memory systems run in parallel and write conflicting entries.

See the [knowledge-graph README](knowledge-graph/README.md) and the [wiki](https://github.com/mironmax/claudecode-plugins/wiki) for full setup, configuration, and usage details.

---

## Contributing

Have a plugin to add? Open a PR with updates to `.claude-plugin/marketplace.json`.

## License

Each plugin has its own license. See individual plugin directories for details.
