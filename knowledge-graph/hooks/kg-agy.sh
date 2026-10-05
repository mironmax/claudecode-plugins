#!/usr/bin/env bash
# Native agy hooks run in the plugin directory. No plugin-root substitution.
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HOOK_DIR/kg-agy.py" "$@"
