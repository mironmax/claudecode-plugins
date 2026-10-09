#!/bin/sh
# Install kg-memory: uv when missing, then the kg command, then kg setup.
#   curl -LsSf https://raw.githubusercontent.com/mironmax/kg-memory/main/install.sh | sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (https://docs.astral.sh/uv/)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
fi

uv tool install --upgrade kg-memory   # a re-run upgrades
KG="$(command -v kg || echo "$HOME/.local/bin/kg")"

# Piped into sh, stdin is this script: setup asks its questions on the terminal.
# /dev/tty can exist without a controlling terminal (an agent's shell): try it.
if (exec < /dev/tty) 2>/dev/null; then
    exec "$KG" setup < /dev/tty
fi
echo "kg is installed. Run \`kg setup\` in a terminal to connect your harnesses."
