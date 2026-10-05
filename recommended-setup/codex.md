# Recommended Codex CLI setup

Make the current model, usage limits and context visible, then load the same working style the Claude Code setup uses. These settings apply across projects and complement the Knowledge Graph plugin; they also work without it.

Status line checked against Codex CLI **0.157.1**; standing instructions tested on **0.158.0**. The benchmark numbers in the [Claude Code setup](README.md) were measured on Claude Code; on Codex only the small test [below](#what-was-tested-codex-cli-01580) was run.

## Status line

Run `/statusline` to select and reorder footer items. For the same preset directly in `~/.codex/config.toml`, merge this into its existing `[tui]` table:

```toml
[tui]
status_line = [
  "model-with-reasoning",
  "five-hour-limit",
  "weekly-limit",
  "context-used",
  "project-name",
  "git-branch",
]
```

Keep other settings in that table; do not add a second `[tui]` header. If you use a custom `CODEX_HOME`, its `config.toml` is the target instead.

The footer looks like this when all values are available:

```text
GPT-6-Astra high · 5h 81% left · weekly 74% left · Context 22% used · my-project · main
```

Read the labels carefully: **limit percentages are remaining**, while `context-used` is the portion of this conversation's context already occupied. The Claude script displays quota **used**, so its percentages run in the opposite direction. Model and branch vary with the session; unavailable items can be omitted.

**Verify:** `/statusline` applies changes immediately and saves them. After editing the file externally, launch a fresh CLI session and check the footer. Account quota requires an authenticated account with available limit data.

**Reset times:** type `/status` and press Enter to inspect the limits and their reset times. This is a manual check. The low-quota warning can also show a reset time, but the configurable footer in 0.157.1 has no reset-time or countdown item. Codex already has the timestamps; they are not exposed as selectable footer fields. Requests [#24080](https://github.com/openai/codex/issues/24080) and [#21003](https://github.com/openai/codex/issues/21003) track that gap. Do not add proposed names such as `five-hour-limit-reset` to the config: this version does not support them.

The native footer accepts built-in items, not arbitrary shell output. The Claude `statusline.sh` cannot be installed here, and enabling this preset does not create `~/.claude/last-limits.json` or give the agent a new quota-reading tool. A separate usage display or a custom Codex build is needed for a persistent reset countdown outside the built-in warning.

**Undo:** use `/statusline` to restore your previous selection, or restore the previous `status_line` value from your config backup.

Source: [official footer configuration documentation](https://learn.chatgpt.com/docs/developer-commands#configure-footer-items-with-statusline).

## Standing instructions

Load the same text Claude Code uses, [`concise-quality-v3.md`](output-styles/concise-quality-v3.md), as `developer_instructions`: a top-level key in `~/.codex/config.toml`. Codex adds it as a developer-role message ahead of any `AGENTS.md` guidance and keeps its built-in instructions, so the text layers on top rather than replacing anything. Keep `AGENTS.md` for project conventions. The Memory section applies only with the Knowledge Graph plugin installed; without it, Codex skips that section.

Run from this directory of a checkout. It backs up the config, then writes the style body (without its YAML header) at the top, where top-level keys must go:

```bash
C="${CODEX_HOME:-$HOME/.codex}/config.toml"
mkdir -p "$(dirname "$C")"
if grep -q '^[[:space:]]*developer_instructions[[:space:]]*=' "$C" 2>/dev/null; then
  echo "developer_instructions is already set: merge by hand"
else
  touch "$C" && command cp -f "$C" "$C.bak" &&
  { printf "developer_instructions = '''\n"
    awk 'f>=2; /^---$/{f++}' output-styles/concise-quality-v3.md
    printf "'''\n\n"
    cat "$C.bak"; } > "$C"
fi
```

**Verify:** start a fresh session, send any prompt, then check that its rollout carries the text:

```bash
rg -l 'this is a cooperative exploration' "${CODEX_HOME:-$HOME/.codex}/sessions" -g 'rollout-*.jsonl'
```

A matching file from the fresh session confirms that the text was recorded in its rollout. Older matching sessions do not verify the new configuration.

**Undo:** restore `config.toml.bak`, or delete the `developer_instructions = '''…'''` block, then start a fresh session.

### What was tested (Codex CLI 0.158.0)

- **Placement**, read from session rollouts: the text lands in a developer-role message before project `AGENTS.md` text, stays in history across tool calls and later turns, and survives a resume. The built-in instructions are identical with and without it.
- **Behaviour**, 3 small tasks × 3 arms × 2 runs (no addition, `developer_instructions`, global `AGENTS.md`), partly blinded scoring: every answer led with the outcome and none carried fluff. A planted config-key bug in an "explain this code" task was found unprompted in 0/2 runs without the text, 1/2 as developer instructions and 2/2 as global `AGENTS.md`. Mean output tokens were 396, 383 and 457. Two runs per arm cannot rank the layers; the recommendation rests on placement: a standing working agreement belongs with developer authority, and project guidance stays in `AGENTS.md`.

Global `~/.codex/AGENTS.md` also works: the text then arrives in the user role, merged with project guidance. Choose one home, not both. `model_instructions_file` is not an alternative: it replaces Codex's built-in instructions. `personality` picks a built-in preset and cannot load a style document. See the [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) and [AGENTS.md discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

### Where the text and Codex pull apart

The text was written for Claude Code and is loaded unchanged. Three lines read differently here:

- *"The work itself stays calm and sequential"* means dependent steps one at a time; Codex still runs independent reads in parallel, as its own instructions ask.
- *"No preamble … no summary of what you did"* governs the final answer; Codex's progress updates still appear.
- *Context management* asks for a handover before automatic summarization, where Codex is built to carry on through compaction. That is intended: notes get written before context is lost.
