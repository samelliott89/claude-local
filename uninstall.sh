#!/bin/sh
# Remove claude-local and stop any shim it started.
set -u

DEST="${CLAUDE_LOCAL_DEST:-$HOME/.local/bin}"
rm -f "$DEST/claude-local" "$DEST/shim.py" "$DEST/sglang-serve" "$DEST/sglang-stats"

# Stop a running shim started by this install (safe to miss).
if [ -f "${XDG_RUNTIME_DIR:-/tmp}/claude-local/shim.pid" ]; then
  kill "$(cat "${XDG_RUNTIME_DIR:-/tmp}/claude-local/shim.pid")" 2>/dev/null || true
fi
rm -rf "${XDG_RUNTIME_DIR:-/tmp}/claude-local"

echo "uninstalled claude-local from $DEST"
