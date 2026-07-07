"""Remote-control TCP and MCP servers for mesoSPIM.

The TCP server runs inside mesoSPIM-control and executes validated JSON
commands. The MCP server is a small standalone HTTP server that forwards MCP
tool calls to that same TCP server, so both protocols share one command path.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import socket
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from mesoSPIM.src.mesoSPIM_RemoteControl_ValidateAndRunCommands import parse_call, run, tool_specs
else:
    from .mesoSPIM_RemoteControl_ValidateAndRunCommands import parse_call, run, tool_specs

logger = logging.getLogger(__name__)

ENCODING = "utf-8"
OK_MARKER = "__ZMART_OK__"


class FramingError(ValueError):
    pass


def frame(payload):
    """Return the simple '<byte-count>\\n<payload>' frame used by TCP clients."""
    if isinstance(payload, str):
        payload = payload.encode(ENCODING)
    return str(len(payload)).encode("ascii") + b"\n" + payload


def read_frame(sock):
    """Read one length-framed TCP reply from a socket."""
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


def handle_tcp_message(core, payload):
    try:
        call, args = parse_call(payload)
        return OK_MARKER + json.dumps(run(core, call, args))
    except Exception as exc:
        return f"error: {exc}"


class FrameDecoder:
    """Incremental decoder for TCP frames arriving from Qt sockets."""

    def __init__(self):
        self._buf = b""

    def feed(self, data):
        self._buf += bytes(data)

    def frames(self):
        while b"\n" in self._buf:
            head, _, rest = self._buf.partition(b"\n")
            try:
                length = int(head)
            except ValueError as exc:
                raise FramingError("expected '<byte-count>\\n<payload>'") from exc
            if length < 0:
                raise FramingError(f"byte-count can't be negative: {length}")
            if len(rest) < length:
                return
            self._buf = rest[length:]
            yield rest[:length]


class AuthGate:
    """One-token gate; after a correct token, later frames are commands."""

    def __init__(self, token=None):
        self._token = token or None
        self.passed = self._token is None

    def check(self, supplied):
        if isinstance(supplied, str):
            supplied = supplied.encode(ENCODING)
        ok = hmac.compare_digest(supplied, str(self._token).encode(ENCODING))
        if ok:
            self.passed = True
        return ok


class RemoteControlTCPServer:
    """Single-client framed JSON TCP server hosted by mesoSPIM Core."""

    def __init__(self, core, host="127.0.0.1", port=42000, token=None):
        from PyQt5 import QtNetwork

        self.core = core
        self._token = token or None
        self._server = QtNetwork.QTcpServer(core)
        if not self._server.listen(QtNetwork.QHostAddress(host), int(port)):
            raise RuntimeError(f"cannot listen on {host}:{port}: {self._server.errorString()}")
        self._host = host
        self._port = int(self._server.serverPort())
        self._server.newConnection.connect(self._on_new_connection)
        self._conn = None
        self._decoder = FrameDecoder()
        self._auth = AuthGate(self._token)
        logger.info("Remote control TCP listening on %s:%d", self._host, self._port)

    @property
    def port(self):
        return self._port

    def _on_new_connection(self):
        conn = self._server.nextPendingConnection()
        if self._conn is not None:
            self._drop_client(self._conn)
        self._conn = conn
        self._decoder = FrameDecoder()
        self._auth = AuthGate(self._token)
        conn.readyRead.connect(self._on_ready_read)
        conn.disconnected.connect(partial(self._on_disconnected, conn))

    def _on_disconnected(self, conn):
        if conn is self._conn:
            self._conn = None
        try:
            conn.deleteLater()
        except RuntimeError:
            pass

    def _drop_client(self, conn):
        if conn is self._conn:
            self._conn = None
        try:
            conn.disconnected.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            conn.disconnectFromHost()
            conn.deleteLater()
        except RuntimeError:
            pass

    def _on_ready_read(self):
        if self._conn is None:
            return
        self._decoder.feed(bytes(self._conn.readAll()))
        try:
            for payload in self._decoder.frames():
                self._handle(payload.decode(ENCODING, "replace"))
                if self._conn is None:
                    return
        except FramingError as exc:
            self._send(f"framing error: {exc}")
            self._close()

    def _send(self, text):
        if self._conn is not None:
            self._conn.write(frame(text))
            self._conn.flush()

    def _close(self):
        if self._conn is not None:
            self._conn.disconnectFromHost()
            self._conn = None

    def _handle(self, message):
        if not self._auth.passed:
            if self._auth.check(message):
                self._send("OK")
            else:
                self._send("AUTH-FAILED")
                self._close()
            return
        self._send(handle_tcp_message(self.core, message))

    def stop(self):
        if self._conn is not None:
            self._drop_client(self._conn)
        self._server.close()
        logger.info("Remote control TCP stopped")


def tcp_call(host, port, token, name, arguments, timeout):
    """Call the mesoSPIM TCP server from the MCP server."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        if token:
            sock.sendall(frame(token))
            auth = read_frame(sock).strip()
            if auth != "OK":
                raise RuntimeError(f"TCP authentication failed: {auth}")
        sock.sendall(frame(json.dumps({name: arguments or {}})))
        reply = read_frame(sock)
    if not reply.startswith(OK_MARKER):
        raise RuntimeError(reply)
    return json.loads(reply[len(OK_MARKER):])


def json_response(handler, status, payload):
    body = json.dumps(payload).encode(ENCODING)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def allowed_origin(origin):
    return origin in {
        "http://127.0.0.1", "http://localhost",
        "https://127.0.0.1", "https://localhost",
    }


def make_mcp_handler(config):
    class MCPHandler(BaseHTTPRequestHandler):
        server_version = "mesoSPIM-MCP/1.0"

        def log_message(self, fmt, *args):
            if not config.quiet:
                super().log_message(fmt, *args)

        def do_POST(self):
            if self.path != "/mcp":
                return json_response(self, 404, {"error": "not found"})
            origin = self.headers.get("Origin", "")
            if origin and not allowed_origin(origin):
                return json_response(self, 403, {"error": "origin not allowed"})
            auth = self.headers.get("Authorization", "")
            supplied = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not hmac.compare_digest(supplied, config.token):
                return json_response(self, 401, {"error": "unauthorized"})
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                msg = json.loads(self.rfile.read(length).decode(ENCODING))
            except ValueError:
                return json_response(self, 400, {"error": "invalid JSON"})
            reply = mcp_reply(config, msg) if isinstance(msg, dict) else None
            if reply is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            json_response(self, 200, reply)

    return MCPHandler


def mcp_reply(config, msg):
    rid = msg.get("id")
    if rid is None:
        return None
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "mesoSPIM MCP server", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": tool_specs()}
    elif method == "tools/call":
        params = msg.get("params") or {}
        try:
            data = tcp_call(
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


def start_mcp_server_process(parent, package_directory, host, port, token, tcp_token, tcp_port):
    """Start this file as the standalone MCP server used by the GUI."""
    from PyQt5 import QtCore

    script = str(Path(package_directory) / "src" / "mesoSPIM_RemoteControl_Servers.py")
    proc = QtCore.QProcess(parent)
    proc.start(sys.executable, [
        script,
        "--host", host,
        "--port", str(port),
        "--token", token,
        "--mesospim-host", "127.0.0.1",
        "--mesospim-port", str(tcp_port),
        "--mesospim-token", tcp_token,
    ])
    if not proc.waitForStarted(3000):
        return False, proc.errorString(), None
    QtCore.QThread.msleep(300)
    if proc.state() == QtCore.QProcess.NotRunning:
        stderr = bytes(proc.readAllStandardError()).decode(ENCODING, "replace").strip()
        return False, stderr or "MCP server exited during startup", None
    return True, f"{host}:{port}", proc


def stop_mcp_server_process(proc):
    """Stop a QProcess returned by start_mcp_server_process."""
    if proc is None:
        return
    proc.terminate()
    if not proc.waitForFinished(2000):
        proc.kill()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Expose mesoSPIM Remote Control as MCP.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=42100)
    parser.add_argument("--token", required=True, help="Bearer token for MCP clients")
    parser.add_argument("--mesospim-host", default="127.0.0.1")
    parser.add_argument("--mesospim-port", type=int, default=42000)
    parser.add_argument("--mesospim-token", help="mesoSPIM TCP token; defaults to --token")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    args.mesospim_token = args.mesospim_token if args.mesospim_token is not None else args.token
    server = ThreadingHTTPServer((args.host, args.port), make_mcp_handler(args))
    print(f"mesoSPIM MCP server listening on http://{args.host}:{args.port}/mcp", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
