#!/usr/bin/env python3
"""claude-local-echo: a minimal Model Context Protocol (stdio) server.

Purpose: deterministic MCP testing for claude-local. Exposes two tools:

  echo {text}   -> "text (echoed by claude-local test server)"
  add  {a, b}   -> str(a + b)

Protocol: JSON-RPC 2.0, newline-delimited, over stdin/stdout. No dependencies.

Try it:  echo -e '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
          {"jsonrpc":"2.0","method":"notifications/initialized"}
          {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"text":"hi"}}}' \
          | python3 mcp-echo.py
"""
import json
import sys

NAME = "claude-local-echo"
VERSION = "1.0.0"

TOOLS = [
    {
        "name": "echo",
        "description": "Return the input text with a marker suffix.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two integers and return the sum.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
    },
]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def handle(req):
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": NAME, "version": VERSION},
            },
        })
    elif method == "ping":
        if rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif (method or "").startswith("notifications/"):
        return  # notifications get no response
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            text = f"{args.get('text', '')} (echoed by claude-local test server)"
            send({"jsonrpc": "2.0", "id": rid,
                  "result": {"content": [{"type": "text", "text": text}]}})
        elif name == "add":
            try:
                total = int(args.get("a", 0)) + int(args.get("b", 0))
            except (TypeError, ValueError):
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32602, "message": "a and b must be integers"}})
                return
            send({"jsonrpc": "2.0", "id": rid,
                  "result": {"content": [{"type": "text", "text": str(total)}]}})
        else:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": f"unknown tool: {name}"}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid,
              "error": {"code": -32601, "message": f"method not found: {method}"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception as err:  # noqa: BLE001 - keep the server alive
            sys.stderr.write(f"{NAME}: {err}\n")


if __name__ == "__main__":
    main()
