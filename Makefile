PY ?= python3

.PHONY: unit e2e test clean

# Deterministic tests: no LLM, no network beyond localhost.
unit:
	$(PY) tests/test_rewrite.py
	$(PY) tests/test_mcp_server.py

test: unit

# Full chain: local model + shim + Claude Code + MCP tool call.
# Requires Ollama running with a model.
e2e:
	bash tests/e2e.sh

clean:
	rm -rf $(XDG_RUNTIME_DIR)/claude-local /tmp/claude-local
