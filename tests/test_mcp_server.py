#!/usr/bin/env python3
"""Protocol tests for mcp-echo.py. Drives it over stdio. No LLM involved.

Run:  python3 tests/test_mcp_server.py
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp-echo.py"


def exchange(requests):
    """Send a batch of JSON-RPC lines to the server, return parsed responses."""
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload.encode(), capture_output=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"server exited {proc.returncode}: {proc.stderr.decode()}")
    return [json.loads(line) for line in proc.stdout.decode().splitlines() if line.strip()]


def result_of(responses, rid):
    for r in responses:
        if r.get("id") == rid:
            if "result" in r:
                return r["result"]
            raise AssertionError(f"unexpected error for id={rid}: {r.get('error')}")
    raise AssertionError(f"no response for id={rid}: {responses}")


def run():
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures += 1

    print("mcp-echo protocol tests")

    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "echo", "arguments": {"text": "ping-claude-local"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "add", "arguments": {"a": 19, "b": 23}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "bogus/method"},
    ]
    resps = exchange(reqs)

    init = result_of(resps, 1)
    check("initialize: serverInfo", init["serverInfo"]["name"] == "claude-local-echo")
    check("initialize: protocol echo", init["protocolVersion"] == "2025-06-18")

    tools = result_of(resps, 2)["tools"]
    names = {t["name"] for t in tools}
    check("tools/list: echo+add", names == {"echo", "add"})
    check("tools/list: schemas present",
          all("inputSchema" in t and t["inputSchema"]["type"] == "object" for t in tools))

    echo = result_of(resps, 3)["content"][0]["text"]
    check("echo: marker", echo == "ping-claude-local (echoed by claude-local test server)")

    add = result_of(resps, 4)["content"][0]["text"]
    check("add: 19+23=42", add == "42")

    err5 = next(r for r in resps if r.get("id") == 5)
    check("unknown tool -> error", "error" in err5)
    err6 = next(r for r in resps if r.get("id") == 6)
    check("unknown method -> error", "error" in err6)
    check("notification produced no response",
          all(r.get("id") is not None for r in resps))

    if failures:
        print(f"{failures} test(s) FAILED")
        sys.exit(1)
    print("all tests passed")


if __name__ == "__main__":
    run()
