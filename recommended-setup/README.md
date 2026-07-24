# Recommended user-level setup

Two small config files that tune how Claude Code works and communicates — measured, not guessed. They pair well with the Knowledge Graph plugin (a calm, verify-first working style is exactly the tone you want distilled into long-term memory), but they are useful on their own.

Both files are user-level: they apply to every project on your machine and live under `~/.claude/`.

## What's here

- **[`CLAUDE.md`](CLAUDE.md)** — a working-agreement memory file. Its deeper job is setting the collaboration's emotional vector: calm, unhurried, truth-over-agreement. In benchmarks this framing roughly doubled unprompted exploration and discovery — with the concise output style active, a planted bug in an open-ended "explain this code" task was found in 5/5 runs with these paragraphs vs 1/5 without, at zero added output-token cost.
- **[`output-styles/concise-quality-v2.md`](output-styles/concise-quality-v2.md)** — an output style tuned via blind A/B benchmark (45 runs, 3 arms, blind judges, real replace-mode mechanism): quality 9.13 vs 8.80 for the stock style, −27% output tokens, zero fluff or fabrication flags across all runs. Note that a selected output style *replaces* Claude Code's built-in tone/style rules rather than layering on top — the style text carries all the weight, which is why every line here earned its place.

**Use them together.** The one measured cost of the concise style was dampened *unprompted* digging on open-ended review asks — and the CLAUDE.md's calm/truth framing is what restored it. Adopting the style alone gives you the token savings but not the discovery recovery.

## Install

```bash
# 1. The output style
mkdir -p ~/.claude/output-styles
cp output-styles/concise-quality-v2.md ~/.claude/output-styles/

# 2. The working-agreement memory
#    If you don't have a ~/.claude/CLAUDE.md yet:
cp CLAUDE.md ~/.claude/CLAUDE.md
#    If you do: merge the sections in by hand — don't overwrite your own instructions.
```

Then in any Claude Code session run `/output-style concise-quality-v2` to make it your default. New sessions pick both files up automatically.

**Verify:** start a fresh session and ask something trivial — the answer should lead with the outcome, no preamble, no closing pleasantries.

**Watch out for:** per-project `.claude/settings.local.json` files with their own `outputStyle` — they silently shadow the global default. If one project still sounds different, check there.

**Undo:** `/output-style default` restores the stock style; remove or edit `~/.claude/CLAUDE.md` sections as you like.

## Adapting

Treat both files as starting points, but resist padding them: in benchmarks, adding extra instruction lines to CLAUDE.md *diluted* the effect rather than strengthening it. Short and settled beats long and thorough here.
