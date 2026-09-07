#!/bin/sh
# Remove claude-local and stop any shim it started.
set -u

DEST="${CLAUDE_LOCAL_DEST:-$HOME/.local/bin}"
rm -f "$DEST/claude-local" "$DEST/shim.py" "$DEST/sglang-serve" "$DEST/sglang-stats" "$DEST/freetoken-serve" "$DEST/vllm-serve"

# Stop a running shim started by this install (safe to miss).
for f in "${XDG_RUNTIME_DIR:-/tmp}"/claude-local/*.pid; do
  [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true
done
rm -rf "${XDG_RUNTIME_DIR:-/tmp}/claude-local"

echo "uninstalled claude-local from $DEST"
