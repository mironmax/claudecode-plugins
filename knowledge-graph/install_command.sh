#!/bin/bash
# Install kg-memory command + hook for Claude Code
# Run after installing the plugin via /plugin install

set -e

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
HOOKS_DIR="$HOME/.claude/hooks"
SETTINGS="$HOME/.claude/settings.json"
HOOK_SCRIPT="$HOOKS_DIR/kg-remind.sh"

# ── 1. CLI command ────────────────────────────────────────────────────────────

mkdir -p "$BIN_DIR"
ln -sf "$PLUGIN_DIR/server/manage_server.sh" "$BIN_DIR/kg-memory"
chmod +x "$PLUGIN_DIR/server/manage_server.sh"
echo "✓ kg-memory command installed"

# ── 2. Hook script ────────────────────────────────────────────────────────────

mkdir -p "$HOOKS_DIR"
cat > "$HOOK_SCRIPT" << 'EOF'
#!/usr/bin/env bash
# KG ambient memory reminder — injected before each user prompt via additionalContext.
# Kept minimal to preserve prompt cache efficiency.
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"KG memory is available. Use it well. Awareness and attention to details leads to mastery."}}'
EOF
chmod +x "$HOOK_SCRIPT"
echo "✓ Hook script installed: $HOOK_SCRIPT"

# ── 3. Wire hook into settings.json ──────────────────────────────────────────

if [ ! -f "$SETTINGS" ]; then
    cat > "$SETTINGS" << EOF
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$HOOK_SCRIPT"
          }
        ]
      }
    ]
  }
}
EOF
    echo "✓ Created settings.json with hook"
else
    # settings.json exists — inject hook with Python (handles any existing structure)
    python3 - "$SETTINGS" "$HOOK_SCRIPT" << 'PYEOF'
import json, sys

settings_path = sys.argv[1]
hook_script = sys.argv[2]

hook_entry = {
    "hooks": [
        {
            "type": "command",
            "command": hook_script
        }
    ]
}

with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})
existing = hooks.setdefault("UserPromptSubmit", [])

# Idempotent: skip if this hook command is already registered
for block in existing:
    for h in block.get("hooks", []):
        if h.get("command") == hook_script:
            print("✓ Hook already in settings.json (no change)")
            sys.exit(0)

existing.append(hook_entry)

# Atomic write
import os, tempfile
tmp = settings_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
os.replace(tmp, settings_path)
print("✓ Hook added to settings.json")
PYEOF
fi

# ── 4. PATH reminder ──────────────────────────────────────────────────────────

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "⚠ Add ~/.local/bin to your PATH:"
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc  # or ~/.bashrc"
fi

echo ""
echo "Done. Restart Claude Code for the hook to take effect."
