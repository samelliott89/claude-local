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

- macOS or Linux (Windows: use WSL)
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
CLAUDE_LOCAL_MODEL=qwen3:8b claude-local   # any tag from `ollama list`
claude-local --resume                     # all extra args pass to claude
claude-local -p "say hi" --max-turns 1    # headless
```

The launcher starts the shim if it is not running, then execs `claude`
with `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, and
`ANTHROPIC_DEFAULT_HAIKU_MODEL` set.

### SGLang, vLLM, FreeToken: direct servers

```sh
claude-local --sglang                     # SGLang on http://localhost:30000
claude-local --sglang=http://host:port    # server elsewhere
claude-local --vllm                       # vLLM on http://127.0.0.1:8000, thinking off
claude-local --vllm --think on            # ...with chain-of-thought enabled
claude-local --freetoken                  # FreeToken engine on http://127.0.0.1:1919
claude-local --freetoken --think off      # ...with chain-of-thought disabled
```

SGLang serves the Anthropic Messages API natively (`/v1/messages`), so
`--sglang` skips the shim entirely and points Claude Code straight at the
server. The model is auto-picked from the server's `/v1/models`
(`CLAUDE_LOCAL_MODEL` overrides). Any server that speaks `/v1/messages`
works — the flag name is just honest about what it was built for.
Launcher-owned flags must come before `claude`'s own args.

If the server is down, the launcher starts it: it runs
`CLAUDE_LOCAL_SGLANG_START` if set, else `sglang-serve start` if a script
by that name is on PATH. The command must return once the server is
healthy. `CLAUDE_LOCAL_NO_AUTOSTART=1` disables this, like the shim.
It deliberately sets **no** `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`.
Your Claude login (if any) stays the auth source.
That keeps claude.ai connectors available (see below).

#### Don't "fix" the FlashInfer version mismatch

A CUDA-13 SGLang venv can show a version mismatch between
`flashinfer-cubin` and `flashinfer-python` (e.g. 0.6.13 vs 0.6.17), and
importing `flashinfer` or `sgl_kernel` fails with:

```
RuntimeError: flashinfer-cubin version (0.6.13) does not match
flashinfer version (0.6.17).
```

**This is cosmetic. Leave it alone.** `FLASHINFER_DISABLE_VERSION_CHECK=1`
bypasses the check and everything imports and runs correctly — set that in
whatever launches the server. The mismatch cannot be reconciled anyway:
`flashinfer-cubin` has no release matching the newer `flashinfer-python`.

Trying to downgrade instead (`pip install flashinfer-python==<cubin ver>`)
fails partway through *and drags torch down with it* — cu130 → cu128 —
which breaks `sgl_kernel` with a much less obvious error:

```
ImportError: .../sgl_kernel/.../common_ops.abi3.so: undefined symbol:
_ZNR5torch7Library4_defEON3c1014FunctionSchemaE...
```

If you already did this, restore with:

```sh
pip install --index-url https://download.pytorch.org/whl/cu130 torch==2.13.0
pip install 'cuda-python>=13.0'
```

Then check `python -m pip check` is clean and both imports work under
`FLASHINFER_DISABLE_VERSION_CHECK=1`.

#### Attention backend

Let SGLang choose it. It picks `flashinfer`, which is significantly faster
than `triton` at the long contexts Claude Code actually runs — measured on
a 27B FP8 model: **+47% decode at 30k, +59% at 120k**. Pinning
`--attention-backend triton` (a common workaround for the import error
above) costs you exactly that.

Note that decode speed is rarely the real bottleneck: long turns are
dominated by *prefill* of the uncached context, which no flag fixes.
Shrinking the context (`/compact`) is what makes turns fast.

### Running the SGLang server: `sglang-serve`

`--sglang` needs a server to talk to. `sglang-serve` is the reference
launcher this repo ships for one — a thin, opinionated wrapper around
`sglang.launch_server`, installed alongside `claude-local`. Skip it if you
run SGLang some other way; `--sglang` does not care who started the server.

```sh
sglang-serve start              # serve $SGLANG_MODEL
sglang-serve start <hf-id>      # serve a specific model
sglang-serve stop               # stop, and wait for VRAM to actually free
sglang-serve status
sglang-serve log
```

It exists mainly to encode a few things that are easy to get wrong:

- **No `--attention-backend`.** SGLang picks `flashinfer`; see above for why
  pinning `triton` is a bad trade.
- **`FLASHINFER_DISABLE_VERSION_CHECK=1`** is set, which is the correct fix
  for the cubin/python mismatch — not downgrading anything.
- **Ollama models are evicted before launch.** SGLang sizes its KV pool
  *once*, from free VRAM at startup, and holds that size for the process
  lifetime. An 11GB model resident at start cost us 286878 → 120266 tokens
  of pool. A pool smaller than your session hard-fails with
  `Input length exceeds maximum allowed length` — which is what makes
  `/compact` fail in long sessions.
- **`stop` kills the whole process tree and blocks until VRAM is free.**
  The scheduler child holds the memory, not the launcher pid, so a naive
  `kill` leaves the GPU occupied and the next `start` sizes a tiny pool.

Defaults suit one large GPU. Every knob is env-overridable:
`SGLANG_VENV`, `SGLANG_PORT`, `SGLANG_MODEL`, `SGLANG_ALT_MODEL`,
`SGLANG_DRAFT_MODEL` (empty disables speculative decoding),
`SGLANG_DRAFT_TOKENS`, `SGLANG_MEM_FRACTION`, `SGLANG_ENABLE_METRICS`,
`SGLANG_REASONING_PARSER`,
`SGLANG_TOOL_PARSER`, and `SGLANG_EXTRA_ARGS` appended verbatim.

Two model-specific notes, if you serve Qwen3.8 like we do: it needs
`--reasoning-parser qwen3 --tool-call-parser qwen3_coder` (the template
emits `<function=...>` XML, not hermes JSON), and it is a hybrid Mamba
model — each request reserves 5 Mamba state slots, so the default cache
silently caps concurrency at 1. `--mamba-ssm-dtype bfloat16
--max-mamba-cache-size 20` (both set here) took us from 1 to 4 concurrent
requests, a 2.66x aggregate speedup. Avoid `--chunked-prefill-size 32768`:
it doubles CUDA-graph capture shapes and OOMs at any useful pool size.

### Monitoring: `/metrics` and `sglang-stats`

`sglang-serve` starts the server with `--enable-metrics`, so it serves
Prometheus metrics at `http://localhost:30000/metrics`
(`SGLANG_ENABLE_METRICS=0` turns it off). Point Prometheus/Grafana at that
endpoint if you want dashboards — SGLang ships a compose stack in its
`examples/monitoring` directory.

For a quick look without any of that, `sglang-stats` distills the ~80
metrics down to the ones worth watching:

```sh
sglang-stats           # print once
sglang-stats watch     # refresh every 2s
```

```
model    orcarouter/Qwen3.8-27B-Uncensored-FP8
load     running=1 queued=0 throughput=43 tok/s
kv pool  686217 / 712136 free   usage=3.6%  prefix-hit=61.2%
memory   weights=28.6GB kv=21.7GB cudagraph=3.4GB
spec     accept-rate=21.1% accept-len=2.475
latency  ttft=2.80s queue=0.00s
sizes    prompt=12883 uncached=12883 generated=186 (mean tokens)
```

What to actually look at:

- **`uncached`** — prompt tokens re-prefilled from scratch each turn. This
  is the real cost driver in long sessions and what `/compact` reduces.
  If it tracks `prompt` closely, prefix caching is not helping you.
- **`kv pool`** — if free tokens fall below your session length, requests
  hard-fail with `Input length exceeds maximum allowed length`. A pool much
  smaller than expected means something held VRAM when the server started.
- **`accept-rate`** — speculative decoding's real hit rate. Expect
  0.15–0.30 on prose and code; predictable output inflates it well above
  what you will see in practice, so tune against a realistic workload.
- **`ttft` vs `throughput`** — a high TTFT with healthy throughput means
  you are prefill-bound, not decode-bound. No sampling flag fixes that.

Note the metric namespace is `sglang:foo` with a colon, not an underscore —
easy to miss when writing your own queries.

### FreeToken: `--freetoken` and `freetoken-serve`

```sh
claude-local --freetoken                        # engine on http://127.0.0.1:1919
claude-local --freetoken=http://host:port       # engine elsewhere
```

FreeToken's engine (`ft serve`) also serves the Anthropic Messages API at
`/v1/messages`, so `--freetoken` behaves exactly like `--sglang`: no shim, the
model auto-picked from `/v1/models` (the engine reports the model dir's
basename as the id, e.g. `Qwen3.8-27B-ftw`), and `CLAUDE_LOCAL_MODEL`
overrides.

```sh
freetoken-serve start              # first model in ~/.freetoken/models
freetoken-serve start <model-dir>  # a specific model
freetoken-serve stop               # stop, and wait for VRAM to actually free
freetoken-serve status
freetoken-serve log
```

`freetoken-serve` is the same kind of wrapper `sglang-serve` is for SGLang —
it wraps `ft-freetoken serve --model-path <model> --port 1919`, waits for
`/health`, and on `stop` kills the whole process group and blocks until VRAM is
released. `claude-local --freetoken` runs `freetoken-serve start`
automatically when the engine is down (disable with
`CLAUDE_LOCAL_NO_AUTOSTART=1`).

The `ft-freetoken` wrapper matters: on Blackwell (sm_120f) FlashInfer's JIT
needs a CUDA ≥ 12.9 toolkit, and the wrapper points it at the CUDA-13 wheels
inside the FreeToken venv. If you launch `ft` some other way, make sure that
env is set or the kernels will fail to build.

VRAM: the engine and an SGLang server do not coexist — stop one before starting
the other (`sglang-serve stop`, then `freetoken-serve start`, or vice versa).

### Turning thinking down: `--think`

```sh
claude-local --freetoken --think off     # no chain-of-thought
claude-local --freetoken --think on      # force it on
```

Claude Code always sends `thinking: {"type": "adaptive"}`. Local servers only
act on `"enabled"` / `"disabled"`, so `adaptive` falls through to whatever the
checkpoint defaults to — `xhigh` on Qwen3.8, which means it reasons at full
effort about "say hi". `--think` puts the shim in front as a native passthrough
and rewrites that one field. On an identical request: 75 output tokens / 2.9s
became 4 tokens / 0.2s.

Costs one local hop, so it is opt-in; without `--think` the direct modes stay
direct. Graded levels (`low`/`medium`/`high`) are not available here —
FreeToken takes `reasoning_effort` only on its OpenAI endpoint, and Claude Code
speaks the Anthropic one.

### vLLM: `--vllm` and `vllm-serve`

```sh
claude-local --vllm                  # vLLM server on :8000, thinking off
claude-local --vllm --think on       # enable thinking via passthrough shim
claude-local --vllm=http://host:port # custom server (omit /v1)
```

vLLM serves an OpenAI-compatible API, so `--vllm` routes through the shim on
port 11503 (a dedicated port to avoid colliding with the SGLang think shim on
11502). The shim translates `thinking` into `chat_template_kwargs.enable_thinking`
and passes everything else through. A context-budget 400 triggers one retry
with a reduced `max_tokens` (computed via `/v1/messages/count_tokens`);
conversation text is preserved.

`vllm-serve` is the reference launcher, mirroring `sglang-serve`:

```sh
vllm-serve start              # serve $VLLM_MODEL
vllm-serve start <path|id>    # serve a specific model
vllm-serve stop               # stop, kill the whole process group
vllm-serve status
vllm-serve log
```

Defaults: 128K context, 4 sequences, FP8 KV cache, 0.72 GPU memory fraction,
7 speculative tokens (DFlash2). `VLLM_DRAFT_MODEL=""` disables speculation.
Every knob is env-overridable: `VLLM_VENV`, `VLLM_PORT`, `VLLM_MODEL`,
`VLLM_MAX_LEN`, `VLLM_MEM_FRACTION`, `VLLM_DRAFT_MODEL`, `VLLM_DRAFT_TOKENS`,
`VLLM_MAX_NUM_SEQS`, `VLLM_REASONING_PARSER`, `VLLM_TOOL_PARSER`,
`VLLM_EXTRA_ARGS`, `VLLM_LOCAL_CACHE_BASE`.

Blackwell (sm_120) notes: the venv's `nvidia/cu13` toolkit is placed on
`PATH`/`CUDA_HOME`/`LD_LIBRARY_PATH` so FlashInfer can describe the GPU.
`FLASHINFER_DISABLE_VERSION_CHECK=1` handles the cubin/python version mismatch.
`FlashInferFP8ScaledMMLinearKernel` is disabled (cuDNN mismatch); the native
CUTLASS NVFP4 kernel remains enabled. This is a machine-specific tested preset,
not a claim that every NVFP4 model supports these settings.

VRAM: vLLM and SGLang do not coexist on one GPU — stop one before starting
the other.

### Hybrid: big model on SGLang, small model on Ollama

```sh
claude-local --hybrid
```

Claude Code uses a small "haiku" model for background chores —
conversation titles, compaction — and the main model for everything else.
`--hybrid` serves both at once: the big model on SGLang, a small one on
Ollama, behind a single base URL.

The shim does the splitting. In hybrid mode it becomes a model-aware
router: requests whose model matches `CLAUDE_LOCAL_SGLANG_MODELS` go
straight to SGLang untouched, everything else goes to Ollama with the
usual system-message rewrite. It listens on its own port (11501) so it
never collides with a plain-Ollama shim on 11500.

```
CLAUDE_LOCAL_SGLANG_BASE   http://localhost:30000   SGLang URL
CLAUDE_LOCAL_SMALL_MODEL   (a small Ollama model)   the haiku slot
CLAUDE_LOCAL_HYBRID_PORT   11501                    routing shim port
```

The router logs every request as `route <path> -> sglang|ollama
(model=...)`, in `${XDG_RUNTIME_DIR:-/tmp}/claude-local/shim-hybrid.log` —
useful for confirming which slot a given piece of work actually lands in.

Watch VRAM: both models resident on one GPU is tight. SGLang reserves its
KV pool at startup and the Ollama model loads into whatever is left, so
**do not restart SGLang while the small model is resident** — it would
size its pool against the leftovers and stay that way.

### Move a session between hosted and local

Claude Code sessions are transcripts on disk. The model is stateless and
re-reads the transcript every turn, from whatever backend the launcher
points at. So a session started on a hosted Claude model continues on a
local model — and back — with no conversion:

```sh
claude --resume                    # start or continue a session on hosted Claude
claude-local --resume              # same session list — continue any of them locally
claude-local --continue            # most recent session, whoever made it
claude --resume <session-id>      # take a locally-made session back to hosted
```

Use it to draft cheaply on the local model and switch to a hosted model
for the hard steps, or to keep working through an outage or offline.

Caveats: the first local turn re-processes the whole transcript through
your GPU (long wait on big sessions); sessions longer than the local
model's context window need compaction first; and a small local model
inherits a hosted-model transcript gracefully but performs like itself,
not like the model that wrote it.

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `CLAUDE_LOCAL_MODEL` | first model in `ollama list` | model name sent in requests |
| `CLAUDE_LOCAL_SHIM_PORT` | `11500` | shim listen port |
| `CLAUDE_LOCAL_OLLAMA_URL` | `http://localhost:11434` | Ollama upstream |
| `CLAUDE_LOCAL_SHIM` | `<script dir>/shim.py` | explicit shim path |
| `CLAUDE_LOCAL_SHIM_BIN` | `python3` | shim interpreter |
| `CLAUDE_LOCAL_NO_AUTOSTART` | `0` | `1` = never auto-start the shim |
| `CLAUDE_LOCAL_SGLANG_START` | `sglang-serve start` | command to bring SGLang up |
| `CLAUDE_LOCAL_SGLANG_BASE` | `http://localhost:30000` | SGLang URL for `--hybrid` |
| `CLAUDE_LOCAL_SMALL_MODEL` | a small Ollama model | haiku slot for `--hybrid` |
| `CLAUDE_LOCAL_HYBRID_PORT` | `11501` | routing shim port for `--hybrid` |
| `CLAUDE_LOCAL_SGLANG_URL` | — | shim: route matching models here |
| `CLAUDE_LOCAL_SGLANG_MODELS` | — | shim: comma-separated models to route |
| `CLAUDE_LOCAL_FREETOKEN_BASE` | `http://127.0.0.1:1919` | FreeToken URL for `--freetoken` |
| `CLAUDE_LOCAL_FREETOKEN_START` | `freetoken-serve start` | command to bring FreeToken up |
| `FREETOKEN_PORT` | `1919` | freetoken-serve listen port |
| `FREETOKEN_MODEL` | first dir in `~/.freetoken/models` | freetoken-serve model path |
| `FREETOKEN_FT_BIN` | `ft-freetoken` on PATH | freetoken-serve ft binary |
| `FREETOKEN_ARGS` | tuned set (see script) | extra `ft serve` flags |
| `CLAUDE_LOCAL_THINK` | — | default for `--think` (`off`/`on`) |
| `CLAUDE_LOCAL_THINK_PORT` | `11502` | port for the `--think` shim |
| `CLAUDE_LOCAL_VLLM_BASE` | `http://127.0.0.1:8000` | vLLM URL for `--vllm` |
| `CLAUDE_LOCAL_VLLM_START` | `vllm-serve start` | command to bring vLLM up |

Shim log: `${XDG_RUNTIME_DIR:-/tmp}/claude-local/shim.log`
(hybrid: `shim-hybrid.log`). SGLang server log:
`${XDG_RUNTIME_DIR:-/tmp}/sglang-serve/server.log`. FreeToken engine log:
`${XDG_RUNTIME_DIR:-/tmp}/freetoken-serve/server.log`. vLLM server log:
`${XDG_RUNTIME_DIR:-/tmp}/vllm-serve/server.log`.

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
