"""Remote-control command vocabulary and execution.

Network transports hand this module a decoded command name plus arguments. This
module validates the name against a fixed allowlist and executes the matching
mesoSPIM Core action.
"""

import json
import os

_AXES = ("x", "y", "z", "f", "theta")


def parse_call(payload):
    msg = json.loads(payload)
    if not isinstance(msg, dict) or len(msg) != 1:
        raise ValueError("expected one JSON object: {'command': {args}}")
    (call, args), = msg.items()
    if not isinstance(call, str):
        raise ValueError("command name must be a string")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("command arguments must be an object")
    return call, args


def tool_specs():
    return [{"name": n, "description": _HINTS.get(n, f"mesoSPIM {n}"),
             "inputSchema": {"type": "object"}} for n in COMMANDS]


def _item_get(obj, key, default=None):
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


def _camera_pixels(cfg):
    params = _cfg_dict(cfg, "camera_parameters")
    x = params.get("x_pixels", getattr(cfg, "camera_x_pixels", 2048))
    y = params.get("y_pixels", getattr(cfg, "camera_y_pixels", 2048))
    return int(x or 2048), int(y or 2048)


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
    lasers = [{"name": n, "wavelength_nm": int("".join(c for c in str(n) if c.isdigit()) or 0) or None}
              for n in _cfg_dict(cfg, "laserdict")]
    pixelsizes = _cfg_dict(cfg, "pixelsize")
    zooms = []
    for z, raw in _cfg_dict(cfg, "zoomdict").items():
        zooms.append({"name": z, "pixel_size_um": pixelsizes.get(z, raw if isinstance(raw, (int, float)) else None)})
    pixels_x, pixels_y = _camera_pixels(cfg)
    return {"app": "mesoSPIM-control", "version": getattr(cfg, "version", None),
            "lasers": lasers, "filters": list(_cfg_dict(cfg, "filterdict")), "zooms": zooms,
            "shutter_configs": list(getattr(cfg, "shutteroptions", ["Left", "Right", "Both"])),
            "axes": list(_AXES),
            "camera": {"pixels_x": pixels_x, "pixels_y": pixels_y}}


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
    fname = acq.get("filename") or ""
    return {"started": True,
            "files": [os.path.join(acq.get("folder") or "", fname)] if fname else [],
            "planes": int(acq.get("planes", 1) or 1),
            "pixels": list(_camera_pixels(getattr(core, "cfg", None)))}


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

_HINTS = {
    "move_absolute": "move axes to absolute targets. args: {targets: {x,y,z,f,theta: um/deg}}",
    "move_relative": "move axes by relative offsets. args: {deltas: {axis: um/deg}}",
    "zero": "define the current position as zero. args: {axes: [..]} (omit = all)",
    "set_state": "change settings. args: {settings: {filter,zoom,laser,intensity,shutterconfig,etl_*}}",
    "acquire_start": "start one acquisition. args: {acquisition: {...}}",
    "stat_files": "report which output files exist and their sizes. args: {files: [path,..]}",
    "procedure": "run a named site procedure. args: {name: <procedure>}",
}


def run(core, call, args=None):
    handler = COMMANDS.get(call)
    if handler is None:
        raise KeyError(f"unknown command {call!r}; not in the allowlist")
    return handler(core, args or {})
