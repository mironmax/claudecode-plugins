# kg-memory: persistent memory for coding agents

Your coding agent forgets everything when a session ends. kg-memory gives it a memory that lasts: how your project fits together, what was decided and why, how you like to work, and which mistakes were already made once. Claude Code and Codex CLI share it; Antigravity CLI support is experimental.

It installs as a plugin for your agent plus a small server on your machine that holds the memory. It is free and open source (MIT), and it stays that way: no paid tier, no account, no API key, no telemetry. What it costs you is some context in each session and, only if you switch on background upkeep, some of your subscription quota ([details](#what-it-costs)).

![kg-memory in action](docs/knowledge-graph-demo.gif)

## Why it's worth trying

Without memory, every session starts from zero. The agent re-reads the files it read yesterday, re-derives the conclusions it already reached, and you explain your preferences again. kg-memory keeps what the agent learns and brings it back without being asked:

- **At session start**, the most important memories are already in context.
- **When you type a prompt**, memories that match it arrive with the prompt.
- **When the agent reads or edits a file**, what it learned about that file arrives with the tool result, usually before it changes anything.
- **When another session writes something relevant**, yours is told, instead of writing the same lesson twice.

What makes it more than a notes file:

- **Lessons, not logs.** Each memory is a small node: a one-line lesson (the *gist*), notes with the cases behind it, pointers into the files it concerns, and named links to related memories. That's why it's called a knowledge graph. The agent writes one at the moment it learns something, so next time it reads the conclusion instead of working it out again.
- **It stays small.** Each session sees a fixed budget of memory. What gets used, credited and connected stays on top; the rest moves to an archive that is still searchable and one read away.
- **It tells you when it needs tidying.** The memory tracks its own upkeep: overlong entries, file pointers that no longer resolve, repeated incidents that should become one lesson. A maintenance pass cleans it up, run by you or, if you switch it on, in the background.
- **One memory for all your agents.** Claude Code and Codex talk to the same local server, so what one learns the other knows.
- **Yours to inspect.** Plain JSON under `~/.knowledge-graph/`, versioned with git if you want history, browsable in a visual editor (`kg editor`).

## What a day with it looks like

You don't operate the memory; the agent does. You mostly notice it in the answers.

- **Morning.** You open a session. The most useful memories are loaded before your first message; the agent reads the rest once and says *"I have recalled KG Memories"*.
- **During work.** You ask about the deploy script, and the note about its one non-obvious flag arrives with your question. The agent reads a config file, and the memory that says "generated, edit the template instead" arrives with it.
- **When something is learned.** The agent traces a failing test to a timezone assumption in a fixture, or you say you prefer small commits. It writes that down there and then. You can also just say "remember that…".
- **When memory is wrong or missing.** Correct the agent as you would anyway. It updates the lesson. If the memory existed but didn't surface, the agent reads it back and reports the miss; the report is what keeps that memory from sinking again.
- **Wrapping up.** Tell the agent you're wrapping up. It's the natural moment for it to write down what the session learned and credit the memories that actually helped, which keeps them on top.
- **Over weeks.** Memories nobody uses sink into the archive; the ones that keep helping stay visible. When upkeep is overdue, the memory says so; `/kg-maintain` runs a bounded cleanup, or background upkeep does it for you.

## How mature it is

**Solid.** kg-memory is built by one maintainer and has been in their daily use on real projects since spring 2026, across about 60 releases. Claude Code is the primary client. Codex CLI runs the same plugin against the same memory and is verified against real Codex sessions. Changes reach `main` through pull requests that run the test suite in CI. The concurrent parts (parallel writes, session forks, renames, saves) were checked with formal models. Each defect the models found was reproduced against the real code; nine of the twelve are fixed, and the other three are documented with their reproductions ([formal/](formal/)). Saves are atomic, with a rolling backup and optional git history. Releases so far have carried existing memory forward, migrating stored data on load when its shape changed.

**Measured, within limits.** Every prompt and file recall decision and every credit is logged locally, and a replay evaluator scores ranking changes against that record. Recent scoring and budget changes were replayed on copies of real memory graphs before release. What does not exist yet is a controlled benchmark of how much the memory improves an agent's results. The evidence so far is daily use and these logs, not a headline number.

**Still moving.** It is pre-1.0: minor releases still retune scoring, budgets and the instructions the agent follows, each recorded in the [changelog](CHANGELOG.md). Antigravity CLI support is experimental. Windows is not supported. The background service is set up on Linux (systemd); on macOS the agent's session start launches the server on demand. Two limits to know: projects are told apart by their folder name, so two projects in folders with the same name share one memory; and project folders must be inside your home directory.

### What it costs

- **Context.** On a mature memory, what loads at session start plus the agent's one full read come to about 60,000 characters by design (roughly 15,000 tokens of English text); more only while the memory's current entries alone overflow that budget, until a maintenance pass tightens them.
- **Quota.** Background upkeep runs short headless agent sessions on your subscription. That's why it's off until you switch it on, and why it runs only while your 5-hour and weekly limits have room to spare. `/kg-scout` also spends tokens, since it reads past sessions.
- **Privacy.** Memories the agent reads, and those background upkeep works on, reach your model provider like any other context. The memory server sends no memory data anywhere on its own. (The visual editor's page loads its graph library from a CDN.)

## Install

```bash
uv tool install kg-memory     # needs uv (https://docs.astral.sh/uv/), which also provides Python 3.10+
kg setup                      # asks before each change, backs up what it edits
```

No uv yet? `curl -LsSf https://raw.githubusercontent.com/mironmax/kg-memory/main/install.sh | sh` installs it and runs both steps. Asking an agent to install it? Point it at [INSTALL.md](INSTALL.md). Linux and macOS; Windows is not supported.

`kg setup` finds the agents you have and offers each piece separately; accepting what it offers is the usual start. Background upkeep is opt-in (its question defaults to No), because it spends quota. Two items change your existing setup, so read them before you accept: setup turns off Claude Code's built-in auto-memory (two memory systems write conflicting entries), and it routes your Claude Code status line through `kg gauge` so the memory can read your quota. Your status line keeps rendering. `kg setup --plan` lists everything without changing anything; [the plugin guide](knowledge-graph/README.md#install) describes each item.

Afterwards:

- **If you use Codex**, run `/hooks` in Codex and trust the knowledge-graph hooks. Codex keeps plugin hooks off until you approve them.
- **If you use Claude Desktop**, fully quit and reopen it.
- Run `kg doctor` to check that everything is connected, then start a new session.

`kg update` keeps it current; `kg uninstall` reverses setup (marketplace auto-update stays on) and keeps your memory.

## Your first five minutes

1. **Start a session.** The agent reads its memory and says *"I have recalled KG Memories"*. The memory is empty at first; that's normal.
2. **Just work.** The agent writes down decisions, preferences, debugging discoveries and how your codebase fits together as they come up.
3. **Seed it faster (optional).** `/kg-extract` maps your codebase into memory; `/kg-scout` mines your past Claude Code, Codex and Antigravity sessions for lessons worth keeping.
4. **After a session or two, ask** *"What do you remember about this project?"*

**Also worth adopting:** the **[recommended setup](recommended-setup/)**, an output style carrying a working agreement plus a status line. Its response rules were benchmarked (results in the setup guide); the current combined version hasn't been re-measured. Versions exist for [Codex](recommended-setup/codex.md) and [Antigravity](recommended-setup/antigravity.md).

## Documentation

- **[Plugin guide](knowledge-graph/README.md)**: what the memory does on its own, everyday use, configuration, data and backups.
- **[Architecture](knowledge-graph/ARCHITECTURE.md)**: the design and how each mechanism works.
- **[Antigravity CLI](knowledge-graph/ANTIGRAVITY.md)** · **[Visual editor](knowledge-graph/VISUAL_EDITOR_GUIDE.md)** · **[Security](SECURITY.md)** · **[Changelog](CHANGELOG.md)** · **[Wiki](https://github.com/mironmax/kg-memory/wiki)**

These docs track the current source on `main`; the changelog marks release boundaries. If kg-memory helps you, a ⭐ helps others find it.

---

## Contributing

Issues and pull requests are welcome. Every pull request runs the test suite in CI on Linux (Python 3.10 and the newest release) and macOS; the macOS job reports without blocking while one intermittent failure is investigated. Release rules are in [RELEASING.md](RELEASING.md).

This repository is also the `maxim-plugins` marketplace that Claude Code and Codex install the plugin from; kg-memory (the `knowledge-graph` plugin) is its first entry. To add a plugin, update `.claude-plugin/marketplace.json`.

## License

MIT; see [knowledge-graph/LICENSE](knowledge-graph/LICENSE).
