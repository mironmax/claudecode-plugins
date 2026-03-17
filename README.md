# Maxim's Claude Code Plugins Marketplace

A collection of Claude Code plugins for enhanced development workflows.

## Available Plugins

### Memory (Knowledge Graph)
Extract and remember patterns, insights, and relationships worth preserving across sessions.

**Features:**
- 🧠 Capture knowledge as you work
- ⚡ Fast in-memory operations (persistent MCP server)
- 🔄 Session tracking with diff-based sync
- 🤝 Real-time multi-session collaboration
- 🎯 User & Project level knowledge separation
- 📝 Immediate capture with conflict resolution
- 🗜️ Auto-compaction to manage context window size
- ♻️ Smart archiving with recoverable nodes

**Location:** `knowledge-graph/` in this marketplace repository

## Installation

### 1. Add This Marketplace

```
/plugin marketplace add mironmax/claudecode-plugins
```

### 2. Install Plugins

```
/plugin install knowledge-graph@maxim-plugins
```

### 3. Set Up CLAUDE.md

Add the knowledge graph instructions to your global Claude configuration:

```bash
# If you don't have ~/.claude/CLAUDE.md yet:
cp ~/.claude/plugins/knowledge-graph/templates/CLAUDE.md ~/.claude/CLAUDE.md

# If you already have ~/.claude/CLAUDE.md:
# Append the template content to your existing file
```

**Why this matters:** The template tells Claude to auto-load the knowledge graph at session start. Without it, you'll need to manually call tools each session.

**Important:** Use only this one global `~/.claude/CLAUDE.md`. Avoid project-level CLAUDE.md files in individual repos — they create contradicting instructions and bloat context. The knowledge graph is designed to replace that need.

### 4. Disable Built-in Auto-Memory

Claude Code has a built-in auto-memory system that runs in parallel with the knowledge graph, causing duplicate memory and wasted context. Disable it in **⚙ Settings → Memory → Auto-memory** (toggle off).

### 5. Restart Claude Code

The plugin will be available after restart.

## Manual Installation

If you prefer manual installation, see each plugin's repository for instructions.

## Contributing

Have a plugin to add? Open a PR with updates to `.claude-plugin/marketplace.json`

## License

Each plugin has its own license. See individual plugin repositories for details.
