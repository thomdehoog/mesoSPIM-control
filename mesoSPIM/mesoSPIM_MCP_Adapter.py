"""MCP adapter for mesoSPIM Remote Control.

This process exposes MCP/HTTP and forwards every tool call to the mesoSPIM TCP
Remote Scripting server. It never talks to `mesoSPIM_Core` directly.
"""

from __future__ import annotations

import argparse
import hmac
import json
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mesoSPIM.src.mesoSPIM_RemoteCommands import tool_specs
from mesoSPIM.src.mesoSPIM_RemoteScripting import OK_MARKER, frame

ENCODING = "utf-8"


def _read_frame(sock):
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("TCP server closed the connection")
        buf += chunk
    head, _, rest = buf.partition(b"\n")
    length = int(head)
    while len(rest) < length:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("TCP server closed the connection")
        rest += chunk
    return rest[:length].decode(ENCODING, "replace")


def _tcp_call(host, port, token, name, arguments, timeout):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        if token:
            sock.sendall(frame(token))
            auth = _read_frame(sock).strip()
            if auth != "OK":
                raise RuntimeError(f"TCP authentication failed: {auth}")
        sock.sendall(frame(json.dumps({name: arguments or {}})))
        reply = _read_frame(sock)
    if not reply.startswith(OK_MARKER):
        raise RuntimeError(reply)
    return json.loads(reply[len(OK_MARKER):])


def _json_response(handler, status, payload):
    body = json.dumps(payload).encode(ENCODING)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(config):
    class MCPHandler(BaseHTTPRequestHandler):
        server_version = "mesoSPIM-MCP/1.0"

        def log_message(self, fmt, *args):
            if not config.quiet:
                super().log_message(fmt, *args)

        def do_POST(self):
            if self.path != "/mcp":
                return _json_response(self, 404, {"error": "not found"})
            origin = self.headers.get("Origin", "")
            if origin and not _allowed_origin(origin):
                return _json_response(self, 403, {"error": "origin not allowed"})
            auth = self.headers.get("Authorization", "")
            supplied = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not hmac.compare_digest(supplied, config.token):
                return _json_response(self, 401, {"error": "unauthorized"})
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                msg = json.loads(self.rfile.read(length).decode(ENCODING))
            except ValueError:
                return _json_response(self, 400, {"error": "invalid JSON"})
            reply = _mcp_reply(config, msg) if isinstance(msg, dict) else None
            if reply is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            _json_response(self, 200, reply)

    return MCPHandler


def _allowed_origin(origin):
    return origin in {
        "http://127.0.0.1", "http://localhost",
        "https://127.0.0.1", "https://localhost",
    }


def _mcp_reply(config, msg):
    rid = msg.get("id")
    if rid is None:
        return None
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "mesoSPIM MCP adapter", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": tool_specs()}
    elif method == "tools/call":
        params = msg.get("params") or {}
        try:
            data = _tcp_call(
                config.mesospim_host, config.mesospim_port, config.mesospim_token,
                params.get("name"), params.get("arguments") or {}, config.timeout,
            )
            text, is_error = json.dumps(data), False
        except Exception as exc:
            text, is_error = json.dumps({"error": str(exc)}), True
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Expose mesoSPIM Remote Control as MCP.")
    parser.add_argument("--host", default="127.0.0.1", help="MCP listen host")
    parser.add_argument("--port", type=int, default=42100, help="MCP listen port")
    parser.add_argument("--token", required=True, help="Bearer token for MCP clients")
    parser.add_argument("--mesospim-host", default="127.0.0.1", help="mesoSPIM TCP host")
    parser.add_argument("--mesospim-port", type=int, default=42000, help="mesoSPIM TCP port")
    parser.add_argument("--mesospim-token", help="mesoSPIM TCP token; defaults to --token")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    args.mesospim_token = args.mesospim_token if args.mesospim_token is not None else args.token
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args))
    print(f"mesoSPIM MCP adapter listening on http://{args.host}:{args.port}/mcp", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
