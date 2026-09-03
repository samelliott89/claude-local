#!/usr/bin/env python3
"""The shim must forward a chunked (SSE) upstream body as it arrives.

http.client's read(n) on a chunked body blocks until n bytes or EOF, which
turned every streamed reply into one burst at the end. read1(n) returns as
soon as data is available. This spins up a slow chunked upstream and the
real shim, and checks the first event reaches the client before the second
one is even sent.

Run:  python3 tests/test_stream.py
"""
import http.server
import importlib.util
import os
import pathlib
import socketserver
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAP = 0.5  # seconds between upstream events


class SlowSSE(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for i in range(3):
            data = f"data: {i}\n\n".encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()
            time.sleep(GAP)
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *a):
        pass


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def run():
    up_port = serve(socketserver.TCPServer(("127.0.0.1", 0), SlowSSE))
    os.environ["CLAUDE_LOCAL_OLLAMA_URL"] = f"http://127.0.0.1:{up_port}"
    spec = importlib.util.spec_from_file_location("shim_stream", ROOT / "shim.py")
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    shim_port = serve(shim.ThreadingServer(("127.0.0.1", 0), shim.Proxy))

    req = urllib.request.Request(
        f"http://127.0.0.1:{shim_port}/v1/messages", data=b'{"model":"m"}',
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    resp = urllib.request.urlopen(req)
    arrivals = []
    while True:
        chunk = resp.read1(65536)
        if not chunk:
            break
        arrivals.append((time.time() - t0, chunk))

    first_at = arrivals[0][0]
    total = b"".join(c for _, c in arrivals)
    ok_stream = first_at < GAP
    ok_body = total == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"
    print("stream unit test")
    print(f"  {'PASS' if ok_stream else 'FAIL'}  first event arrived at {first_at:.2f}s (< {GAP}s)")
    print(f"  {'PASS' if ok_body else 'FAIL'}  body intact across {len(arrivals)} reads")
    print("all tests passed" if ok_stream and ok_body else "test(s) failed")
    return 0 if ok_stream and ok_body else 1


if __name__ == "__main__":
    raise SystemExit(run())
