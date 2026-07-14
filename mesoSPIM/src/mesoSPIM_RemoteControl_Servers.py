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
    from mesoSPIM.src.mesoSPIM_RemoteControl_ValidateAndRunCommands import (
        capture_snap_image, complete_operation, parse_call, run, self_test,
        strict_json_loads, tool_specs,
    )
else:
    from .mesoSPIM_RemoteControl_ValidateAndRunCommands import (
        capture_snap_image, complete_operation, parse_call, run, self_test,
        strict_json_loads, tool_specs,
    )

logger = logging.getLogger(__name__)

ENCODING = "utf-8"
OK_MARKER = "__MESOSPIM_OK__"
MAX_FRAME_BYTES = 1 << 20
MAX_FRAME_HEADER_BYTES = 16
MAX_MCP_BODY_BYTES = 1 << 20


class FramingError(ValueError):
    pass


def frame(payload):
    """Return the simple '<byte-count>\\n<payload>' frame used by TCP clients."""
    if isinstance(payload, str):
        payload = payload.encode(ENCODING)
    if len(payload) > MAX_FRAME_BYTES:
        raise FramingError(f"payload exceeds {MAX_FRAME_BYTES} bytes")
    return str(len(payload)).encode("ascii") + b"\n" + payload


def read_frame(sock):
    """Read one length-framed TCP reply from a socket."""
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("TCP server closed the connection")
        buf += chunk
        if b"\n" not in buf and len(buf) > MAX_FRAME_HEADER_BYTES:
            raise FramingError("frame header is too long")
    head, _, rest = buf.partition(b"\n")
    if not head or len(head) > MAX_FRAME_HEADER_BYTES or not head.isdigit():
        raise FramingError("expected canonical byte-count header")
    length = int(head)
    if length > MAX_FRAME_BYTES:
        raise FramingError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
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
        while True:
            if b"\n" not in self._buf:
                if len(self._buf) > MAX_FRAME_HEADER_BYTES:
                    raise FramingError("frame header is too long")
                return
            head, _, rest = self._buf.partition(b"\n")
            if not head or len(head) > MAX_FRAME_HEADER_BYTES or not head.isdigit():
                raise FramingError("expected canonical byte-count header")
            length = int(head)
            if length > MAX_FRAME_BYTES:
                raise FramingError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
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
        # PRE-FLIGHT SELF-TEST (fail-closed), FIRST -- before we even import Qt or open a
        # socket: prove -- against a mock Core carrying THIS instrument's real cfg -- that the
        # loaded limits are actually enforced. If they are not (a drifted limits file, a
        # validation quirk), we raise here so the server never binds and the Core reports the
        # failure to the GUI; the real hardware is never exposed. Covers both lanes: MCP
        # forwards to this server, and every call runs the same validated dispatch this tests.
        ok, report = self_test(getattr(core, "cfg", None))
        if not ok:
            failed = [line for line in report if line.startswith("FAIL")]
            raise RuntimeError("remote-control self-test failed; not going live: " + "; ".join(failed))

        from PyQt5 import QtNetwork

        self.core = core
        self._token = token or None
        self._server = QtNetwork.QTcpServer(core)
        if not self._server.listen(QtNetwork.QHostAddress(host), int(port)):
            raise RuntimeError(f"cannot listen on {host}:{port}: {self._server.errorString()}")
        self._host = host
        self._port = int(self._server.serverPort())
        self._server.newConnection.connect(self._on_new_connection)
        self._clients = {}
        if hasattr(core, "sig_finished"):
            core.sig_finished.connect(self._on_core_finished)
        if hasattr(core, "sig_time_lapse_finished"):
            core.sig_time_lapse_finished.connect(self._on_time_lapse_finished)
        if hasattr(core, "sig_time_lapse_cancelled"):
            core.sig_time_lapse_cancelled.connect(self._on_time_lapse_finished)
        camera_signal = getattr(getattr(core, "camera_worker", None), "sig_camera_frame", None)
        if camera_signal is not None:
            camera_signal.connect(self._on_camera_frame)
        logger.info("Remote control TCP listening on %s:%d", self._host, self._port)

    @property
    def port(self):
        return self._port

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            self._clients[conn] = {
                "decoder": FrameDecoder(),
                "auth": AuthGate(self._token),
            }
            conn.readyRead.connect(partial(self._on_ready_read, conn))
            conn.disconnected.connect(partial(self._on_disconnected, conn))
            # A fast local client can send its first frame before readyRead is
            # connected.  Qt does not replay an already-emitted signal, so
            # consume anything that was buffered with the pending connection.
            if conn.bytesAvailable():
                self._on_ready_read(conn)

    def _on_disconnected(self, conn):
        self._clients.pop(conn, None)
        try:
            conn.deleteLater()
        except RuntimeError:
            pass

    def _drop_client(self, conn):
        self._clients.pop(conn, None)
        try:
            conn.disconnected.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            conn.disconnectFromHost()
            conn.deleteLater()
        except RuntimeError:
            pass

    def _on_ready_read(self, conn):
        client = self._clients.get(conn)
        if client is None:
            return
        try:
            # readyRead is not recursive.  An authenticated loopback client can
            # send its command while this slot is still replying "OK", so drain
            # all bytes that became available before returning to the event loop.
            while conn in self._clients and conn.bytesAvailable():
                client["decoder"].feed(bytes(conn.readAll()))
                for payload in client["decoder"].frames():
                    self._handle(conn, payload.decode(ENCODING, "replace"))
                    if conn not in self._clients:
                        return
        except FramingError as exc:
            self._send(conn, f"framing error: {exc}")
            self._close(conn)

    def _send(self, conn, text):
        if conn in self._clients:
            conn.write(frame(text))
            conn.flush()

    def _close(self, conn):
        if conn in self._clients:
            self._drop_client(conn)

    def _handle(self, conn, message):
        client = self._clients.get(conn)
        if client is None:
            return
        auth = client["auth"]
        if not auth.passed:
            if auth.check(message):
                self._send(conn, "OK")
            else:
                self._send(conn, "AUTH-FAILED")
                self._close(conn)
            return
        self._send(conn, handle_tcp_message(self.core, message))

    def _on_core_finished(self):
        complete_operation(self.core, "finished")

    def _on_camera_frame(self):
        capture_snap_image(self.core)

    def _on_time_lapse_finished(self):
        # Core's time-lapse scheduler can leave its public state at
        # run_acquisition_list after the final time point. The remote adapter
        # owns the remote completion contract, so normalize that stale readback
        # here without changing mesoSPIM Core or MainWindow.
        try:
            self.core.state["state"] = "idle"
        except (AttributeError, KeyError, TypeError):
            pass
        complete_operation(self.core, "time_lapse")

    def stop(self):
        for conn in list(self._clients):
            self._drop_client(conn)
        for signal, slot in (
            (getattr(self.core, "sig_finished", None), self._on_core_finished),
            (getattr(self.core, "sig_time_lapse_finished", None), self._on_time_lapse_finished),
            (getattr(self.core, "sig_time_lapse_cancelled", None), self._on_time_lapse_finished),
            (getattr(getattr(self.core, "camera_worker", None), "sig_camera_frame", None),
             self._on_camera_frame),
        ):
            if signal is not None:
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
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
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1:
                return json_response(self, 403, {"error": "multiple Origin headers"})
            origin = origins[0] if origins else ""
            if origin and not allowed_origin(origin):
                return json_response(self, 403, {"error": "origin not allowed"})
            authorizations = self.headers.get_all("Authorization", [])
            if len(authorizations) != 1:
                return json_response(self, 401, {"error": "unauthorized"})
            auth = authorizations[0]
            supplied = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not hmac.compare_digest(supplied, config.token):
                return json_response(self, 401, {"error": "unauthorized"})
            try:
                transfer_encodings = self.headers.get_all("Transfer-Encoding", [])
                if transfer_encodings:
                    return json_response(
                        self, 400, {"error": "Transfer-Encoding is unsupported"})
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not lengths[0].isdigit():
                    return json_response(self, 400, {"error": "invalid Content-Length"})
                length = int(lengths[0])
                if length > MAX_MCP_BODY_BYTES:
                    return json_response(self, 413, {"error": "request body too large"})
                body = self.rfile.read(length)
                if len(body) != length:
                    return json_response(self, 400, {"error": "truncated request body"})
                msg = strict_json_loads(body.decode(ENCODING))
            except (UnicodeError, ValueError):
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
