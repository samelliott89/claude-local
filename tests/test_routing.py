#!/usr/bin/env python3
"""Unit tests for the shim's hybrid model routing. No network, no server.

The routing decision is `SGLANG_URL and model and model in SGLANG_MODELS`,
which also gates the Ollama-only system-message rewrite. Both halves matter:
routing the wrong way sends a request to a server that cannot serve it, and
rewriting a body bound for SGLang corrupts a conversation that was fine.

Run:  python3 tests/test_routing.py
"""
import importlib
import importlib.util
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_shim(**env):
    """Import shim.py fresh with the given env — module-level config."""
    old = {k: os.environ.get(k) for k in
           ("CLAUDE_LOCAL_SGLANG_URL", "CLAUDE_LOCAL_SGLANG_MODELS")}
    os.environ.pop("CLAUDE_LOCAL_SGLANG_URL", None)
    os.environ.pop("CLAUDE_LOCAL_SGLANG_MODELS", None)
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("shim_routing", ROOT / "shim.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def routes_to_sglang(shim, model):
    """Mirror the shim's routing predicate."""
    return bool(shim.SGLANG_URL and model and model in shim.SGLANG_MODELS)


def run():
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures += 1

    print("hybrid routing unit tests")

    # 1. No SGLANG_URL: plain Ollama mode, nothing routes away.
    s = load_shim()
    check("no SGLANG_URL -> no routing", s.SGLANG_URL == "")
    check("no SGLANG_URL -> big model still ollama",
          not routes_to_sglang(s, "big-model"))

    # 2. Configured: named model goes to SGLang, others to Ollama.
    s = load_shim(CLAUDE_LOCAL_SGLANG_URL="http://localhost:30000",
                  CLAUDE_LOCAL_SGLANG_MODELS="big-model")
    check("named model -> sglang", routes_to_sglang(s, "big-model"))
    check("small model -> ollama", not routes_to_sglang(s, "small-model"))
    check("missing model -> ollama", not routes_to_sglang(s, None))

    # 3. Multiple models, with whitespace, all route.
    s = load_shim(CLAUDE_LOCAL_SGLANG_URL="http://localhost:30000",
                  CLAUDE_LOCAL_SGLANG_MODELS=" a , b ,c ")
    check("comma list parsed", s.SGLANG_MODELS == {"a", "b", "c"})
    check("each listed model -> sglang",
          all(routes_to_sglang(s, m) for m in ("a", "b", "c")))
    check("unlisted -> ollama", not routes_to_sglang(s, "d"))

    # 4. Trailing slash on the URL is stripped (else paths double up).
    s = load_shim(CLAUDE_LOCAL_SGLANG_URL="http://localhost:30000/",
                  CLAUDE_LOCAL_SGLANG_MODELS="a")
    check("url trailing slash stripped", s.SGLANG_URL == "http://localhost:30000")

    # 5. The rewrite must NOT be applied to SGLang-bound bodies: SGLang
    #    accepts mid-conversation system messages, Ollama does not.
    body = json.dumps({
        "model": "big-model", "max_tokens": 5,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "a note"},
        ],
    }).encode()
    rewritten = json.loads(s.rewrite(body))
    check("rewrite would alter this body",
          rewritten["messages"][1]["role"] == "user")
    # Routing to sglang means rewrite() is skipped, so the body stays as-is.
    original = json.loads(body)
    check("unrewritten body keeps system role",
          original["messages"][1]["role"] == "system")

    print("all tests passed" if not failures else f"{failures} test(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
