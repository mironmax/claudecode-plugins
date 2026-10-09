# Recommended Antigravity CLI setup

Use a quota display and the same v3 working style as the Claude Code and Codex setups. Both settings work across projects, independently of the KG adapter.

The installation paths and rule loading below were checked on **agy 1.2.16 on Linux**. The KG plugin's Antigravity adapter is separate and experimental; see the [Antigravity guide](../knowledge-graph/ANTIGRAVITY.md).

## Status line

Antigravity can run an external command with session JSON on stdin and display its stdout. See the [official status-line documentation](https://antigravity.google/docs/cli/statusline/).

Run from this repository root. The script requires **Bash and Python 3**, with no additional Python packages:

```bash
mkdir -p ~/.gemini/antigravity-cli
command cp recommended-setup/statusline-agy.sh ~/.gemini/antigravity-cli/statusline.sh
chmod +x ~/.gemini/antigravity-cli/statusline.sh
```

Merge into `~/.gemini/antigravity-cli/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.gemini/antigravity-cli/statusline.sh"
  }
}
```

The display has **two data rows and a divider**:

```text
user@host  📁 my-project  🕐 23:25  🔗 kg installed  [agy v1.2.16]
────────────────────────────────────────────────────────────────────
⚡ Gemini │ 📊 Gemini:88%→Sat 23:17  3P:100%→Sat 23:37 │ 📐 ctx:8% (15k toks)
```

The first row shows identity, workspace location, local time, MCP information when supplied, and CLI version. An installed plugin is labelled **installed**; a connection is reported only when the payload supplies a connected status. Missing information is shown as unknown.

The second row shows the current model, remaining quota for the `gemini-weekly` and `3p-weekly` buckets, both reset times, and context used. Quota is green at 50% or more remaining, amber at 20–50%, and red below 20%. The payload determines which buckets are available; these labels do not establish an account's billing or credit policy.

Each render atomically writes `~/.gemini/antigravity-cli/last-limits.json`:

```json
{
  "gemini_pct_remaining": 88,
  "gemini_resets_at": "2026-10-10T20:17:23Z",
  "gemini_seen_at": 1791060000,
  "third_party_pct_remaining": 100,
  "third_party_resets_at": "2026-10-10T20:37:18Z",
  "third_party_seen_at": 1791060000,
  "context_pct": 8,
  "model": "Gemini",
  "updated_at": 1791060000
}
```

Each bucket keeps its own observation timestamp. A frame with no quota preserves earlier observations and displays them as **cached**. Once a cached reset time has passed, the display shows **reset passed** until a fresh observation arrives. `updated_at` records the render time; use the bucket's `*_seen_at` to judge freshness. Missing context is null, not zero. Context and model belong to whichever session rendered last.

**Verify:** launch an interactive `agy` session and check the display and saved JSON. `jq . ~/.gemini/antigravity-cli/last-limits.json` is an optional reader. Unattended sessions may leave the file stale; this script supplies observations only. The Knowledge Graph plugin's budget notices and Antigravity maintenance runner do not read this file; they query `agy -p /usage` live.

**Undo:** remove `statusLine` from settings and remove the installed script if desired.

## Standing instructions

Load the body of [concise-quality-v3.md](output-styles/concise-quality-v3.md) into the global rule file `~/.gemini/config/GEMINI.md`, without YAML frontmatter. The [official rules documentation](https://antigravity.google/docs/rules/) describes the global rule as always active.

Run from this repository root:

```bash
mkdir -p ~/.gemini/config
G="$HOME/.gemini/config/GEMINI.md"
if [ -f "$G" ]; then
  command cp -f "$G" "$G.bak"
  echo "Existing GEMINI.md preserved: merge the style body into it by hand."
else
  awk 'f>=2; /^---$/{f++}' recommended-setup/output-styles/concise-quality-v3.md > "$G"
fi
```

If the file already exists, keep your other rules and add the style body once. A backup is created before manual merging. Start a fresh session after changing the rule.

On 1.2.16, transcript checks confirmed that this file arrived as a `user_global` rule. This verifies loading; Antigravity behaviour and quality have not been benchmarked against the stock setup. The Claude Code benchmark numbers belong to earlier components of the style, as explained in the [setup overview](README.md#what-was-measured).

v3 asks the agent to write notes and a handover before compaction. With the KG plugin installed, its own rule supplies the mechanical memory protocol and a checkpoint re-queues the memory preload; the v3 Memory section applies only then.

**Undo:** restore `GEMINI.md.bak` after a manual merge, or remove the style text you added. If this installation created a new file containing only the style, remove that file.
