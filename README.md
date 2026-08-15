# claude-local

Run [Claude Code](https://claude.com/claude-code) on local models.
Uses [Ollama](https://ollama.com). No API key. No per-token billing.

```
you → claude-local → Claude Code → shim (:11500) → Ollama (:11434) → your GPU
```

## Why this exists

Claude Code speaks the Anthropic Messages API.
Ollama accepts it at `/v1/messages` — with one exception.
Ollama rejects mid-conversation `role:"system"` messages
("system message must be at the beginning",
[ollama/ollama#13949](https://github.com/ollama/ollama/issues/13949)).

Claude Code sends those messages.
So every agentic turn fails with HTTP 500.
`ANTHROPIC_BASE_URL=http://localhost:11434` alone never works.

`claude-local` ships a ~150-line shim that rewrites those messages into
user-role `<system-note>` blocks.
Everything else passes through untouched, including streaming.
When Ollama fixes the upstream issue, point `CLAUDE_LOCAL_OLLAMA_URL`
straight at Ollama and drop the shim.

## Requirements

- Python 3.8+ (standard library only)
- `curl`
- Ollama running, with at least one local model
- Claude Code installed (`claude --version`)

## Install

```sh
git clone https://github.com/samelliott89/claude-local
cd claude-local
./install.sh          # -> ~/.local/bin (set CLAUDE_LOCAL_DEST to change)
```

Or skip the installer entirely — the repo works in place:

```sh
./claude-local
```

## Usage

```sh
claude-local                              # pick the first model in `ollama list`
CLAUDE_LOCAL_MODEL=deepseek-r1 claude-local
claude-local --resume                     # all extra args pass to claude
claude-local -p "say hi" --max-turns 1    # headless
```

The launcher starts the shim if it is not running, then execs `claude`
with `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, and
`ANTHROPIC_DEFAULT_HAIKU_MODEL` set.
It deliberately sets **no** `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`.
Your Claude login (if any) stays the auth source.
That keeps claude.ai connectors available (see below).

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `CLAUDE_LOCAL_MODEL` | first model in `ollama list` | model name sent in requests |
| `CLAUDE_LOCAL_SHIM_PORT` | `11500` | shim listen port |
| `CLAUDE_LOCAL_OLLAMA_URL` | `http://localhost:11434` | Ollama upstream |
| `CLAUDE_LOCAL_SHIM` | `<script dir>/shim.py` | explicit shim path |
| `CLAUDE_LOCAL_SHIM_BIN` | `python3` | shim interpreter |
| `CLAUDE_LOCAL_NO_AUTOSTART` | `0` | `1` = never auto-start the shim |

Shim log: `${XDG_RUNTIME_DIR:-/tmp}/claude-local/shim.log`

## MCP and connectors

Verified behaviors (2026-08):

- **Interactive sessions**: claude.ai connectors attach and local models
  can call their tools. Verified end-to-end: a local Qwen called Figma's
  `whoami` and got real account data back.
- **Headless (`claude -p`)**: no MCP tools attach at all — with local or
  hosted models. This is Claude Code's behavior, not this project's.
- **Your own MCP servers** (`claude mcp add ...`): work, and are covered
  by the e2e test.
- Keep `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY` **unset** for sessions
  that need connectors. Claude Code disables connectors when an API key
  is present.

## Use with Cursor (bonus)

Cursor's "Override OpenAI Base URL" also works against Ollama, with two
extra requirements:

1. **Reachability**: Cursor calls your endpoint from its servers.
   Expose Ollama over HTTPS, e.g.
   `cloudflared tunnel --url http://localhost:11434 --http-host-header localhost:11434`
   (the host-header flag matters — Ollama 403s foreign `Host` headers).
2. **Model name**: recent Cursor builds reject unrecognized model names
   ("Model name is not valid"). Alias the model under a known name:
   `ollama cp yourmodel:tag gpt-5-mini`.

Then in Cursor: Settings → Models → enable the matching model,
toggle the OpenAI key **on** (any `sk-…`-shaped placeholder),
set the base URL to `https://<your-tunnel>/v1`.

## Tests

```sh
make unit        # rewriter + MCP server protocol. No LLM, no network.
make e2e         # full chain: model → shim → Claude Code → MCP tool call.
                 # Needs Ollama + a model. Set CLAUDE_LOCAL_MODEL to pick.
```

## Uninstall

```sh
./uninstall.sh   # removes the two installed files, stops the shim
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `500 system message must be at the beginning` | you are bypassing the shim. Point `ANTHROPIC_BASE_URL` at `:11500`, or set `CLAUDE_LOCAL_OLLAMA_URL` if Ollama is elsewhere |
| `claude-local: shim failed to start` | read the shim log; usually Ollama is down |
| Slow first reply, then fast | normal: the model loads into VRAM, then the KV cache keeps later turns fast |
| Connectors missing in a session | an `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` is set in the environment. Unset it |
| `403` from Ollama via a tunnel | add `--http-host-header localhost:11434` to cloudflared |

## License

MIT — see [LICENSE](LICENSE).
