#!/usr/bin/env python3
"""claude-local shim: let Claude Code talk to Ollama's Anthropic-compatible API.

Claude Code sends Anthropic Messages API requests. Ollama (>= 0.14) accepts
them at /v1/messages — except it rejects mid-conversation messages with
role "system" ("system message must be at the beginning",
https://github.com/ollama/ollama/issues/13949). Claude Code sends exactly
those, so every agentic turn 500s.

This shim runs in front of Ollama and rewrites role:"system" messages into
user-role <system-note> blocks. Everything else passes through unchanged,
including streaming responses (token-by-token, not buffered).

Run:  python3 shim.py
Env:  CLAUDE_LOCAL_SHIM_PORT     listen port       (default 11500)
       CLAUDE_LOCAL_OLLAMA_URL   upstream Ollama   (default http://localhost:11434)
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import urllib.error
import urllib.request

HOST = os.environ.get("CLAUDE_LOCAL_SHIM_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAUDE_LOCAL_SHIM_PORT", "11500"))
UPSTREAM = os.environ.get("CLAUDE_LOCAL_OLLAMA_URL", "http://localhost:11434").rstrip("/")

# Hybrid routing (optional). When SGLANG_URL is set, requests whose model
# name is in SGLANG_MODELS go to SGLANG_URL (a native Anthropic /v1/messages
# server, e.g. SGLang); everything else goes to UPSTREAM (Ollama). This lets
# one base URL serve a big main model on SGLang and a small model on Ollama.
# SGLANG_URL empty = plain Ollama mode, no routing (existing behavior).
SGLANG_URL = os.environ.get("CLAUDE_LOCAL_SGLANG_URL", "").rstrip("/")
SGLANG_MODELS = {
    m.strip() for m in os.environ.get("CLAUDE_LOCAL_SGLANG_MODELS", "").split(",") if m.strip()
}

# Thinking override (optional). Claude Code always sends
# thinking:{"type":"adaptive"}, which servers that only know "enabled"/
# "disabled" ignore — so the checkpoint's own default applies (xhigh on
# Qwen3.8, i.e. it thinks hard about "say hi"). "off"/"on" rewrites that field
# to a type the server acts on. Empty = leave the request alone.
THINKING = os.environ.get("CLAUDE_LOCAL_THINKING", "").strip().lower()

# Native mode: the upstream speaks Anthropic itself, so skip the Ollama
# system-message rewrite and just proxy (plus any THINKING override).
NATIVE = os.environ.get("CLAUDE_LOCAL_NATIVE", "") == "1"

# Headers dropped when forwarding client -> upstream.
# content-length is recomputed by urllib for the (possibly rewritten) body.
REQ_STRIP = {
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade",
}

# Headers dropped when forwarding upstream -> client.
# NOTE: content-length is NOT in this set — the client needs it to know
# where the body ends on a keep-alive connection.
RESP_STRIP = {
    "connection", "keep-alive", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
}


def set_thinking(body: bytes) -> bytes:
    """Force the thinking mode Claude Code won't let us set."""
    if THINKING not in ("off", "on"):
        return body
    try:
        req = json.loads(body)
    except Exception:
        return body
    if not isinstance(req, dict):
        return body
    req["thinking"] = {"type": "disabled" if THINKING == "off" else "enabled"}
    return json.dumps(req).encode()


def rewrite(body: bytes) -> bytes:
    """Rewrite mid-conversation role:"system" messages for Ollama.

    Pure function: bytes in, bytes out. Safe to unit-test without a server.
    Non-JSON or non-Messages bodies are returned unchanged.
    """
    try:
        req = json.loads(body)
    except Exception:
        return body
    if not isinstance(req, dict):
        return body

    changed = False
    for msg in req.get("messages", []) or []:
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        msg["role"] = "user"
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = f"<system-note>\n{content}\n</system-note>"
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    block["text"] = f"<system-note>\n{block.get('text', '')}\n</system-note>"
        changed = True

    if not changed:
        return body
    return json.dumps(req).encode("utf-8")


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "claude-local-shim/1.0"

    def _upstream_request(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None

        model = None
        if body:
            try:
                model = json.loads(body).get("model")
            except Exception:
                model = None

        to_sglang = bool(SGLANG_URL and model and model in SGLANG_MODELS)
        upstream = SGLANG_URL if to_sglang else UPSTREAM
        # Only Ollama needs the mid-conversation system-message rewrite;
        # SGLang speaks Anthropic natively and accepts system messages as-is.
        if body and self.path.startswith("/v1/messages"):
            if not to_sglang and not NATIVE:
                body = rewrite(body)
            body = set_thinking(body)

        self.log_message("route %s -> %s (model=%s)",
                         self.path, "sglang" if to_sglang else "ollama", model)

        out = f"{upstream}{self.path}"
        req = urllib.request.Request(out, data=body, method=self.command)
        for key, value in self.headers.items():
            if key.lower() in REQ_STRIP:
                continue
            req.add_header(key, value)
        return req

    def _stream_response(self, req):
        try:
            # No timeout arg: we must stream for the whole generation.
            # (timeout=0 would mean non-blocking sockets, not "infinite".)
            resp = urllib.request.urlopen(req)
            status = resp.status
            headers = resp.headers
        except urllib.error.HTTPError as err:
            status = err.code
            headers = err.headers
            body = err.read()
            self._send_simple(status, headers, body)
            return
        except urllib.error.URLError as err:
            self._send_simple(502, {}, f"claude-local: cannot reach Ollama at {UPSTREAM} ({err.reason})".encode())
            return

        self.send_response(status)
        upstream_te = headers.get("Transfer-Encoding")
        upstream_cl = headers.get("Content-Length")
        for key, value in headers.items():
            if key.lower() in RESP_STRIP or key.lower() == "transfer-encoding":
                continue
            self.send_header(key, value)
        if upstream_te is not None and upstream_cl is None:
            # Upstream framed the body as chunks (e.g. SSE). We cannot
            # reframe incrementally, so deliver it close-delimited instead.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_simple(self, status, headers, body):
        self.send_response(status)
        for key, value in (headers or {}).items():
            lk = key.lower()
            if lk in RESP_STRIP or lk in ("content-length", "transfer-encoding"):
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        self._stream_response(self._upstream_request())

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _handle

    def log_message(self, fmt, *args):
        sys.stderr.write("[claude-local] " + (fmt % args) + "\n")
        sys.stderr.flush()


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = ThreadingServer((HOST, PORT), Proxy)
    if SGLANG_URL:
        routes = (f" -> {UPSTREAM} (ollama) / {SGLANG_URL} "
                  f"(sglang: {', '.join(sorted(SGLANG_MODELS))})")
    else:
        routes = f" -> {UPSTREAM}"
    sys.stderr.write(
        f"[claude-local] shim {HOST}:{PORT}{routes}\n"
        f"[claude-local] point Claude Code at ANTHROPIC_BASE_URL=http://{HOST}:{PORT}\n"
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
