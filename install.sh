#!/bin/sh
# Install claude-local: puts the launcher and shim in a bin directory.
#
#   ./install.sh                       # installs to ~/.local/bin
#   CLAUDE_LOCAL_DEST=/usr/local/bin ./install.sh
set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
DEST="${CLAUDE_LOCAL_DEST:-$HOME/.local/bin}"

command -v python3 >/dev/null 2>&1 || { echo "install: python3 is required" >&2; exit 1; }
command -v claude >/dev/null 2>&1 || echo "install: note: 'claude' not found on PATH; install Claude Code first"

mkdir -p "$DEST"
install -m 0755 "$ROOT/claude-local" "$DEST/claude-local"
install -m 0755 "$ROOT/shim.py" "$DEST/shim.py"
# Optional: only useful if you run these servers locally, harmless otherwise.
install -m 0755 "$ROOT/sglang-serve" "$DEST/sglang-serve"
install -m 0755 "$ROOT/sglang-stats" "$DEST/sglang-stats"
install -m 0755 "$ROOT/freetoken-serve" "$DEST/freetoken-serve"
install -m 0755 "$ROOT/vllm-serve" "$DEST/vllm-serve"

case ":$PATH:" in
  *":$DEST:"*) : ;;
  *)
    echo "note: $DEST is not on your PATH. Add it:"
    echo "  fish:     set -gx PATH \$PATH '$DEST'"
    echo "  bash/zsh: export PATH=\"\$PATH:$DEST\""
    ;;
esac

echo "installed: $DEST/claude-local (+ shim.py, sglang-serve, sglang-stats, freetoken-serve)"
echo "try:        claude-local"
