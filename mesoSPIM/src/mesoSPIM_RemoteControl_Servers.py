"""Remote-control TCP and MCP servers for mesoSPIM, and the clients that call them.

The TCP server runs inside mesoSPIM-control and executes validated JSON commands. The MCP
server is a small standalone HTTP server that forwards MCP tool calls to that same TCP
server, so both protocols share one command path.

The clients live here too, next to the servers that speak to them, so the wire format is
written exactly once:

    from mesoSPIM.src.mesoSPIM_RemoteControl_Servers import RemoteControl, mcp_call

    scope = RemoteControl(port=42000, token="...")
    scope.call("move_absolute", targets={"x": 100})

Everything at module level here is standard library, so a script can import the clients
without pulling in Qt or any hardware driver.

Run as a script, this starts the MCP bridge. That is how the GUI launches it.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import socket
import sys
import urllib.request
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


def frame_length(head):
    """The payload length a frame header promises, refusing anything non-canonical.

    Canonical means ASCII digits and nothing else: no sign, no padding, no whitespace, so
    "+1" and "1 0" are refused rather than quietly coerced. Both framing implementations
    below call this, because two copies of a security check are two chances for one of them
    to be the laxer. The size cap is enforced here, on the header alone, so an oversized
    frame is refused before a byte of its payload is read or buffered.
    """
    if not head or len(head) > MAX_FRAME_HEADER_BYTES or not head.isdigit():
        raise FramingError("expected canonical byte-count header")
    length = int(head)
    if length > MAX_FRAME_BYTES:
        raise FramingError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    return length


class FrameReader:
    """Reads length-framed replies from a socket it owns, blocking until each is complete.

    It keeps any bytes that belong to the NEXT frame, so a client whose replies arrive
    coalesced does not silently lose one. This is the blocking counterpart of FrameDecoder,
    which can never block because it runs on Qt's event loop; both call frame_length, so the
    header rules cannot drift apart.
    """

    def __init__(self, sock):
        self._sock = sock
        self._buf = b""

    def read(self):
        while b"\n" not in self._buf:
            if len(self._buf) > MAX_FRAME_HEADER_BYTES:
                raise FramingError("frame header is too long")
            self._buf += self._recv()
        head, _, rest = self._buf.partition(b"\n")
        length = frame_length(head)
        while len(rest) < length:
            rest += self._recv()
        self._buf = rest[length:]
        return rest[:length].decode(ENCODING, "replace")

    def _recv(self):
        chunk = self._sock.recv(4096)
        if not chunk:
            raise ConnectionError("TCP server closed the connection")
        return chunk


def read_frame(sock):
    """One frame from a socket carrying nothing after it: one-shot callers and tests."""
    return FrameReader(sock).read()


def handle_tcp_message(core, payload):
    try:
        call, args = parse_call(payload)
        return OK_MARKER + json.dumps(run(core, call, args))
    except Exception as exc:
        return f"error: {exc}"


class FrameDecoder:
    """Incremental decoder for TCP frames arriving from Qt sockets.

    The non-blocking counterpart of read_frame: Qt hands us whatever bytes have arrived, so a
    frame can be split across readyRead signals and several frames can arrive in one. It
    therefore keeps a buffer and yields only complete frames, returning quietly when it needs
    more -- it must never wait on the socket, because it runs on the Core's event loop.
    """

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
            length = frame_length(head)
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
    """Single-client framed JSON TCP server hosted by mesoSPIM Core.

    A plain Python object, deliberately not a QObject. That keeps the completion signals as
    direct connections, which is the live-validated behaviour -- and it means _on_camera_frame
    runs ON THE CAMERA THREAD while command dispatch mutates the same Core-owned session from
    the Core thread. Two known consequences, neither of them fixable inside a cleanup:

    - Cross-thread session access. capture_snap_image writes the snapshot from the camera
      thread; dispatch writes the operation from the Core thread. Preserved as-is.
    - A snap whose camera frame never arrives wedges the busy gate. The operation stays active
      on milestone "snap_image"; stop only moves it to "stopping", which is still active, and
      sig_finished cannot close a "snap_image" milestone. Every mutating command then raises
      BusyError until mesoSPIM restarts. Server Stop/Start is NOT a recovery path: it
      deliberately preserves the Core-owned session, precisely so a GUI click cannot clear a
      busy gate while the instrument is running. Operation-ID matching would not fix this
      either -- it prevents a stale callback completing a NEWER operation, and says nothing
      about a completion signal that never arrives. Real recovery needs a timeout, a
      cancel-on-stop transition, or a recovery command. All are out of scope here.
    """

    def __init__(self, core, host="127.0.0.1", port=42000, token=None):
        """Smoke-check the limits, then bind. Fail-closed, and in that order.

        The smoke-check runs FIRST, before Qt is even imported or a socket opened: it drives a
        mock Core carrying THIS instrument's real cfg and checks the loaded limits still refuse
        an out-of-range call. If they do not -- a drifted limits file, a validation quirk --
        this raises, the server never binds, and Core reports the failure to the tab. The
        hardware is never exposed. It covers both lanes, because MCP forwards to this server
        and every call runs the same validated dispatch.
        """
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
        self._connections = [
            (getattr(core, "sig_finished", None), self._on_core_finished),
            (getattr(core, "sig_time_lapse_finished", None), self._on_time_lapse_finished),
            (getattr(core, "sig_time_lapse_cancelled", None), self._on_time_lapse_finished),
            (getattr(getattr(core, "camera_worker", None), "sig_camera_frame", None),
             self._on_camera_frame),
        ]
        for signal, slot in self._connections:
            if signal is not None:
                signal.connect(slot)
        logger.info("Remote control TCP listening on %s:%d", self._host, self._port)

    @property
    def port(self):
        return self._port

    def _on_new_connection(self):
        """Accept each pending client, then read what it may already have sent.

        A fast local client can deliver its first frame before readyRead is connected, and Qt
        does not replay an already-emitted signal. Without the explicit drain, that first frame
        would sit in the socket buffer until more bytes arrived -- and the MCP bridge, which
        sends its token and then waits, would hang instead of authenticating.
        """
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            self._clients[conn] = {
                "decoder": FrameDecoder(),
                "auth": AuthGate(self._token),
            }
            conn.readyRead.connect(partial(self._on_ready_read, conn))
            conn.disconnected.connect(partial(self._on_disconnected, conn))
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
        """Drain everything that has arrived, not just the first frame.

        readyRead is not recursive: Qt will not re-enter this slot while it is running. An
        authenticated loopback client can send its command while we are still writing "OK", so
        bytes that arrive during this call would sit unread until the NEXT readyRead -- which
        may never come, because the client is waiting for our reply. Hence the drain loop, and
        hence the re-check of conn after each frame: a handler may have dropped the client.
        """
        client = self._clients.get(conn)
        if client is None:
            return
        try:
            while conn in self._clients and conn.bytesAvailable():
                client["decoder"].feed(bytes(conn.readAll()))
                for payload in client["decoder"].frames():
                    self._handle(conn, payload.decode(ENCODING, "replace"))
                    if conn not in self._clients:
                        return
        except FramingError as exc:
            self._send(conn, f"framing error: {exc}")
            self._drop_client(conn)

    def _send(self, conn, text):
        if conn in self._clients:
            conn.write(frame(text))
            conn.flush()

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
                self._drop_client(conn)
            return
        self._send(conn, handle_tcp_message(self.core, message))

    def _on_core_finished(self):
        complete_operation(self.core, "finished")

    def _on_camera_frame(self):
        """Runs ON THE CAMERA THREAD -- this is a direct connection. See the class docstring."""
        capture_snap_image(self.core)

    def _on_time_lapse_finished(self):
        """Complete the remote time-lapse operation, normalizing Core's stale state readback.

        Core's time-lapse scheduler can leave its public state at run_acquisition_list after
        the final time point. The remote adapter owns the remote completion contract, so the
        readback is normalized here rather than by changing mesoSPIM Core or MainWindow.
        """
        try:
            self.core.state["state"] = "idle"
        except (AttributeError, KeyError, TypeError):
            pass
        complete_operation(self.core, "time_lapse")

    def stop(self):
        """Drop the clients, disconnect the completion signals, close the listener.

        The disconnect walks the same _connections list the connect did, so the two can no
        longer drift apart: a signal added to one and forgotten in the other would leave this
        object alive, listening to a Core it no longer serves. The Core-owned session is
        deliberately left alone -- see the class docstring.
        """
        for conn in list(self._clients):
            self._drop_client(conn)
        for signal, slot in self._connections:
            if signal is not None:
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._server.close()
        logger.info("Remote control TCP stopped")


class RemoteControl:
    """The TCP client: connect once, authenticate once, then make named calls.

    The MCP bridge, the test suite and any operator script all drive the microscope through
    this one class, so the wire format is implemented once rather than mirrored in a separate
    example file where it could silently drift from the server that speaks it.

    The error contract is the protocol's, not Python's: a reply that does not carry the OK
    marker IS the error text, so it is raised verbatim rather than reshaped.
    """

    def __init__(self, host="127.0.0.1", port=42000, token=None, timeout=10.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._reader = FrameReader(self._sock)
        if token:
            self._sock.sendall(frame(token))
            reply = self._reader.read().strip()
            if reply != "OK":
                self.close()
                raise RuntimeError(f"TCP authentication failed: {reply}")

    def call(self, name, **arguments):
        """Send ``{name: arguments}`` and return the decoded result."""
        self._sock.sendall(frame(json.dumps({name: arguments})))
        reply = self._reader.read()
        if not reply.startswith(OK_MARKER):
            raise RuntimeError(reply.strip())
        return json.loads(reply[len(OK_MARKER):])

    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def mcp_call(host, port, token, method, name=None, arguments=None, timeout=10.0):
    """The MCP client: POST one JSON-RPC message and return the decoded reply.

    The Origin header is sent because the server refuses anything but a loopback origin;
    the Bearer token guards the public MCP endpoint.
    """
    params = {"name": name, "arguments": arguments or {}} if name else {}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(ENCODING)
    headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://{host}:{port}/mcp", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read().decode(ENCODING))


def tcp_call(host, port, token, name, arguments, timeout):
    """One named call on its own connection: what the MCP bridge makes per tools/call."""
    with RemoteControl(host, port, token, timeout=timeout) as scope:
        return scope.call(name, **(arguments or {}))


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
    """Start this file as the standalone MCP server used by the GUI.

    Launched by absolute path, not with -m: the child would then depend on inheriting a
    working directory, and the failure mode of that assumption is "the MCP lane silently does
    not start". The path is derived from the package directory the GUI already knows, and the
    sys.path shim at the top of this file is what lets the child re-import the command module
    when it runs with no package. The QProcess is parented to the caller, so whoever started
    it reaps it.
    """
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
