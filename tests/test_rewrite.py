#!/usr/bin/env python3
"""Unit tests for the shim's request rewriter. No network, no server.

Run:  python3 tests/test_rewrite.py
"""
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("shim", ROOT / "shim.py")
shim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shim)


def run():
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures += 1

    print("rewrite() unit tests")

    # 1. Mid-conversation system message (string content) becomes user.
    body = json.dumps({
        "model": "m", "max_tokens": 5,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "a note"},
            {"role": "user", "content": "bye"},
        ],
    }).encode()
    out = json.loads(shim.rewrite(body))
    mid = out["messages"][1]
    check("system->user (string)", mid["role"] == "user")
    check("text wrapped in <system-note>",
          "<system-note>" in mid["content"] and "a note" in mid["content"])
    check("neighbors untouched",
          out["messages"][0] == {"role": "user", "content": "hi"}
          and out["messages"][2] == {"role": "user", "content": "bye"})

    # 2. System message with block-array content.
    body = json.dumps({"messages": [
        {"role": "system", "content": [{"type": "text", "text": "note"}]},
    ]}).encode()
    out = json.loads(shim.rewrite(body))
    m0 = out["messages"][0]
    check("system->user (blocks)", m0["role"] == "user")
    check("block text wrapped",
          "<system-note>" in m0["content"][0]["text"])

    # 3. Body without system messages: byte-identical passthrough.
    body = json.dumps({"model": "m",
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    check("no-system body unchanged", shim.rewrite(body) == body)

    # 4. Non-JSON body: unchanged.
    check("non-JSON unchanged", shim.rewrite(b"hello") == b"hello")

    # 5. Top-level "system" field is Ollama's system prompt: must stay.
    body = json.dumps({"system": "top",
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    out = json.loads(shim.rewrite(body))
    check("top-level system preserved", out.get("system") == "top")

    # 6. Real Claude Code shape: top-level system blocks + tools + thinking.
    body = json.dumps({
        "model": "m", "max_tokens": 10, "stream": True,
        "system": [{"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": "t", "description": "d",
                   "input_schema": {"type": "object", "properties": {}}}],
        "thinking": {"type": "adaptive", "display": "omitted"},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "system", "content": [{"type": "text", "text": "ctx"}]},
        ],
    }).encode()
    out = json.loads(shim.rewrite(body))
    check("real shape: tools intact", len(out["tools"]) == 1)
    check("real shape: thinking intact", out["thinking"]["type"] == "adaptive")
    check("real shape: top system intact", out["system"][0]["text"] == "A")
    check("real shape: mid system rewritten",
          out["messages"][1]["role"] == "user"
          and "<system-note>" in out["messages"][1]["content"][0]["text"])

    print(f"{'' if failures else 'all tests passed'}".strip() or "all tests passed")
    if failures:
        print(f"{failures} test(s) FAILED")
        sys.exit(1)


if __name__ == "__main__":
    run()
