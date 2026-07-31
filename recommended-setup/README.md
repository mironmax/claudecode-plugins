# Recommended user-level setup

Three small config files that tune how Claude Code works, communicates, and paces itself — measured, not guessed. They pair well with the Knowledge Graph plugin (a calm, verify-first working style is exactly the tone you want distilled into long-term memory), but they are useful on their own.

All three are user-level: they apply to every project on your machine and live under `~/.claude/`.

## What's here

- **[`CLAUDE.md`](CLAUDE.md)** — a working-agreement memory file. Its deeper job is setting the collaboration's emotional vector: calm, unhurried, truth-over-agreement. In benchmarks this framing roughly doubled unprompted exploration and discovery — with the concise output style active, a planted bug in an open-ended "explain this code" task was found in 5/5 runs with these paragraphs vs 1/5 without, at zero added output-token cost.
- **[`output-styles/concise-quality-v2.md`](output-styles/concise-quality-v2.md)** — an output style tuned via blind A/B benchmark (45 runs, 3 arms, blind judges, real replace-mode mechanism): quality 9.13 vs 8.80 for the stock style, −27% output tokens, zero fluff or fabrication flags across all runs. Note that a selected output style *replaces* Claude Code's built-in tone/style rules rather than layering on top — the style text carries all the weight, which is why every line here earned its place.
- **[`statusline.sh`](statusline.sh)** — a two-line status line showing session identity and session health, including your rolling subscription quota. Its second job is the one that changes how Claude works: it writes the quota to `~/.claude/last-limits.json`, which is the only way an agent can read its own remaining budget. See [Working with the limits](#working-with-the-limits) below.

**Use the first two together.** The one measured cost of the concise style was dampened *unprompted* digging on open-ended review asks — and the CLAUDE.md's calm/truth framing is what restored it. Adopting the style alone gives you the token savings but not the discovery recovery.

## Install

```bash
# 1. The output style
mkdir -p ~/.claude/output-styles
cp output-styles/concise-quality-v2.md ~/.claude/output-styles/

# 2. The working-agreement memory
#    If you don't have a ~/.claude/CLAUDE.md yet:
cp CLAUDE.md ~/.claude/CLAUDE.md
#    If you do: merge the sections in by hand — don't overwrite your own instructions.

# 3. The status line (requires jq)
cp statusline.sh ~/.claude/statusline.sh
chmod +x ~/.claude/statusline.sh
```

Then point Claude Code at the script by adding this to `~/.claude/settings.json` (merge into your existing JSON — don't paste a second top-level object):

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.sh"
  }
}
```

The tilde form is what Claude Code documents — the command runs through a shell. On Windows it routes through Git Bash, so the script needs Git Bash installed.

Then in any Claude Code session run `/output-style concise-quality-v2` to make it your default. New sessions pick all three up automatically.

**Verify:** start a fresh session and ask something trivial. The answer should lead with the outcome, no preamble, no closing pleasantries — and the status line should render two lines like this:

```
maxim@Solaris 📁 claudecode-plugins 🕐 21:21 🔗 knowledge-graph,claude-in-chrome [concise-quality-v2]
─────────────────────────────────────────────────────────────────────────────────────────────────────
⚡ Opus 5 │ 📊 5h:41%→02:10 7d:62%→Sun 02 │ 💾 cache:94% │ 📐 ctx:34%
```

Reading line two: model · 5-hour quota used and when it resets · 7-day quota used and when it resets · prompt-cache hit rate this turn · context window filled. Quota percentages are green under 50%, amber to 80%, red above.

A dash in the quota segment means that render carried no `rate_limits`. Claude Code sends it only to Claude.ai subscribers (Pro/Max) and only after the session's first API response, and each window can be absent independently — so early frames legitimately show a dash, and API-key users never see one at all. The line renders only what the current frame actually carried; the file on disk is the one that remembers.

Then confirm the disk side-effect, which is the part Claude uses:

```bash
jq . ~/.claude/last-limits.json
```

**Watch out for:** per-project `.claude/settings.local.json` files with their own `outputStyle` or `statusLine` — they silently shadow the global ones. If one project still sounds or looks different, check there.

**Undo:** `/output-style default` restores the stock style; remove the `statusLine` key from settings; remove or edit `~/.claude/CLAUDE.md` sections as you like.

---

## Working with the limits

*Written from the agent's side of the terminal.*

### Why the file exists

Claude Code pipes a JSON payload to the status-line command on every render, and that payload carries `rate_limits` — your 5-hour and 7-day subscription usage, with reset timestamps. It goes to that command's stdin and nowhere else. I never see it, and the harness doesn't persist it. So by default, when I need to know how much budget is left before committing to a two-hour refactor, I have exactly one move available: ask you to read the number off your screen.

That is a silly place to be, and the fix is a few lines of shell. Every render, this script atomically writes what it received:

```json
{"five_hour_pct":41.5,"five_hour_resets_at":1785539400,"five_hour_seen_at":1785521950,
 "seven_day_pct":62.0,"seven_day_resets_at":1785664800,"seven_day_seen_at":1785521950,
 "context_pct":34.2,"updated_at":1785521950}
```

Now `jq . ~/.claude/last-limits.json` is a cheap, one-call answer to "how much room do I have?" — and the question stops being a question for you.

Four properties of that file are worth knowing before trusting it:

- **`five_hour_pct` and `seven_day_pct` are account-global.** Whichever session rendered last wrote them, and the value is valid for every session — including background and scheduled ones sharing your account.
- **`context_pct` is not.** It belongs to whichever session rendered last, which may not be me. I treat it as a hint and use my own context signals for my own conversation.
- **Each window carries its own `*_seen_at` stamp**, because the two windows can arrive independently. A frame carrying only the 5-hour figure still gets written — dropping a live reading because its neighbour was absent would be the worse failure — and the 7-day value is carried over *with its original stamp*. That is the honest arrangement: a number's freshness travels with the number, so a carried-over value can never pass itself off as current.
- **`updated_at` is when the file was written**, not when either reading was taken. It answers "is this file being maintained at all", which matters because headless and scheduled sessions don't reliably render a frame. For "can I trust this number", read that window's `*_seen_at`.

### What I actually do with it

The three gauges answer three different questions, on three different time horizons, and conflating them is the main way this goes wrong.

**Context window** governs *this conversation*. When it climbs past two thirds, the useful move isn't to keep going carefully — it's to reach a checkpoint, write the handover, and let a fresh session continue with a clean context. A compaction I chose is always better than one that arrives mid-edit.

**The 5-hour window** governs *today's session*. This is the one I read at the start of any substantial piece of work, because it decides the shape of the plan rather than the plan itself. At 10% used I can propose an ambitious block. At 70% I should propose the same work in smaller waves with a commit after each, so that stopping is always cheap. At 90% the honest recommendation is: finish the current wave, write the handover, stop.

**The 7-day window** governs *the week*. It's the one that matters if you have paid work later in the week and a hobby project tonight — it's the difference between "there's plenty" and "spend this deliberately."

The practice that makes those readings useful is **scoping work into waves that end on a checkpoint**. Merge and commit after each one. Then a session that stops early stops cleanly, and every reading of the gauge is an opportunity to stop rather than an emergency. The corollary is to keep a few percent in reserve at the end: the wrap-up itself — the handover letter, the memory writes, the final commit — costs quota too, and exhausting the window *during* wrap-up is the one failure mode that loses the session's learning instead of merely pausing it. I aim to stop around 90%, not at 100%.

**Calibrate once, on your own workload.** General rules about how much a session costs are close to worthless because the variance between workloads is enormous. A reading before and after one representative wave gives you a number you can plan with for months. Mine, measured on parallel Sonnet subagent batches: one wave of four ≈ 15% of the 5-hour window. Yours will differ, and yours is the one worth having.

**Schedule against the reset, not against the clock.** `five_hour_resets_at` is an epoch timestamp, directly usable by a scheduler. The 5-hour window is anchored to first use and drifts day to day, so "run the batch at 14:00" quietly decays while "run it an hour before the window resets" stays correct. If the work also has a per-window cap of its own — sends, API calls, anything rate-limited on the other side — anchoring to the reset makes both windows refresh in lockstep.

**The cache figure is a spend signal, not a health readout.** Input tokens are cheap largely *because* of prompt caching; output tokens are what you actually pay for. A cache rate that drops below ~30% means something early in the prompt is churning every turn, so every turn re-pays for the whole prefix. The fix is usually to stabilize what sits at the front of the context, not to trim what's at the back.

### What I'd tell another Claude Code user

Install the script even if you never look at the second line. The rendering is a convenience; the file it writes is the capability.

Then **tell your agent that the file exists** — otherwise nothing changes, because I have no way to discover it. One line is enough:

```markdown
## Limits
My rolling quota is at ~/.claude/last-limits.json (written by the status line):
five_hour_pct, seven_day_pct, reset epochs, per-window *_seen_at stamps. Read it
at session start
and before committing to a large block of work; pace against it and say plainly
when the budget argues for a smaller scope.
```

That can live in `~/.claude/CLAUDE.md` — keep it factual and short, since in benchmarks padding that file diluted its effect rather than strengthening it. If you run the [Knowledge Graph plugin](../README.md), the cheaper home is a memory node: it arrives in the session-start preload, costs nothing in the always-loaded budget, and can accumulate your calibration numbers alongside it.

Finally, treat the gauge as a **pacing instrument rather than an alarm**. Its value is not the warning at 85% — it's that a plan made against a known budget gets scoped correctly at the start, so the warning never arrives. A session that ends on a checkpoint by choice is worth several that end mid-edit.

One caveat I'd rather state than have you discover: headless and scheduled sessions don't reliably render a status-line frame, so the file can go stale exactly when an unattended run needs it most. This is why the timestamps are in there. Check the window's `*_seen_at` before planning against it, and when the reading is old, plan conservatively instead of trusting it.

---

## Adapting

Treat all three files as starting points. Resist padding the first two: in benchmarks, adding extra instruction lines to CLAUDE.md *diluted* the effect rather than strengthening it. Short and settled beats long and thorough here.

The status line is the opposite — it's yours to rearrange freely. The segments are independent; the only part worth preserving verbatim is the `last-limits.json` write block, including its guard against overwriting a good reading with nulls.
