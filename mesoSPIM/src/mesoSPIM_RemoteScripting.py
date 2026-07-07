"""TCP remote scripting server for mesoSPIM-control.

The server accepts one length-framed JSON command per request. Command parsing,
validation, and Core execution live in `mesoSPIM_RemoteCommands`; this file only
owns the TCP transport and token gate.
"""

import hmac
import json
import logging
from functools import partial

from .mesoSPIM_RemoteCommands import parse_call, run

logger = logging.getLogger(__name__)

ENCODING = "utf-8"
OK_MARKER = "__ZMART_OK__"


class FramingError(ValueError):
    pass


def frame(payload):
    if isinstance(payload, str):
        payload = payload.encode(ENCODING)
    return str(len(payload)).encode("ascii") + b"\n" + payload


def handle_message(core, payload):
    try:
        call, args = parse_call(payload)
        return OK_MARKER + json.dumps(run(core, call, args))
    except Exception as exc:
        return f"error: {exc}"


class FrameDecoder:
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


class RemoteScriptingServer:
    """Single-client TCP command server. A new connection replaces the old one."""

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
        logger.info(
            "Remote scripting TCP listening on %s:%d (token %s)",
            self._host, self._port, "required" if self._token else "off",
        )

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
        self._send(handle_message(self.core, message))

    def stop(self):
        if self._conn is not None:
            self._drop_client(self._conn)
        self._server.close()
        logger.info("Remote scripting TCP stopped")
