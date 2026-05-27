#!/usr/bin/env python3
"""
RTI HTTP Client Driver — Test Server
------------------------------------
Runs a plain HTTP/1.1 server that logs every request the driver sends and
returns a configurable response.

Usage:
    python test_server.py [--host HOST] [--port PORT] [--status STATUS]
                          [--body BODY] [--response-file PATH]

Examples:
    # Listen on all interfaces, port 8080, default 200 OK response
    python test_server.py --port 8080

    # Reply with a custom body (e.g. to test LastResponse system variable)
    python test_server.py --port 8080 --status 200 --body '{"ok":true}'

    # Reply with a body read from a file
    python test_server.py --port 8080 --response-file response.json

RTI driver config:
    Default Host  →  IP of the machine running this script
    Default Port  →  --port value (default 8080)
"""

import argparse
import datetime
import http.server
import json
import os
import sys
import textwrap
from typing import Optional


# ---------------------------------------------------------------------------
# ANSI colours (disabled automatically when not a TTY)
# ---------------------------------------------------------------------------

USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def green(t):  return _c("92", t)
def yellow(t): return _c("93", t)
def cyan(t):   return _c("96", t)
def red(t):    return _c("91", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)


# ---------------------------------------------------------------------------
# Global config (set after arg parsing)
# ---------------------------------------------------------------------------

g_response_status: int = 200
g_response_body: bytes = b'{"status":"ok"}'
g_response_content_type: str = "application/json"
g_request_count: int = 0


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class RTITestHandler(http.server.BaseHTTPRequestHandler):

    # Suppress built-in access log — we print our own nicer version
    def log_message(self, fmt, *args):
        pass

    def _send_response(self):
        self.send_response(g_response_status)
        self.send_header("Content-Type", g_response_content_type)
        self.send_header("Content-Length", str(len(g_response_body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(g_response_body)
        self.wfile.flush()

    def _handle(self):
        global g_request_count
        g_request_count += 1

        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        sep = "─" * 60

        # Read optional body
        body_bytes = b""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            body_bytes = self.rfile.read(content_length)

        # ── Print incoming request ──────────────────────────────────────────
        print()
        print(dim(sep))
        print(
            bold(f"[#{g_request_count}]"),
            cyan(ts),
            bold(green(self.command)),
            yellow(self.path),
            dim(f"HTTP/{self.request_version.split('/')[-1] if '/' in self.request_version else '1.1'}")
        )
        print(dim(f"  From: {self.client_address[0]}:{self.client_address[1]}"))

        if self.headers:
            print(dim("  Headers:"))
            for key, val in self.headers.items():
                print(dim(f"    {key}: {val}"))

        if body_bytes:
            body_text = body_bytes.decode("utf-8", errors="replace")
            print(dim("  Body:"))
            for line in body_text.splitlines():
                print(dim(f"    {line}"))

        # ── Send response ───────────────────────────────────────────────────
        self._send_response()

        status_label = green(str(g_response_status)) if g_response_status < 400 else red(str(g_response_status))
        resp_preview = g_response_body.decode("utf-8", errors="replace")[:80].replace("\n", "\\n")
        if len(g_response_body) > 80:
            resp_preview += "…"
        print(f"  → replied {status_label}  {dim(repr(resp_preview))}")
        print(dim(sep))

    # Route all methods to the same handler
    def do_GET(self):    self._handle()
    def do_POST(self):   self._handle()
    def do_PUT(self):    self._handle()
    def do_PATCH(self):  self._handle()
    def do_DELETE(self): self._handle()
    def do_HEAD(self):   self._handle()
    def do_OPTIONS(self):self._handle()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Simple HTTP test server for the RTI HTTP Client driver",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__).strip(),
    )
    p.add_argument("--host", default="0.0.0.0",
                   help="Interface to listen on (default: 0.0.0.0 = all interfaces)")
    p.add_argument("--port", type=int, default=8080,
                   help="TCP port to listen on (default: 8080)")
    p.add_argument("--status", type=int, default=200,
                   help="HTTP status code to return (default: 200)")
    p.add_argument("--body", default=None,
                   help="Response body string (default: '{\"status\":\"ok\"}')")
    p.add_argument("--response-file", default=None, metavar="PATH",
                   help="Read response body from a file instead of --body")
    p.add_argument("--content-type", default=None, metavar="MIME",
                   help="Response Content-Type (auto-detected from file extension, default: application/json)")
    return p.parse_args()


def detect_content_type(path: str) -> str:
    import mimetypes
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def main():
    global g_response_status, g_response_body, g_response_content_type

    args = parse_args()
    g_response_status = args.status

    if args.response_file:
        if not os.path.isfile(args.response_file):
            print(red(f"ERROR: response file not found: {args.response_file}"), file=sys.stderr)
            sys.exit(1)
        with open(args.response_file, "rb") as f:
            g_response_body = f.read()
        g_response_content_type = args.content_type or detect_content_type(args.response_file)
    elif args.body is not None:
        g_response_body = args.body.encode()
        g_response_content_type = args.content_type or "text/plain"
    else:
        # default JSON body
        g_response_content_type = args.content_type or "application/json"

    server_address = (args.host, args.port)

    try:
        httpd = http.server.HTTPServer(server_address, RTITestHandler)
    except OSError as e:
        print(red(f"ERROR: cannot bind {args.host}:{args.port} — {e}"), file=sys.stderr)
        sys.exit(1)

    # Find a useful display address
    display_host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host

    print(bold("RTI HTTP Client Driver — Test Server"))
    print(f"  Listening on  {bold(f'http://{display_host}:{args.port}/')}")
    print(f"  Bind address  {args.host}:{args.port}")
    print(f"  Response      {bold(str(g_response_status))}  ({len(g_response_body)} bytes)")
    print()
    print("RTI driver settings:")
    print(f"  Default Host  →  {bold(display_host)}")
    print(f"  Default Port  →  {bold(str(args.port))}")
    print()
    print(dim("Waiting for requests… (Ctrl-C to stop)"))

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        print(bold(f"Stopped. {g_request_count} request(s) received."))


if __name__ == "__main__":
    main()
