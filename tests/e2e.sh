#!/bin/sh
# claude-local end-to-end test.
#
# Verifies the full chain: local model -> shim -> Claude Code harness -> MCP tool call.
#
# Requires: python3, curl, the `claude` CLI, and Ollama running with a local model.
#
# Usage:
#   CLAUDE_LOCAL_MODEL=qwen2.5-coder:14b bash tests/e2e.sh
#   bash tests/e2e.sh            # model from CLAUDE_LOCAL_MODEL or `ollama list`
#
# Exits 0 on PASS, non-zero on FAIL.
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PORT="${CLAUDE_LOCAL_SHIM_PORT:-11500}"
OLLAMA_URL="${CLAUDE_LOCAL_OLLAMA_URL:-http://localhost:11434}"
BASE="http://127.0.0.1:$PORT"
MARKER="ping-claude-local"
MCP_NAME="claudelocal-echo"

fail() { echo "E2E FAIL: $1" >&2; exit 1; }

command -v claude >/dev/null 2>&1 || fail "claude CLI not found"
command -v curl >/dev/null 2>&1 || fail "curl not found"

# --- preconditions ---------------------------------------------------------
curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1 \
  || fail "Ollama not reachable at $OLLAMA_URL (is it running?)"

MODEL="${CLAUDE_LOCAL_MODEL:-$(ollama list 2>/dev/null | awk 'NR>1 && $4 ~ /GB|MB|B$/ {print $1; exit}')}"
[ -n "$MODEL" ] || fail "no model found; set CLAUDE_LOCAL_MODEL or pull one"
echo "e2e: model=$MODEL shim=$BASE ollama=$OLLAMA_URL"

# --- start shim if needed --------------------------------------------------
if ! curl -sf -m 1 "$BASE/v1/models" >/dev/null 2>&1; then
  echo "e2e: starting shim"
  mkdir -p "${XDG_RUNTIME_DIR:-/tmp}/claude-local"
  LOG="${XDG_RUNTIME_DIR:-/tmp}/claude-local/e2e-shim.log"
  CLAUDE_LOCAL_SHIM_PORT="$PORT" CLAUDE_LOCAL_OLLAMA_URL="$OLLAMA_URL" \
    nohup python3 "$ROOT/shim.py" >>"$LOG" 2>&1 &
  # Ollama may be under load; be generous: ~30s budget, 2s per attempt.
  ok=0
  i=0
  while [ $i -lt 60 ]; do
    if curl -sf -m 2 "$BASE/v1/models" >/dev/null 2>&1; then ok=1; break; fi
    i=$((i + 1)); sleep 0.5
  done
  [ "$ok" = 1 ] || fail "shim failed to start; see $LOG"
fi

# --- register the echo MCP server (user scope), always clean up ------------
claude mcp add "$MCP_NAME" -- python3 "$ROOT/mcp-echo.py" >/dev/null 2>&1 || true
cleanup() { claude mcp remove "$MCP_NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

# --- the real test: local model must call the MCP tool --------------------
echo "e2e: running claude-local (this takes a minute or two on a local model)"
OUT=$(CLAUDE_LOCAL_MODEL="$MODEL" CLAUDE_LOCAL_SHIM_PORT="$PORT" \
      CLAUDE_LOCAL_OLLAMA_URL="$OLLAMA_URL" \
      "$ROOT/claude-local" -p \
      "Use the echo tool (from the claude-local-echo MCP server) with text '$MARKER'. Reply with ONLY the echoed output." \
      --max-turns 4 2>&1)
echo "----- model output -----"
echo "$OUT"
echo "------------------------"

case "$OUT" in
  *"echoed by claude-local test server"*)
    echo "E2E PASS"
    ;;
  *)
    fail "expected the echo tool's marker in the model output"
    ;;
esac
