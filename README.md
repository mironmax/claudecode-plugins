# kg-memory: persistent memory for coding agents

Your coding agent forgets everything when a session ends. kg-memory gives it a memory that lasts: how your project fits together, what was decided and why, how you like to work, and which mistakes were already made once. Claude Code and Codex CLI share it; Antigravity CLI support is experimental.

It is free and open source (MIT), and it stays that way: no paid tier, no account, no API key, no telemetry. The memory lives in plain files on your machine.

![Knowledge Graph in action](docs/knowledge-graph-demo.gif)

## Why it's worth trying

Without memory, every session starts from zero. The agent re-reads the files it read yesterday, re-derives the conclusions it already reached, and you explain your preferences again. kg-memory keeps what the agent learns and brings it back without being asked:

- **At session start**, the most important memories are already in context.
- **When you type a prompt**, memories that match it arrive with the prompt.
- **When the agent reads or edits a file**, what it learned about that file arrives with the tool result, usually before it changes anything.
- **When another session writes something relevant**, yours is told, instead of writing the same lesson twice.

What makes it more than a notes file:

- **Lessons, not logs.** The agent writes a node at the moment it learns something: a one-line gist that states the lesson, notes with the cases behind it, pointers into the files, and typed edges to related nodes. Next time it reads the conclusion instead of working it out again.
- **It stays small and sharp.** Each session sees a fixed budget of memory. What gets used, endorsed and connected stays on top; the rest moves to an archive that is still searchable and one read away.
- **It looks after itself.** The graph reports its own upkeep debt (bloated entries, file pointers that no longer resolve, repeated episodes that should become one principle). A maintenance pass pays it down, run by you or in the background on spare quota.
- **One memory for all your agents.** Claude Code and Codex talk to one local server, so what one learns the other knows.
- **Yours to inspect.** Plain JSON under `~/.knowledge-graph/`, versioned with git if you want history, browsable in a visual editor (`kg editor`).

## What a day with it looks like

You don't operate the graph; the agent does. You mostly notice it in the answers.

- **Morning.** You open a session. The preload is in context before your first message; the agent reads the rest of the memory once and says *"I have recalled KG Memories"*.
- **During work.** You ask about the deploy script, and the note about its one non-obvious flag arrives with your question. The agent reads a config file, and the memory that says "generated, edit the template instead" arrives with it.
- **When something is learned.** A bug is traced to its cause, or you state a preference. The agent writes it down there and then. You can also just say "remember that…".
- **Wrapping up.** Tell the agent you're wrapping up. It's the natural moment for it to write down what the session learned and credit the memories that actually helped, which keeps them on top.
- **Over weeks.** Memories nobody uses sink into the archive; the ones that keep helping stay visible. When upkeep debt runs high, the memory flags it; `/kg-maintain` runs a bounded cleanup, or background upkeep does it for you.

## How mature it is

An honest account, so you can decide what to expect.

**Solid.** kg-memory has been in daily use on real projects since spring 2026, across about 60 releases. Claude Code is the primary harness. Codex CLI runs the same plugin against the same memory and is verified against real Codex sessions. Every change reaches `main` through a pull request with the test suite green in CI (Linux on Python 3.10 and the newest Python, plus macOS). The concurrent and stateful parts (parallel writes, session forks, renames, saves) were modelled in Lean, and each defect the models found was reproduced against the real code before it was fixed ([formal/](formal/)). Saves are atomic, with a rolling backup and optional git history.

**Measured, within limits.** Every recall decision and every endorsement is logged locally, and a replay evaluator scores ranking changes against that record. Changes to scoring and budgets are replayed on copies of real graphs before release. What does not exist yet is a controlled benchmark of how much the memory improves an agent's results. The evidence so far is daily use and these logs, not a headline number.

**Still moving.** It is pre-1.0: minor releases still retune scoring, budgets and the instructions the agent follows, each recorded in the [changelog](CHANGELOG.md). Antigravity CLI support is experimental. Windows is not supported (the `kg` command and the hooks need a POSIX system). The macOS CI job reports without blocking while one intermittent failure is investigated. The background service is set up on Linux (systemd); elsewhere the session hook starts the server on demand.

**What it costs.** Context: on a mature graph, the preload plus one full read come to at most about 60,000 characters (roughly 15,000 tokens of English text). Quota: background upkeep runs headless agent sessions on your subscription, which is why it is off until you switch it on and runs only while quota is spare. Privacy: memories the agent reads are part of its context, so they reach your model provider like any other context.

## Install

```bash
uv tool install kg-memory     # needs uv: https://docs.astral.sh/uv/
kg setup                      # asks before each change, backs up what it edits
```

No uv yet? `curl -LsSf https://raw.githubusercontent.com/mironmax/kg-memory/main/install.sh | sh` installs it and runs both steps. Asking an agent to install it? Point it at [INSTALL.md](INSTALL.md).

`kg setup` finds Claude Code, Codex CLI, Antigravity CLI and Claude Desktop, and offers each piece separately: the plugin and its auto-update, pre-approved memory tools, Claude Code's built-in auto-memory turned off (two memory systems write conflicting entries), a quota gauge in the status line, one local memory server for all of them, and background upkeep. `kg setup --plan` lists the items without changing anything; `kg doctor` checks everything later; `kg update` keeps it current; `kg uninstall` reverses setup and keeps your memory.

Two steps are yours: in Codex, run `/hooks` and trust the knowledge-graph hooks (Codex keeps plugin hooks off until you approve them); fully quit and reopen Claude Desktop. Then start a new session. Platform notes: [Codex](knowledge-graph/README.md#codex-cli) · [Antigravity](knowledge-graph/ANTIGRAVITY.md).

Requires Python 3.10+ (uv provides it).

## Your first five minutes

1. **Start any session.** The preload arrives with it, and after its first full read the agent says *"I have recalled KG Memories"*. The memory is empty at first; that's normal.
2. **Just work.** The agent captures decisions, preferences, debugging discoveries and how your codebase fits together as they come up.
3. **Seed it faster (optional).** `/kg-extract` maps your codebase architecture into the graph; `/kg-scout` mines your past Claude Code, Codex and Antigravity sessions for knowledge you've already paid for.
4. **Next session, ask** *"What do you remember about this project?"*

**Also worth adopting:** the **[recommended setup](recommended-setup/)**, an output style carrying a working agreement plus a status line. Its earlier response rules measured −27% output tokens at equal or better quality; the current combined style has not been re-benchmarked. Versions exist for [Codex](recommended-setup/codex.md) and [Antigravity](recommended-setup/antigravity.md).

## Documentation

- **[Plugin guide](knowledge-graph/README.md)**: what the memory does on its own, everyday commands, configuration, data and backups.
- **[Architecture](knowledge-graph/ARCHITECTURE.md)**: the design and how each mechanism works.
- **[Antigravity CLI](knowledge-graph/ANTIGRAVITY.md)** · **[Visual editor](knowledge-graph/VISUAL_EDITOR_GUIDE.md)** · **[Security](SECURITY.md)** · **[Changelog](CHANGELOG.md)** · **[Wiki](https://github.com/mironmax/kg-memory/wiki)**

These docs track the current source on `main`; the changelog marks release boundaries. If kg-memory helps you, a ⭐ helps others find it.

---

## More plugins

This is the `maxim-plugins` marketplace. Knowledge Graph is its first plugin.

## Contributing

Issues and pull requests are welcome. Every pull request runs the test suite in CI on Linux (Python 3.10 and the newest release) and, report-only for now, macOS. Release rules are in [RELEASING.md](RELEASING.md); to add a plugin to the marketplace, update `.claude-plugin/marketplace.json`.

## License

Each plugin has its own license; Knowledge Graph is [MIT](knowledge-graph/LICENSE).
