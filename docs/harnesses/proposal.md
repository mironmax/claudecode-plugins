# Proposal: where core ends and adapters begin

> **Status: proposal.** Everything in this document is a recommendation for
> the maintainer to accept, change or reject. The facts it rests on are in
> the [coupling map](coupling-map.md), the [harness cards](cards/) and the
> [instrument matrix](instrument-matrix.md), each with its source and
> verification level. Where this proposal recommends something, it says so.

## 1. The deciding question

**Does Codex CLI have a hook that runs on every user prompt and can add
context to the model's input, as `UserPromptSubmit` does in Claude Code?**

**Yes.** Codex CLI 0.157.1 has a `UserPromptSubmit` hook. Its
`hookSpecificOutput.additionalContext` reaches the model as a `developer`
message placed directly after the user's prompt.

Evidence:

1. **Observed by running it.** Codex was pointed at a local mock of the
   Responses API, and the request it sent to the model was captured. With a
   `UserPromptSubmit` hook returning
   `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "MARKER-UserPromptSubmit-7f3a: remembered fact"}}`,
   the request's `input` contained, in order, the user message `what is 2+2`
   and then a `developer` message with exactly that marker text. The same
   held for `SessionStart` and `PostToolUse`.
2. **Observed with this plugin, unchanged.** This repository's own
   `knowledge-graph/hooks/hooks.json` and `kg-remind.sh` were installed into
   Codex from the repository's own `.claude-plugin/marketplace.json`, with no
   edits, and ran against this repository's server (v0.9.42). The server
   answered `/api/prompt_context`, and its reply arrived in the model's input
   as a developer message. The same session also delivered the
   `SessionStart` preload (`KG MEMORY PRELOADED …`) and ran file recall for
   `cat notes.txt` through `PostToolUse`.
3. **The shipped binary.** Codex embeds a JSON schema titled
   `user-prompt-submit.command.output` whose `hookSpecificOutput` accepts
   `hookEventName: "UserPromptSubmit"` and `additionalContext: string`. The
   input schema carries `prompt`, `session_id`, `cwd` and `transcript_path`.
4. **Documentation.** The Codex hooks page
   (<https://developers.openai.com/codex/hooks>, which redirects to
   <https://learn.chatgpt.com/docs/hooks>, read 2026-09-26) states that for
   `SessionStart` and `UserPromptSubmit`, plain stdout becomes developer
   context and JSON supports `additionalContext`.

Two conditions apply. Both are observed, and both shape the rest of this
proposal:

- Codex skips plugin hooks until the user trusts them once in `/hooks`.
  Before that, nothing fires.
- Codex truncates hook output past about 2,500 tokens (a
  12,000-character payload was cut to about 10,100 characters, keeping the
  head and the tail). The plugin's preload budget of 10,000 characters was
  measured against Claude Code, so it sits close to that line.

What the method cannot show: how OpenAI's models weigh a developer message
injected there. The mock shows placement, not effect.

**So the plan does not have to change the way it would have if the answer
were no.** Ambient recall reaches the model in Codex through the same
instrument, and the same script, as in Claude Code.

## 2. What else the review found

The brief treated a second harness as a port. The review found something
closer to a dialect.

- **Codex CLI speaks Claude Code's plugin dialect.** It reads
  `.claude-plugin/marketplace.json` and `plugin.json`, the same `hooks.json`
  format, the same hook payload fields (`session_id`, `cwd`,
  `transcript_path`, `source`, `prompt`, `tool_name`, `tool_input`), the same
  output envelope, `${CLAUDE_PLUGIN_ROOT}`, `.mcp.json` and `SKILL.md`. It
  even reports shell calls as `Bash`. Of the 1,081 lines in the coupling
  map's layer 2, 456 ran under Codex unchanged.
- **Cursor imports Claude Code hooks and translates their output**, and it
  has `sessionStart` and `postToolUse` context channels. Its documented
  prompt hook cannot add context, and it exposes no usage signal.
- **Antigravity has a different hook model.** Context enters only through
  `PreInvocation` step injection, and the hook must read the transcript to
  learn what happened. Its importer converts a Claude Code plugin but drops
  an HTTP MCP server's URL.

## 3. The proposed line between core and adapter

### The principle

This proposal recommends one rule. **The server makes every decision. An
adapter does only three things:**

1. carry an event from the harness to the server;
2. put the server's answer into the model's input, in the form that harness
   accepts;
3. launch and budget a maintenance run.

Today's code is already close to this. The hooks post raw payloads and print
whatever comes back (`kg-remind.sh`, `kg-tool-event.sh`). That is why they
ran in Codex unchanged, and the proposal keeps them that way. What breaks
the rule is that the server reads Claude Code's payload and writes Claude
Code's envelope inline, in the shared modules.

### What moves out of Claude Code-specific code

This proposal recommends a small server-side **harness profile**, chosen per
event, that owns everything harness-shaped the server touches. The rest of
the server works on a normalised event.

| Today | Location | Proposed home |
|---|---|---|
| Tool name to touched paths (`Read`, `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, `Bash`) | `file_recall.py:107-108`, `263-276` | Profile. The Claude Code and Codex profiles share the `Bash` parser. Codex adds `apply_patch`, whose touched paths are the `*** Add File:`, `*** Update File:`, `*** Delete File:` and `*** Move to:` lines in `tool_input.command` (observed payload). Cursor maps `Shell`, `Read` and `Write`. Antigravity maps `view_file`, `write_to_file`, `replace_file_content` and `run_command`. |
| Web targets for capture nudges (`Read`, `WebFetch`, `WebSearch`) | `ambient.py:492-503` | Profile |
| Output envelope (`hookSpecificOutput`) | `rest.py:270-275`, `290-295` | Profile. Claude Code and Codex share one. Cursor takes snake_case `additional_context` natively, though its CLI also translates the Claude form. Antigravity needs `injectSteps`. |
| Preload budget, measured on Claude Code | `constants.py:33-40` | Profile. Claude Code: 10,000 characters, as measured. Codex: either a budget under its about 2,500-token default, or `additionalContextLimit` set on the hook. See open question 3. |
| `claude_sid` names and `claude_session` log fields | `session_manager.py`, `ambient.py`, `file_recall.py`, `rest.py`, `store.py:520` | A harness session id, tagged with the harness name so ids from two harnesses can never collide. This is a rename plus one field. |
| Transcript-marker recovery | `session_manager.py:14-51` | Stays in core. It is already harness-neutral in practice: it found the KG session in a Codex rollout. |

The core keeps layer 0 and layer 1 unchanged: the graph, scoring, recall
decisions, chore selection, the MCP tools and REST.

### What stays in adapters

These are files per harness, and they stay thin:

- Hook configuration. Claude Code and Codex can share one `hooks.json`, as
  observed. Cursor can load the same file through its Claude Code import, or
  ship its own under `.cursor-plugin/`. Antigravity needs its own file and a
  `PreInvocation` script that reads the transcript.
- Packaging. Claude Code and Codex already share `.claude-plugin/`. Cursor
  reads it too, according to its code. Antigravity needs a root `plugin.json`
  and an `mcp_config.json` that uses `serverUrl`.
- Operations text: install steps, `/mcp` Reconnect in `kg-autostart.sh:112`
  and `:119`, and the `kg-ops` runbook.
- The maintenance runner and budget provider (section 4).
- Visual editor project discovery. This proposal notes a neutral
  alternative: the storage root already holds a graph for every project the
  server has seen, so discovery could read the store instead of any
  harness's history.

### What full functionality costs in each harness

| Harness | Reachable today | Adapter work proposed | Remaining gap |
|---|---|---|---|
| Codex CLI | Preload, per-prompt recall, shell file recall, memory tools, skills, packaging, all observed | `apply_patch` path parsing; a preload budget that fits; a hook-trust step in install docs; runner and budget provider; project discovery; a `kg-scout` variant for rollout files | Web capture nudges: Codex's hosted web search never reaches a hook |
| Cursor | Preload (`sessionStart`), tool recall (`postToolUse`), memory tools, skills, packaging; none observed live | Profile; test whether `beforeSubmitPrompt` `additional_context` reaches the model | Per-prompt recall, unless that test passes; the budget signal |
| Antigravity | Memory tools, skills and rules, as documented | A `PreInvocation` adapter that reads the transcript for both prompt recall and tool recall; its own `hooks.json` and plugin manifest | No documented session start event; no context channel after a tool; recall arrives one model call later than in Claude Code |

## 4. Maintenance dispatch and the budget gate

This is the hardest part, because today it assumes the `claude` binary and a
Claude quota file. The pipeline today is:

1. **Trigger:** a prompt arrives (`rest.py:245-257`).
2. **Selection:** layer 0, harness-neutral.
3. **Gates:** read `~/.claude/last-limits.json` (`chore_dispatch.py:244-299`).
4. **Runner:** `claude -p --model … --setting-sources user --settings <allowlist>` (`chore_dispatch.py:134-176`, `371-434`).

The trigger ports as-is to every harness examined. It needs only a
notification, not a context channel, so Cursor's `beforeSubmitPrompt` and
Antigravity's `PreInvocation` both suffice. Selection needs no change. The
gates and the runner are the problem.

### Option A: a budget provider and a runner per harness

The gate would read a normalised reading from a per-harness provider: a list
of windows, each with a used fraction, window length, reset time and
observation time. Today's thresholds become fractions, and `weekly_pace`
(`chore_dispatch.py:220-241`) applies to the longest window.

| Harness | Budget provider | Runner |
|---|---|---|
| Claude Code | Today's status line file | Today's `claude -p` command |
| Codex CLI | The newest rollout file's last `token_count.rate_limits`: primary and secondary `used_percent`, `window_minutes`, `resets_at`. Freshness comes from the event timestamp. Codex writes this itself, so no status line script is needed. | `codex exec --ephemeral -s read-only --disable shell_tool -c web_search="disabled" -c mcp_servers.<kg>.enabled_tools=[…] -c mcp_servers.<kg>.default_tools_approval_mode="approve"`, run from the storage root |
| Antigravity | A status line script, like Claude Code's, that persists `quota.<bucket>.remaining_fraction` | `agy -p` with `mcp(kg/*)` in `permissions.allow`; unlisted tools are soft-denied |
| Cursor | None exists, so chores stay off | `agent -p` with a `cli-config.json` allowlist, untested |

Strengths:

- It spends quota the user already pays for. That is the design's stated
  premise: unspent weekly allowance is lost at the reset
  (`chore_dispatch.py:220-241`).
- Each harness restricts tools natively. Observed for Codex: no shell, no web
  search, only the listed KG tools.
- The maintenance model matches the family the user works with.

Weaknesses:

- There are as many runners and permission dialects as harnesses.
- Gate fidelity rests on vendor signals of uneven standing. Codex's quota
  headers are undocumented and were observed only against a mock. Cursor has
  no signal at all.
- The MCP server's name differs by install: `mcp__plugin_knowledge-graph_kg__…`
  in Claude Code, `mcp__kg` in Codex. Every allowlist must follow the name.

### Option B: maintenance driven by the server itself

The server would call a model API directly and expose the `kg_*` operations
as in-process tools. No harness binary is involved. The budget becomes a
token or cost ceiling that the server keeps and the user sets.

Strengths:

- There is one implementation, identical in every harness, including Cursor.
- The tool surface is exactly the KG operations. There is no shell to deny
  and no permission dialect to translate.
- It needs no quota signal, and it can be tested offline against a mock, as
  this review did with Codex.

Weaknesses:

- It needs an API key and separate billing. It does not spend the
  subscription quota the current design exists to harvest.
- It adds a model client, a tool loop and their dependencies to the server.
- Prompts tuned for `claude -p` may need retuning.
- Memory content leaves the machine by a route the user must newly consent
  to.

### This proposal's recommendation

Build the seam for both, and ship Option A first, for Claude Code and Codex.

1. Split `chore_dispatch.py` into selection (unchanged), a `BudgetProvider`,
   and a `Runner`. Move today's Claude Code code behind the two interfaces
   without changing its behaviour.
2. Add the Codex provider and runner in the shapes observed in this review.
3. Default to off for any harness with no provider, which today means
   Cursor.
4. Keep Option B as a later provider and runner pair, named "api", for users
   who opt in with a key, and as the way to give Cursor maintenance.

Option A keeps the economic premise the current design is built on. The seam
costs little, and Option B then becomes an addition rather than a rewrite.

## 5. Open questions only the maintainer can decide

1. **Which harness comes first, and is Codex parity the bar for this
   phase?** The evidence makes Codex nearly free. Cursor and Antigravity each
   carry a real gap.
2. **One plugin directory or several manifests?** Codex installs
   `.claude-plugin/` as-is, and Cursor's code reads it too. Is relying on
   that compatibility acceptable, or should each harness get its own
   manifest (`.codex-plugin/`, `.cursor-plugin/`, a root `plugin.json` for
   Antigravity), at the cost of keeping them in sync?
3. **The preload budget in Codex.** Either lower the budget for the Codex
   profile, or set `additionalContextLimit` on the `SessionStart` hook. Codex
   documents that key; whether Claude Code tolerates an unknown key in a
   shared `hooks.json` is untested.
4. **How should the server learn which harness sent an event?** Payload
   sniffing is fragile, because Codex deliberately mirrors Claude Code's
   fields. The alternatives are a query parameter or header set by
   per-harness hook config, which would end the shared `hooks.json`, or an
   environment variable the hook script reads.
5. **The Codex hook-trust step.** Plugin hooks do nothing until the user
   reviews them in `/hooks`. Is a documented one-time step acceptable, or
   should the install flow detect untrusted hooks and say so, for example
   from the MCP server when no hook has ever called in?
6. **Spending Codex quota on maintenance, and which model.** `CHORE_MODEL` is
   a Claude model today. Is spending the user's ChatGPT-plan quota the same
   decision the user made for Claude?
7. **Option B at all?** Is an API-key maintenance path in scope for this
   plugin, given that it changes who pays and where memory content goes?
8. **Cursor's per-prompt channel.** Is it worth an account to test whether
   `beforeSubmitPrompt`'s `additional_context` reaches the model? If it does
   not, is Cursor acceptable at reduced timing (preload and tool recall
   only)? The roadmap's stated goal rules out a reduced tier.
9. **Antigravity's scope.** A `PreInvocation` adapter that reads the
   transcript is a real subsystem, and the recall it gives lags one model
   call. Is that parity, or a reason to defer Antigravity?
10. **Codex web capture nudges.** Hosted web search is invisible to hooks.
    Accept the loss, or ask whether the model's web results can be seen
    another way?

## 6. What this proposal does not rest on

- Model behaviour under Codex. Every Codex run used a mock model, so the
  proposal relies on placement, not effect.
- Real Codex quota values. The rollout format was observed with simulated
  headers. That OpenAI's backend sends 5-hour and weekly windows is inferred.
- Live Cursor or Antigravity turns. Both need accounts. Their cells rest on
  documentation and static reading, and the cards mark each claim
  accordingly.
