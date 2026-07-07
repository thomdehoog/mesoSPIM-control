"""
mesoSPIM remote scripting server.
==================================
Lets an external program control mesoSPIM by **named calls, not code**. Two lanes
over one port, both ending at the same allowlist:

- scripts (framed TCP): send ``{"<method>": {args}}``, get ``__ZMART_OK__<json>`` back.
- LLMs (MCP over HTTP): POST a JSON-RPC ``tools/call``, get a JSON-RPC result.

The method name is looked up in the fixed :data:`COMMANDS` table and the matching
``mesoSPIM_Core`` call runs -- the same methods the GUI's own buttons call. An
unknown name is rejected before anything moves. No client Python is ever run.

Safety (a call still moves the stage / runs acquisitions, so this matters):
- off until an operator starts it from the GUI; binds 127.0.0.1 by default;
- optional shared token, compared in constant time (a Bearer header over HTTP);
- over HTTP, any non-localhost ``Origin`` is rejected, so a web page open in the
  operator's browser can't drive the instrument (DNS-rebinding / CSRF).
A call runs on the Core's own (GUI) thread, so the GUI is busy until it returns --
keep calls short.

Map of this file, top to bottom:
  frame() / FrameDecoder   the "<byte-count>\\n<payload>" wire framing (TCP lane)
  AuthGate                 the constant-time token check
  COMMANDS + _dispatch     the allowlist: a name -> one Core call (READ THIS FIRST)
  handle_message           the TCP lane: {"<method>": {args}} -> reply text
  _mcp_reply               the HTTP lane's brain: JSON-RPC in -> JSON-RPC out
  RemoteScriptingServer    the Qt glue: accept a client, read bytes, route to the above

Author: Thom de Hoog (ZMB, University of Zurich). License: GPL-3.0 (part of
mesoSPIM-control; it uses the GPL Core API).
"""

import hmac
import json
import logging
import os
import traceback
from functools import partial

logger = logging.getLogger(__name__)

ENCODING = "utf-8"
OK_MARKER = "__ZMART_OK__"  # reply prefix; the JSON result follows on the same line
_AXES = ("x", "y", "z", "f", "theta")


class FramingError(ValueError):
    """A frame's length prefix was not a plain integer."""


def frame(payload):
    """Wrap payload as b"<byte-length>\\n" + payload -- used for both requests and replies."""
    if isinstance(payload, str):
        payload = payload.encode(ENCODING)  # the wire only carries bytes, so text has to be encoded first
    # "<how many bytes follow>\n" then the bytes themselves, so the reader knows exactly where it ends
    return str(len(payload)).encode("ascii") + b"\n" + payload


# ==========================================================================
# Restricted mode: a fixed allowlist of named calls (no exec).
# ==========================================================================
# Each handler is fn(core, args) -> dict. Writes return {} (the client confirms
# by reading state back); reads build the result from `core`. `core` is the live
# mesoSPIM_Core -- the same object execute_script sees as `self`. This table IS
# the allowlist: a name not in it never runs. Verified against v1.20.0.


def _item_get(obj, key, default=None):
    """Read from dict-like objects or mesoSPIM_StateSingleton without assuming .get()."""
    if obj is None:
        return default
    get = getattr(obj, "get", None)
    if callable(get):
        return get(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError, AttributeError):
        return default


def _state_get(core, key, default=None):
    return _item_get(getattr(core, "state", None), key, default)


def _state_position(core):
    pos = _state_get(core, "position", {}) or {}
    return {ax: _item_get(pos, ax, _item_get(pos, ax + "_pos")) for ax in _AXES}


def _cfg_dict(cfg, name):
    value = getattr(cfg, name, None)
    return value if isinstance(value, dict) else {}


def _move_absolute(core, a):
    core.move_absolute({k + "_abs": float(v) for k, v in a["targets"].items()}, wait_until_done=True)
    return {}


def _move_relative(core, a):
    core.move_relative({k + "_rel": float(v) for k, v in a["deltas"].items()}, wait_until_done=True)
    return {}


def _zero(core, a):
    core.zero_axes(list(a.get("axes") or _AXES))
    return {}


def _stop(core, a):
    core.sig_stop_movement.emit()
    return {}


def _set_state(core, a):
    core.sig_state_request_and_wait_until_done.emit(dict(a["settings"]))
    return {}


def _hello(core, a):
    cfg = getattr(core, "cfg", None)
    return {"app": "mesoSPIM-control", "version": getattr(cfg, "version", None),
            "protocol": 1, "state": _state_get(core, "state")}


def _ping(core, a):
    return {"pong": True, "state": _state_get(core, "state")}


def _get_position(core, a):
    return _state_position(core)


def _get_state(core, a):
    keys = ("laser", "intensity", "filter", "zoom", "shutterconfig",
            "etl_l_amplitude", "etl_l_offset", "etl_r_amplitude", "etl_r_offset")
    out = {"state": _state_get(core, "state"), "position": _state_position(core)}
    out.update({k: _state_get(core, k) for k in keys})
    return out


def _get_config(core, a):
    cfg = getattr(core, "cfg", None)
    ld = _cfg_dict(cfg, "laserdict")
    lasers = [{"name": n, "wavelength_nm": int("".join(c for c in str(n) if c.isdigit()) or 0) or None}
              for n in ld]
    zd = _cfg_dict(cfg, "zoomdict")
    pixelsizes = _cfg_dict(cfg, "pixelsize")
    zooms = []
    for z in zd:
        pixel_size = pixelsizes.get(z)
        if pixel_size is None and isinstance(zd.get(z), (int, float)):
            pixel_size = zd.get(z)
        zooms.append({"name": z, "pixel_size_um": pixel_size})
    camera_parameters = _cfg_dict(cfg, "camera_parameters")
    pixels_x = camera_parameters.get("x_pixels", getattr(cfg, "camera_x_pixels", 2048))
    pixels_y = camera_parameters.get("y_pixels", getattr(cfg, "camera_y_pixels", 2048))
    return {"app": "mesoSPIM-control", "version": getattr(cfg, "version", None),
            "lasers": lasers, "filters": list(_cfg_dict(cfg, "filterdict")), "zooms": zooms,
            "shutter_configs": list(getattr(cfg, "shutteroptions", ["Left", "Right", "Both"])),
            "axes": list(_AXES),
            "camera": {"pixels_x": int(pixels_x or 2048), "pixels_y": int(pixels_y or 2048)}}


def _get_progress(core, a):
    return {"state": _state_get(core, "state"),
            "current_plane": _state_get(core, "current_framenumber"),
            "total_planes": _state_get(core, "snap_count"),
            "current_acquisition": _state_get(core, "current_acquisition"),
            "total_acquisitions": _state_get(core, "total_acquisitions")}


def _acquire_start(core, a):
    try:
        from .utils.acquisitions import Acquisition, AcquisitionList
    except ImportError:
        from utils.acquisitions import Acquisition, AcquisitionList
    acq = dict(a["acquisition"])
    obj = Acquisition()
    obj.update({k: v for k, v in acq.items() if v is not None})
    st = core.state
    try:
        core._zmart_prev_acq_list = (True, st["acq_list"])
    except (KeyError, TypeError):
        core._zmart_prev_acq_list = (False, None)
    st["acq_list"] = AcquisitionList([obj])
    core.start(row=0)
    cfg = getattr(core, "cfg", None)
    fname = acq.get("filename") or ""
    return {"started": True,
            "files": [os.path.join(acq.get("folder") or "", fname)] if fname else [],
            "planes": int(acq.get("planes", 1) or 1),
            "pixels": [int(getattr(cfg, "camera_x_pixels", 2048) or 2048),
                       int(getattr(cfg, "camera_y_pixels", 2048) or 2048)]}


def _stat_files(core, a):
    files = [str(f) for f in (a.get("files") or [])]
    return {"missing": [f for f in files if not os.path.isfile(f)],
            "sizes": {f: os.path.getsize(f) for f in files if os.path.isfile(f)}}


def _acquire_finish(core, a):
    st = core.state
    had, prev = getattr(core, "_zmart_prev_acq_list", (False, None))
    if had:
        st["acq_list"] = prev
    else:
        try:
            del st["acq_list"]
        except Exception:
            st["acq_list"] = prev
    try:
        del core._zmart_prev_acq_list
    except AttributeError:
        pass
    return {"state": _state_get(core, "state")}


def _procedure(core, a):
    raise RuntimeError(f"procedure {a.get('name')!r} is not implemented server-side")


COMMANDS = {
    "hello": _hello, "ping": _ping,
    "get_state": _get_state, "get_position": _get_position,
    "get_config": _get_config, "get_progress": _get_progress,
    "move_absolute": _move_absolute, "move_relative": _move_relative,
    "zero": _zero, "stop": _stop, "set_state": _set_state,
    "acquire_start": _acquire_start, "stat_files": _stat_files, "acquire_finish": _acquire_finish,
    "procedure": _procedure,
}

# Argument shape per call, so an LLM's tools/list is self-describing. A call not
# listed here takes no args and just uses its bare name.
_HINTS = {
    "move_absolute": "move axes to absolute targets. args: {targets: {x,y,z,f,theta: um/deg}}",
    "move_relative": "move axes by relative offsets. args: {deltas: {axis: um/deg}}",
    "zero": "define the current position as zero. args: {axes: [..]} (omit = all)",
    "set_state": "change settings. args: {settings: {filter,zoom,laser,intensity,shutterconfig,etl_*}}",
    "acquire_start": "start one acquisition. args: {acquisition: {...}}",
    "stat_files": "report which output files exist and their sizes. args: {files: [path,..]}",
    "procedure": "run a named site procedure. args: {name: <procedure>}",
}


def _dispatch(core, call, args):
    """One allowlisted call -> result dict (unknown method -> KeyError). The single
    point where a request becomes a Core call, shared by both front ends."""
    if call not in COMMANDS:
        raise KeyError(f"unknown command {call!r}")
    return COMMANDS[call](core, args or {})


def handle_message(core, payload):
    """A framed named call ``{"<method>": {args}}`` -> ``__ZMART_OK__<json>`` / error text.
    (The MCP/LLM lane is HTTP -- see :meth:`RemoteScriptingServer._http_handle`.)"""
    try:
        (call, args), = json.loads(payload).items()  # exactly one key: method -> args
        return OK_MARKER + json.dumps(_dispatch(core, call, args))
    except Exception as exc:
        return f"error: {exc}"


def _mcp_reply(core, msg):
    """MCP (LLM) path: a JSON-RPC request -> a response string (None for a notification)."""
    rid = msg.get("id")
    if rid is None:
        return None  # a notification (e.g. notifications/initialized) gets no reply
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "mesoSPIM", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": _HINTS.get(n, f"mesoSPIM {n}"),
                             "inputSchema": {"type": "object"}} for n in COMMANDS]}
    elif method == "tools/call":
        p = msg.get("params") or {}
        try:
            text, err = json.dumps(_dispatch(core, p.get("name"), p.get("arguments") or {})), False
        except Exception as exc:
            text, err = json.dumps({"error": str(exc)}), True
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    else:
        return json.dumps({"jsonrpc": "2.0", "id": rid,
                           "error": {"code": -32601, "message": f"method not found: {method}"}})
    return json.dumps({"jsonrpc": "2.0", "id": rid, "result": result})


class FrameDecoder:
    """Turns a stream of bytes back into whole frames.

    TCP can split one message across reads or join several into one, so we
    can't assume one readAll() call is one frame. Feed it bytes as they
    arrive; frames() hands back only the ones that are fully in.

    Kept free of sockets/Qt so it can be unit-tested on its own.
    """

    def __init__(self):
        self._buf = b""  # everything received so far that hasn't turned into a full frame yet

    def feed(self, data):
        """Add newly-arrived bytes to the buffer; call frames() afterwards to collect any full ones."""
        self._buf += bytes(data)

    def frames(self):
        """Yield each complete frame's payload, oldest first, removing it from the buffer."""
        while b"\n" in self._buf:
            # Everything before the first "\n" is the decimal length; "rest" is everything
            # after it, which may be just this frame's payload, or that payload plus the
            # start of the next frame too (frames() loops again to peel that one off).
            head, _, rest = self._buf.partition(b"\n")
            try:
                length = int(head)
            except ValueError as exc:
                raise FramingError("expected '<byte-count>\\n<payload>'") from exc
            if length < 0:
                # int() accepts a leading "-", but a negative length isn't a byte count --
                # letting it through would slice rest[] from the wrong end below
                raise FramingError(f"byte-count can't be negative: {length}")
            if len(rest) < length:
                return  # the payload hasn't fully arrived yet -- stop here and wait for more feed()
            self._buf = rest[length:]  # drop the frame we're about to yield, keep anything after it
            yield rest[:length]


class AuthGate:
    """Checks a shared token in constant time, so a client can't learn it one
    byte at a time by measuring how fast a wrong guess is rejected.
    """

    def __init__(self, token=None):
        self._token = token or None  # an empty string counts as "no token", same as None
        self.passed = self._token is None  # no token configured -> there is nothing to check

    @property
    def required(self):
        """Whether a client has to send a token before anything else is accepted."""
        return self._token is not None

    def check(self, supplied):
        """Compare supplied against the real token; remembers success in .passed."""
        if isinstance(supplied, str):
            supplied = supplied.encode(ENCODING)  # compare_digest needs bytes; a non-ASCII token is a str
        # constant-time compare: a normal == would return faster on an early mismatching byte,
        # which an attacker could time to guess the token one byte at a time
        ok = hmac.compare_digest(supplied, str(self._token).encode(ENCODING))
        if ok:
            self.passed = True
        return ok


class RemoteScriptingServer:
    """Listens on one TCP port and dispatches each received call against COMMANDS.

    Only one client at a time: a new connection replaces whatever was there
    before, so a client that crashes without closing its socket can't block
    the next one from connecting.

    Raises RuntimeError if the port can't be bound (e.g. already in use), so
    the caller can report that instead of showing a false "running".
    """

    def __init__(self, core, host="127.0.0.1", port=42000, token=None):
        from PyQt5 import QtNetwork  # imported here, not at module load, so this file stays Qt-free for tests

        self.core = core  # the live mesoSPIM Core; each call runs a method on this
        self._token = token or None  # an empty string counts as "no token"
        self._server = QtNetwork.QTcpServer(core)  # parented to Core, so Qt destroys it when Core is destroyed
        if not self._server.listen(QtNetwork.QHostAddress(host), int(port)):
            raise RuntimeError(f"cannot listen on {host}:{port}: {self._server.errorString()}")
        self._host, self._port = host, int(port)  # remembered only for the log line below
        self._server.newConnection.connect(self._on_new_connection)  # call us back whenever a client connects
        self._conn = None  # the one active client socket, if any
        self._decoder = FrameDecoder()  # reassembles that client's bytes into whole messages
        self._auth = AuthGate(self._token)  # tracks whether that client has presented the token yet
        logger.info(
            "Remote scripting listening on %s:%d (token %s)",
            self._host, self._port, "required" if self._token else "off",
        )

    # -- connection lifecycle ------------------------------------------------

    def _on_new_connection(self):
        """Called by Qt whenever a client connects. Accepts it, replacing any earlier client."""
        conn = self._server.nextPendingConnection()
        if self._conn is not None:
            self._drop_client(self._conn)  # only one client allowed at a time; the old one goes
        self._conn = conn
        self._decoder = FrameDecoder()  # fresh decoder/auth state for the new connection
        self._auth = AuthGate(self._token)
        self._mode = None  # "framed" (scripts/named calls) or "http" (MCP) -- decided from the first bytes
        self._httpbuf = b""
        conn.readyRead.connect(self._on_ready_read)  # call us back whenever this client sends bytes
        # partial(), not a lambda, so the callback keeps its OWN reference to this exact conn.
        # self._conn can already have moved on by the time the callback actually runs (e.g. _close()
        # below sets it to None before Qt gets around to emitting disconnected), so the callback
        # can't just read self._conn when it fires -- it has to bring conn along with it.
        conn.disconnected.connect(partial(self._on_disconnected, conn))

    def _on_disconnected(self, conn):
        """Called once conn has actually disconnected (client closed it, or we did in _close())."""
        if conn is self._conn:
            self._conn = None
        try:
            conn.deleteLater()  # tell Qt it's safe to free this socket once nothing is using it
        except RuntimeError:
            pass  # Qt already destroyed the underlying C++ socket before this queued signal fired

    def _drop_client(self, conn):
        """Forcibly disconnect conn, e.g. because a new client just took its place."""
        if conn is self._conn:
            self._conn = None
        try:
            conn.disconnected.disconnect()  # so this old client can't also trigger _on_disconnected later
        except (TypeError, RuntimeError):
            pass  # nothing was connected, or the socket is already gone -- either way, nothing to undo
        try:
            conn.disconnectFromHost()
            conn.deleteLater()
        except RuntimeError:
            pass  # already reclaimed by Qt

    # -- framing / dispatch --------------------------------------------------

    def _on_ready_read(self):
        """Called by Qt whenever the client has sent more bytes.

        The one socket carries two shapes. We decide once, from the first bytes:
        an HTTP request line (``POST ... HTTP/1.1``) means an MCP-over-HTTP client
        (off-the-shelf LLM tools); anything else is the length-framed protocol
        (scripts / named calls). Both end up on the same allowlist dispatch.
        """
        if self._conn is None:
            return
        data = bytes(self._conn.readAll())
        if self._mode is None:
            self._mode = "http" if data[:5] in (b"POST ", b"GET /", b"HEAD ", b"OPTIO") else "framed"
        if self._mode == "http":
            self._http_feed(data)
            return
        self._decoder.feed(data)
        try:
            for payload in self._decoder.frames():  # usually zero or one frame, but handle several at once too
                self._handle(payload.decode(ENCODING, "replace"))
                if self._conn is None:
                    return  # this frame closed the connection (e.g. auth failed) -- drop anything queued after it
        except FramingError as exc:
            self._send(f"framing error: {exc}")
            self._close()

    def _send(self, text):
        """Frame text and write it back to the client, if one is still connected."""
        if self._conn is not None:
            self._conn.write(frame(text))
            self._conn.flush()

    def _close(self):
        """Ask Qt to disconnect the current client."""
        if self._conn is not None:
            self._conn.disconnectFromHost()
            self._conn = None  # _on_disconnected() still runs later and deleteLater()s the real socket

    def _handle(self, message):
        """Handle one already-unframed message: either the token, or a call to run."""
        if not self._auth.passed:
            # First message after connecting must be the token, when one is configured.
            if self._auth.check(message):
                self._send("OK")
            else:
                self._send("AUTH-FAILED")
                self._close()
            return
        self._send(handle_message(self.core, message))  # named call -> allowlist; no client code runs

    # -- MCP over HTTP (off-the-shelf LLM clients) ---------------------------
    # The MCP Streamable-HTTP transport: the client POSTs a JSON-RPC message and
    # gets a JSON-RPC response. Same dispatch as everything else -- HTTP only adds
    # a request envelope, and two HTTP-specific guards (Origin + Bearer token).

    def _http_feed(self, data):
        """Accumulate bytes and handle each complete HTTP request (supports keep-alive)."""
        self._httpbuf += data
        while b"\r\n\r\n" in self._httpbuf:  # a full header block is in
            head, _, rest = self._httpbuf.partition(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            method = lines[0].split(b" ", 1)[0].decode("latin1")
            headers = {}
            for ln in lines[1:]:
                k, _, v = ln.partition(b":")
                headers[k.strip().lower()] = v.strip()
            length = int(headers.get(b"content-length", b"0") or b"0")
            if len(rest) < length:
                return  # body not fully arrived yet -- wait for more bytes
            body = rest[:length]
            self._httpbuf = rest[length:]
            self._http_handle(method, headers, body)
            if self._conn is None:
                return

    def _http_handle(self, method, headers, body):
        """One HTTP request -> one JSON-RPC response, after the two HTTP guards."""
        # Guard 1 (DNS-rebinding / CSRF): a browser attaches an Origin header. A
        # malicious page could POST to http://127.0.0.1 from the operator's browser;
        # reject any Origin that isn't localhost so only real MCP clients get through.
        origin = headers.get(b"origin", b"").decode("latin1")
        local = ("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost")
        if origin and not origin.startswith(local):
            return self._http_respond(403, b'{"error":"origin not allowed"}')
        # Guard 2 (auth): when a token is set, require it as a Bearer header --
        # the same shared secret as the framed path, compared in constant time.
        if self._token is not None:
            auth = headers.get(b"authorization", b"")
            supplied = auth[7:] if auth[:7].lower() == b"bearer " else b""
            if not hmac.compare_digest(supplied, str(self._token).encode(ENCODING)):
                return self._http_respond(401, b'{"error":"unauthorized"}')
        if method != "POST":  # no server-initiated SSE stream; POST JSON-RPC only
            return self._http_respond(405, b'{"error":"POST a JSON-RPC message"}')
        try:
            msg = json.loads(body.decode(ENCODING))
        except ValueError:
            return self._http_respond(400, b'{"error":"invalid JSON"}')
        reply = _mcp_reply(self.core, msg) if isinstance(msg, dict) else None
        if reply is None:  # a notification -> accepted, no body
            return self._http_respond(202, b"")
        self._http_respond(200, reply.encode(ENCODING))

    def _http_respond(self, status, body):
        """Write a minimal HTTP/1.1 JSON response."""
        reason = {200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized",
                  403: "Forbidden", 405: "Method Not Allowed"}.get(status, "OK")
        header = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
                  f"Content-Length: {len(body)}\r\n\r\n").encode("latin1")
        if self._conn is not None:
            self._conn.write(header + body)
            self._conn.flush()

    def stop(self):
        """Close the listening socket. Safe to call more than once."""
        if self._conn is not None:
            self._drop_client(self._conn)
        self._server.close()
        logger.info("Remote scripting stopped")
