#!/bin/bash
# Install kg-memory + kg-visual shell commands for Claude Code knowledge-graph plugin.
#
# Hooks are now shipped inside the plugin (hooks/hooks.json) and auto-register on
# /plugin install + /reload-plugins — no settings.json edit needed.
#
# This script only:
#   1. Symlinks kg-memory and kg-visual into ~/.local/bin/
#   2. Cleans up the old user-settings hook entry from earlier plugin versions
#      (the hook now lives in the plugin itself; leaving the old entry causes
#      double-firing).
#
# Run once after installing the plugin via /plugin install.

set -e

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
SETTINGS="$HOME/.claude/settings.json"
OLD_HOOK_SCRIPT="$HOME/.claude/hooks/kg-remind.sh"

# ── 1. CLI commands ───────────────────────────────────────────────────────────

mkdir -p "$BIN_DIR"
ln -sf "$PLUGIN_DIR/server/manage_server.sh" "$BIN_DIR/kg-memory"
chmod +x "$PLUGIN_DIR/server/manage_server.sh"
echo "✓ kg-memory command installed"

ln -sf "$PLUGIN_DIR/visual-editor/manage_visual.sh" "$BIN_DIR/kg-visual"
chmod +x "$PLUGIN_DIR/visual-editor/manage_visual.sh"
echo "✓ kg-visual command installed"

# ── 2. Migrate from old user-settings hook (idempotent) ──────────────────────
#
# Earlier versions of this script wrote the kg-remind hook into
# ~/.claude/settings.json and dropped the script at ~/.claude/hooks/kg-remind.sh.
# Now the hook is bundled in the plugin (hooks/hooks.json), so the old entry
# would cause every prompt to fire two reminders. Strip it if present.

if [ -f "$SETTINGS" ]; then
    python3 - "$SETTINGS" "$OLD_HOOK_SCRIPT" << 'PYEOF'
import json, os, sys

settings_path, old_hook_script = sys.argv[1], sys.argv[2]

with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.get("hooks", {})
event_list = hooks.get("UserPromptSubmit")
if not event_list:
    print("✓ No legacy hook entry in settings.json (clean)")
    sys.exit(0)

removed = 0
new_event_list = []
for block in event_list:
    inner = block.get("hooks", [])
    kept = [h for h in inner if h.get("command") != old_hook_script]
    removed += len(inner) - len(kept)
    if kept:
        new_event_list.append({**block, "hooks": kept})

if removed == 0:
    print("✓ No legacy hook entry in settings.json (clean)")
    sys.exit(0)

if new_event_list:
    hooks["UserPromptSubmit"] = new_event_list
else:
    hooks.pop("UserPromptSubmit", None)
if not hooks:
    settings.pop("hooks", None)

tmp = settings_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
os.replace(tmp, settings_path)
print(f"✓ Removed legacy kg-remind hook entry from settings.json ({removed} entr{'y' if removed == 1 else 'ies'})")
print("  The plugin now ships hooks/hooks.json — reminders will fire from there after /reload-plugins.")
PYEOF
fi

# Also remove the old standalone hook script if present — harmless on disk but
# tidier to clean up.
if [ -f "$OLD_HOOK_SCRIPT" ]; then
    rm -f "$OLD_HOOK_SCRIPT"
    echo "✓ Removed legacy hook script: $OLD_HOOK_SCRIPT"
fi

# ── 3. PATH reminder ──────────────────────────────────────────────────────────

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "⚠ Add ~/.local/bin to your PATH:"
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc  # or ~/.bashrc"
fi

echo ""
echo "Done. The bundled hook activates automatically on /plugin install + /reload-plugins"
echo "(or on Claude Code restart) — this script only handles the shell commands and the"
echo "legacy-hook cleanup above."
