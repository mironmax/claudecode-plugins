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
# KG ambient memory reminder — random selection from a pool of targeted prompts.
# Each targets a distinct behavior; random hops prevent habituation to sequence.

msgs=(
  "KG memory active. Not loaded yet this session? Call kg_read(cwd) before any task work."
  "KG capture pulse: did the last exchange reveal anything worth keeping? Write it before moving on."
  "About to search files or web? Check KG first — kg_search may already have the answer."
  "Opening files? Check for component nodes in KG before reading — skip what's already mapped."
  "Did user express a preference, style, or constraint? Capture it as a user-level node now."
  "Any node gist gone stale or vague after using it? Sharpen it while context is still live."
  "Active memory session: write discoveries mid-conversation, not at task end. Cache is warm — cost is minimal."
  "About to make an assumption? kg_search first. If missing, state it and capture it."
  "User just agreed on an approach? Capture the methodology as a node — decisions alone aren't enough."
  "Explained something non-obvious? That explanation is a node. Write it before context scrolls away."
  "Any edges missing between nodes you've used today? One edge makes both nodes far more durable."
  "Context window getting deep? Scan for anything unrecorded — this is the highest-value capture moment."
  "User corrected your approach? Capture the signal you missed, not just the fix."
  "Just resolved something that took 10+ minutes? Root cause node before moving on."
  "Any architectural decision made this session? Node with rationale in notes — not just the conclusion."
  "Check archived node IDs — any feel related to current work? kg_read(cwd, id) to promote and use."
  "Did you discover how two parts of the codebase connect? That's an edge. Write it now."
  "KG is your twin across sessions — what would future-you wish was recorded from this conversation?"
)

idx=$(( RANDOM % ${#msgs[@]} ))
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}' "${msgs[$idx]}"
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
