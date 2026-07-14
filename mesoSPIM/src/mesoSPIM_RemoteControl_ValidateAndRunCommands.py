"""Remote-control command vocabulary and execution.

Network transports hand this module a decoded command name plus arguments. This
module validates the name against a fixed allowlist and executes the matching
mesoSPIM Core action.
"""

import base64
import hashlib
import json
import logging
import math
import os

logger = logging.getLogger(__name__)

_AXES = ("x", "y", "z", "f", "theta")
_POSITION_KEYS = {"x": "x_pos", "y": "y_pos", "z": "z_pos", "f": "f_pos", "theta": "theta_pos"}
_STATE_REQUEST_KEYS = (
    "filter", "zoom", "laser", "intensity", "shutterconfig", "state",
    "camera_exposure_time", "camera_line_interval",
    "samplerate", "sweeptime", "ETL_cfg_file",
    "etl_l_delay_%", "etl_l_ramp_rising_%", "etl_l_ramp_falling_%",
    "etl_l_amplitude", "etl_l_offset",
    "etl_r_delay_%", "etl_r_ramp_rising_%", "etl_r_ramp_falling_%",
    "etl_r_amplitude", "etl_r_offset",
    "galvo_l_frequency", "galvo_l_amplitude", "galvo_l_offset",
    "galvo_l_duty_cycle", "galvo_l_phase",
    "galvo_r_frequency", "galvo_r_offset", "galvo_r_duty_cycle", "galvo_r_phase",
    "laser_l_delay_%", "laser_l_pulse_%",
    "laser_r_delay_%", "laser_r_pulse_%",
    "camera_delay_%", "camera_pulse_%",
    "camera_display_live_subsampling", "camera_display_acquisition_subsampling",
    "camera_sensor_mode", "camera_binning", "galvo_amp_scale_w_zoom",
)
_MODES = (
    "live", "snap", "run_selected_acquisition", "run_acquisition_list",
    "preview_acquisition_with_z_update", "preview_acquisition_without_z_update",
    "idle", "lightsheet_alignment_mode", "visual_mode",
)
_ACQUISITION_FIELDS = (
    "x_pos", "y_pos", "z_start", "z_end", "z_step", "planes", "rot",
    "f_start", "f_end", "laser", "intensity", "filter", "zoom",
    "shutterconfig", "folder", "filename", "image_writer_plugin",
    "etl_l_offset", "etl_l_amplitude", "etl_r_offset", "etl_r_amplitude",
    "processing",
)
_ACQUISITION_AXIS_FIELDS = {
    "x_pos": "x", "y_pos": "y", "z_start": "z", "z_end": "z",
    "f_start": "f", "f_end": "f", "rot": "theta",
}
_ACQUISITION_STRING_FIELDS = {
    "folder", "filename", "image_writer_plugin", "processing",
}
_ETL_READBACK_KEYS = (
    "ETL_cfg_file", "laser", "zoom",
    "etl_l_delay_%", "etl_l_ramp_rising_%", "etl_l_ramp_falling_%",
    "etl_l_amplitude", "etl_l_offset",
    "etl_r_delay_%", "etl_r_ramp_rising_%", "etl_r_ramp_falling_%",
    "etl_r_amplitude", "etl_r_offset",
)

READ_ONLY_COMMANDS = frozenset({
    "hello", "ping", "get_state", "get_position", "get_state_all",
    "get_config", "get_info", "get_limits", "get_capabilities", "get_progress",
    "get_snap_image", "self_test", "get_acquisition_list", "stat_files",
})
EMERGENCY_COMMANDS = frozenset({
    "stop", "stop_activity", "time_lapse_stop", "close_shutters",
})
FINISHED_SIGNAL_COMMANDS = frozenset({
    "snap", "run_acquisition_list", "run_selected_acquisition",
    "acquire_start", "start_live",
    "start_visual_mode", "start_lightsheet_alignment_mode",
})
_ACTIVE_OPERATION_STATES = frozenset({"processing", "stopping"})
_SNAPSHOT_CHUNK_BYTES = 256 * 1024
_MAX_SNAPSHOT_CHUNK_BYTES = 512 * 1024


class BusyError(RuntimeError):
    """Raised when a mutating call arrives while another operation is active."""


def operation_snapshot(core):
    """Return the public status of the most recent remote operation."""
    operation = getattr(core, "_mesospim_remote_operation", None)
    if not isinstance(operation, dict):
        return {"status": "idle"}
    return {
        key: operation[key]
        for key in (
            "id", "command", "status", "stop_requested", "warnings", "error",
        )
        if key in operation
    }


def _active_operation(core):
    operation = getattr(core, "_mesospim_remote_operation", None)
    if isinstance(operation, dict) and operation.get("status") in _ACTIVE_OPERATION_STATES:
        return operation
    return None


def _begin_operation(core, command, completion=None):
    active = _active_operation(core)
    if active is not None:
        raise BusyError(
            "system busy: currently processing "
            f"{active['command']} (operation {active['id']})")
    counter = int(getattr(core, "_mesospim_remote_operation_counter", 0)) + 1
    core._mesospim_remote_operation_counter = counter
    operation = {
        "id": f"op-{counter:06d}",
        "command": command,
        "status": "processing",
        "_completion": completion,
    }
    core._mesospim_remote_operation = operation
    return operation


def _finish_operation(core, status="completed", error=None):
    operation = getattr(core, "_mesospim_remote_operation", None)
    if not isinstance(operation, dict):
        return False
    operation["status"] = status
    if error is not None:
        operation["error"] = str(error)
    return True


def complete_operation(core, completion):
    """Complete an operation only when its real completion milestone matches."""
    operation = _active_operation(core)
    if operation is None or operation.get("_completion") != completion:
        return False
    return _finish_operation(core)


def fail_operation(core, completion, error):
    """Fail an active operation when its matching deferred action raises."""
    operation = _active_operation(core)
    if operation is None or operation.get("_completion") != completion:
        return False
    return _finish_operation(core, "failed", error)


def capture_snap_image(core):
    """Capture a completed remote snap as bounded, chunk-readable raw pixels.

    This is called only from the camera-frame signal while a remote ``snap`` operation
    is waiting for its image. Live and acquisition frames never enter this store.
    """
    operation = _active_operation(core)
    if operation is None or operation.get("_completion") != "snap_image":
        return False
    try:
        queue = getattr(core, "frame_queue_display", None)
        if not queue:
            raise RuntimeError("snapshot camera frame was signalled but no image is available")
        image = queue[-1]
        dtype = getattr(image, "dtype", None)
        shape = getattr(image, "shape", None)
        tobytes = getattr(image, "tobytes", None)
        if dtype is None or shape is None or not callable(tobytes):
            raise TypeError("snapshot image must expose dtype, shape, and tobytes()")
        if bool(getattr(dtype, "hasobject", False)):
            raise TypeError("object-dtype snapshot images cannot be transferred")
        data = tobytes(order="C")
        if not isinstance(data, bytes) or not data:
            raise ValueError("snapshot image contains no pixel bytes")
        core._mesospim_remote_snapshot = {
            "operation_id": operation["id"],
            "format": "raw",
            "dtype": str(getattr(dtype, "str", dtype)),
            "shape": [int(value) for value in shape],
            "order": "C",
            "total_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "_data": data,
        }
    except Exception as exc:
        return fail_operation(core, "snap_image", exc)
    return complete_operation(core, "snap_image")


def _completion_kind(core, call, args):
    # Recording/unit-test Cores have no completion signals, so their calls remain
    # synchronous.  The real Core advertises these signals and is gated until they fire.
    is_snap = call == "snap" or (call == "set_mode" and args.get("mode") == "snap")
    if is_snap and hasattr(getattr(core, "camera_worker", None), "sig_camera_frame"):
        return "snap_image"
    if call == "preview_acquisition" and hasattr(core, "sig_finished"):
        return "preview_returned_idle"
    if call in FINISHED_SIGNAL_COMMANDS and hasattr(core, "sig_finished"):
        return "finished"
    if call == "set_mode" and args.get("mode") != "idle" and hasattr(core, "sig_finished"):
        return "finished"
    if call == "time_lapse_start" and hasattr(core, "sig_time_lapse_finished"):
        return "time_lapse"
    return None


def _is_emergency(call, args):
    return call in EMERGENCY_COMMANDS or (call == "set_mode" and args.get("mode") == "idle")


def _accepted(result, command, core, operation=None):
    output = dict(result) if isinstance(result, dict) else {"result": result}
    output["accepted"] = True
    output["accepted_command"] = command
    output["operation"] = operation_snapshot(core) if operation is None else {
        key: operation[key]
        for key in ("id", "command", "status", "stop_requested", "warnings", "error")
        if key in operation
    }
    return output


def _unique_object(pairs):
    """Build a JSON object while refusing ambiguous duplicate member names."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _finite_json_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"non-finite JSON number {text!r}")
    return value


def strict_json_loads(payload):
    """Decode strict JSON: unique object keys and finite numeric values only."""
    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant {value!r}")

    return json.loads(
        payload,
        object_pairs_hook=_unique_object,
        parse_float=_finite_json_float,
        parse_constant=reject_constant,
    )


def parse_call(payload):
    msg = strict_json_loads(payload)
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


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    items = getattr(value, "items", None)
    if callable(items):
        return {str(k): _jsonable(v) for k, v in items()}
    return str(value)


def _defer(func, *args, **kwargs):
    from PyQt5 import QtCore
    QtCore.QTimer.singleShot(0, lambda: func(*args, **kwargs))


def _defer_until_returned_idle(core, completion, func, *args, **kwargs):
    """Run a deferred blocking action and close its gate only after a verified idle return."""
    def invoke():
        try:
            func(*args, **kwargs)
            state = _state_get(core, "state")
            if state != "idle":
                raise RuntimeError(
                    f"deferred command returned without reaching idle (state={state!r})")
        except Exception as exc:
            fail_operation(core, completion, exc)
            logger.exception("remote deferred operation failed")
        else:
            complete_operation(core, completion)

    _defer(invoke)


def _state_snapshot(core, keys=None):
    state = getattr(core, "state", None)
    if keys is None:
        raw = getattr(state, "_state_dict", None)
        keys = list(raw.keys()) if isinstance(raw, dict) else ()
    get_parameter_dict = getattr(state, "get_parameter_dict", None)
    if callable(get_parameter_dict):
        return _jsonable(get_parameter_dict(list(keys)))
    return {k: _jsonable(_state_get(core, k)) for k in keys}


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
    move = {k + "_rel": float(v) for k, v in a["deltas"].items()}
    # Core.move_relative(wait_until_done=True) emits a signal rather than performing a
    # synchronous call. From the Core-hosted remote server that can return before the
    # position changes (and can wedge the next request). MainWindow already calls the
    # serial worker directly for this reason; use the same proven path here.
    serial_move = getattr(getattr(core, "serial_worker", None), "move_relative", None)
    if callable(serial_move):
        serial_move(move, wait_until_done=True)
    else:
        core.move_relative(move, wait_until_done=True)
    return {}


def _zero(core, a):
    core.zero_axes(list(a.get("axes") or _AXES))
    return {}


def _stop(core, a):
    core.sig_stop_movement.emit()
    return {}


def _set_state(core, a):
    settings = dict(a["settings"])
    invalid = sorted(set(settings) - set(_STATE_REQUEST_KEYS))
    if invalid:
        raise ValueError(f"unknown state setting(s): {', '.join(invalid)}")
    core.state_request_handler(settings)
    return {}


def _settings_from_args(a, keys):
    settings = {key: a[key] for key in keys if key in a}
    if not settings:
        raise ValueError(f"expected one or more of: {', '.join(keys)}")
    return settings


def _set_filter(core, a):
    core.set_filter(str(a["filter"]), wait_until_done=bool(a.get("wait", False)))
    return {}


def _set_zoom(core, a):
    zoom = str(a["zoom"])
    update_etl = bool(a.get("update_etl", True))
    core.set_zoom(zoom, wait_until_done=bool(a.get("wait", True)),
                  update_etl=update_etl)
    return {}


def _set_laser(core, a):
    core.set_laser(str(a["laser"]), wait_until_done=bool(a.get("wait", False)),
                   update_etl=bool(a.get("update_etl", True)))
    return {}


def _set_intensity(core, a):
    core.set_intensity(a["intensity"], wait_until_done=bool(a.get("wait", False)))
    return {}


def _set_shutterconfig(core, a):
    core.set_shutterconfig(str(a["shutterconfig"]))
    return {}


def _set_camera(core, a):
    keys = ("camera_exposure_time", "camera_line_interval", "camera_delay_%",
            "camera_pulse_%", "camera_display_live_subsampling",
            "camera_display_acquisition_subsampling", "camera_sensor_mode", "camera_binning")
    core.state_request_handler(_settings_from_args(a, keys))
    return {}


def _set_etl(core, a):
    keys = ("etl_l_delay_%", "etl_l_ramp_rising_%", "etl_l_ramp_falling_%",
            "etl_l_amplitude", "etl_l_offset", "etl_r_delay_%",
            "etl_r_ramp_rising_%", "etl_r_ramp_falling_%",
            "etl_r_amplitude", "etl_r_offset")
    core.state_request_handler(_settings_from_args(a, keys))
    return {}


def _set_galvo(core, a):
    keys = ("galvo_l_frequency", "galvo_l_amplitude", "galvo_l_offset",
            "galvo_l_duty_cycle", "galvo_l_phase", "galvo_r_frequency",
            "galvo_r_offset", "galvo_r_duty_cycle", "galvo_r_phase",
            "galvo_amp_scale_w_zoom")
    core.state_request_handler(_settings_from_args(a, keys))
    return {}


def _set_laser_timing(core, a):
    keys = ("laser_l_delay_%", "laser_l_pulse_%",
            "laser_r_delay_%", "laser_r_pulse_%")
    core.state_request_handler(_settings_from_args(a, keys))
    return {}


def _etl_request(core, settings, wait=True):
    if wait:
        core.sig_state_request_and_wait_until_done.emit(settings)
    else:
        core.sig_state_request.emit(settings)
    return _state_snapshot(core, _ETL_READBACK_KEYS)


def _reload_etl_config(core, a):
    cfg_file = a.get("path", _state_get(core, "ETL_cfg_file"))
    if not cfg_file:
        raise ValueError("path is required when ETL_cfg_file is not set")
    return _etl_request(core, {"ETL_cfg_file": str(cfg_file)},
                        wait=bool(a.get("wait", True)))


def _update_etl_from_laser(core, a):
    laser = a.get("laser", _state_get(core, "laser"))
    if not laser:
        raise ValueError("laser is required when state['laser'] is not set")
    return _etl_request(core, {"set_etls_according_to_laser": str(laser)},
                        wait=bool(a.get("wait", True)))


def _update_etl_from_zoom(core, a):
    zoom = a.get("zoom", _state_get(core, "zoom"))
    if not zoom:
        raise ValueError("zoom is required when state['zoom'] is not set")
    return _etl_request(core, {"set_etls_according_to_zoom": str(zoom)},
                        wait=bool(a.get("wait", True)))


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


def _get_state_all(core, a):
    keys = a.get("keys")
    if keys is not None and not isinstance(keys, list):
        raise ValueError("keys must be a list")
    return _state_snapshot(core, keys)


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


def _snapshot_metadata(core):
    snapshot = getattr(core, "_mesospim_remote_snapshot", None)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("_data"), bytes):
        return None
    return {key: value for key, value in snapshot.items() if not key.startswith("_")}


def _get_info(core, a):
    """Return an intentionally extensible, authenticated microscope-info document."""
    cfg = getattr(core, "cfg", None)
    writer = getattr(core, "image_writer", None)
    operation = operation_snapshot(core)
    return {
        "app": "mesoSPIM-control",
        "version": getattr(cfg, "version", None),
        "protocol": 1,
        "state": _state_get(core, "state"),
        "stage_type": _cfg_dict(cfg, "stage_parameters").get("stage_type"),
        "save_path": _state_get(core, "folder"),
        "snap_folder": _state_get(core, "snap_folder"),
        "last_acquisition_path": getattr(writer, "path", None),
        "etl_config_path": _state_get(core, "ETL_cfg_file"),
        "operation": operation,
        "warnings": list(operation.get("warnings", [])),
    }


def _get_limits(core, a):
    cfg = getattr(core, "cfg", None)
    axes = _effective_limits(core)
    return {"stage": _jsonable(getattr(cfg, "stage_parameters", {})),
            "camera": _jsonable(getattr(cfg, "camera_parameters", {})),
            "startup": _jsonable(getattr(cfg, "startup", {})),
            # exactly what _validate enforces, so a script/LLM can read the rules and
            # see where a check is OFF (range null = only the type is checked):
            "enforced": {
                "axes": {ax: (list(axes[ax]) if ax in axes else None) for ax in _AXES},
                "parameters": _param_constraints(core)}}


def _get_capabilities(core, a):
    return {
        "commands": list(COMMANDS),
        "axes": list(_AXES),
        "position_keys": dict(_POSITION_KEYS),
        "settable_state_keys": list(_STATE_REQUEST_KEYS),
        "modes": list(_MODES),
        "acquisition_fields": list(_ACQUISITION_FIELDS),
    }


def _self_test(core, a):
    # Re-run the startup self-test on demand (over TCP or MCP): prove -- against a mock
    # Core carrying THIS server's real cfg -- that the loaded limits are still enforced.
    # Never touches the real hardware; useful for an LLM/script to confirm before driving.
    ok, report = self_test(getattr(core, "cfg", None))
    return {"ok": ok, "report": report}


def _get_progress(core, a):
    return {"state": _state_get(core, "state"),
            "current_plane": _state_get(core, "current_framenumber"),
            "total_planes": _state_get(core, "snap_count"),
            "current_acquisition": _state_get(core, "current_acquisition"),
            "total_acquisitions": _state_get(core, "total_acquisitions"),
            "operation": operation_snapshot(core)}


def _get_snap_image(core, a):
    snapshot = getattr(core, "_mesospim_remote_snapshot", None)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("_data"), bytes):
        raise RuntimeError("no remote snapshot is available; call snap and poll completion first")
    requested_id = a.get("operation_id")
    if requested_id is not None and requested_id != snapshot["operation_id"]:
        raise ValueError(
            f"snapshot {requested_id!r} is unavailable; latest is {snapshot['operation_id']!r}")
    offset = int(a.get("offset", 0))
    chunk_size = int(a.get("max_bytes", _SNAPSHOT_CHUNK_BYTES))
    data = snapshot["_data"]
    if offset > len(data):
        raise ValueError(f"offset {offset} exceeds snapshot size {len(data)}")
    end = min(offset + chunk_size, len(data))
    out = _snapshot_metadata(core)
    out.update({
        "encoding": "base64",
        "offset": offset,
        "chunk_bytes": end - offset,
        "next_offset": end if end < len(data) else None,
        "complete": end == len(data),
        "data": base64.b64encode(data[offset:end]).decode("ascii"),
    })
    return out


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
        core._mesospim_prev_acq_list = (True, st["acq_list"])
    except (KeyError, TypeError):
        core._mesospim_prev_acq_list = (False, None)
    st["acq_list"] = AcquisitionList([obj])
    _defer(core.start, row=0)
    fname = acq.get("filename") or ""
    return {"started": True, "scheduled": True,
            "files": [os.path.join(acq.get("folder") or "", fname)] if fname else [],
            "planes": int(acq.get("planes", 1) or 1),
            "pixels": list(_camera_pixels(getattr(core, "cfg", None)))}


def _make_acquisition_list(acquisitions):
    try:
        from .utils.acquisitions import Acquisition, AcquisitionList
    except ImportError:
        from utils.acquisitions import Acquisition, AcquisitionList
    out = AcquisitionList([])
    for raw in acquisitions:
        obj = Acquisition()
        obj.update({k: v for k, v in dict(raw).items() if v is not None})
        out.append(obj)
    return out


def _get_acquisition_list(core, a):
    return {"acquisitions": _jsonable(_state_get(core, "acq_list", []))}


def _set_acquisition_list(core, a):
    acquisitions = a.get("acquisitions")
    if not isinstance(acquisitions, list):
        raise ValueError("acquisitions must be a list")
    core.state["acq_list"] = _make_acquisition_list(acquisitions)
    row = a.get("selected_row")
    if row is not None:
        core.state["selected_row"] = int(row)
    return {"count": len(core.state["acq_list"])}


def _run_acquisition_list(core, a):
    _defer(core.start, row=None)
    return {"scheduled": True}


def _run_selected_acquisition(core, a):
    row = int(a.get("row", _state_get(core, "selected_row", 0)))
    core.state["selected_row"] = row
    _defer(core.start, row=row)
    return {"scheduled": True, "row": row}


def _preview_acquisition(core, a):
    row = int(a.get("row", _state_get(core, "selected_row", 0)))
    core.state["selected_row"] = row
    _defer_until_returned_idle(
        core, "preview_returned_idle", core.preview_acquisition,
        z_update=bool(a.get("z_update", True)))
    return {"scheduled": True, "row": row}


def _stat_files(core, a):
    files = [str(f) for f in (a.get("files") or [])]
    return {"missing": [f for f in files if not os.path.isfile(f)],
            "sizes": {f: os.path.getsize(f) for f in files if os.path.isfile(f)}}


def _get_disk_space(core, a):
    acq_list = _make_acquisition_list(a["acquisitions"]) if "acquisitions" in a else _state_get(core, "acq_list")
    return {"free_bytes": int(core.get_free_disk_space(acq_list)),
            "required_bytes": int(core.get_required_disk_space(acq_list))}


def _check_motion_limits(core, a):
    acq_list = _make_acquisition_list(a["acquisitions"]) if "acquisitions" in a else _state_get(core, "acq_list")
    return {"outside_limits": list(core.check_motion_limits(acq_list))}


def _acquire_finish(core, a):
    st = core.state
    had, prev = getattr(core, "_mesospim_prev_acq_list", (False, None))
    if had:
        st["acq_list"] = prev
    else:
        try:
            del st["acq_list"]
        except Exception:
            st["acq_list"] = prev
    try:
        del core._mesospim_prev_acq_list
    except AttributeError:
        pass
    return {"state": _state_get(core, "state")}


def _procedure(core, a):
    raise RuntimeError(f"procedure {a.get('name')!r} is not implemented server-side")


def _unzero(core, a):
    core.unzero_axes(list(a.get("axes") or _AXES))
    return {}


def _open_shutters(core, a):
    core.open_shutters()
    return {"shutterstate": _state_get(core, "shutterstate")}


def _close_shutters(core, a):
    core.close_shutters()
    return {"shutterstate": _state_get(core, "shutterstate")}


def _stop_activity(core, a):
    # Re-broadcasting camera/writer abort while already idle can double-trigger
    # native teardown. An idempotent stop must be a real no-op in that state.
    if _state_get(core, "state") != "idle":
        core.stop()
    return {"state": _state_get(core, "state")}


def _snap(core, a):
    # Remote snapshots are transferred through get_snap_image. Never emit the
    # MainWindow's local save/prefix dialog (Camera emits it only for write_flag=True).
    core._mesospim_remote_snapshot = None
    _defer(core.snap, write_flag=False,
           laser_blanking=bool(a.get("laser_blanking", True)))
    return {"scheduled": True, "image_stream": "get_snap_image"}


def _set_mode(core, a):
    mode = a["mode"]
    if mode not in _MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "idle":
        if _state_get(core, "state") != "idle":
            core.stop()
        return {"mode": mode}
    if mode == "snap":
        return _snap(core, {})
    _defer(core.set_state, mode)
    return {"scheduled": True, "mode": mode}


def _start_live(core, a):
    return _set_mode(core, {"mode": "live"})


def _start_visual_mode(core, a):
    return _set_mode(core, {"mode": "visual_mode"})


def _start_lightsheet_alignment_mode(core, a):
    return _set_mode(core, {"mode": "lightsheet_alignment_mode"})


def _load_sample(core, a):
    params = _cfg_dict(getattr(core, "cfg", None), "stage_parameters")
    if "y_load_position" not in params:
        raise ValueError("stage configuration has no y_load_position")
    core.move_absolute(
        {"y_abs": float(params["y_load_position"])}, wait_until_done=True,
        use_internal_position=False)
    return {}


def _unload_sample(core, a):
    params = _cfg_dict(getattr(core, "cfg", None), "stage_parameters")
    if "y_unload_position" not in params:
        raise ValueError("stage configuration has no y_unload_position")
    core.move_absolute(
        {"y_abs": float(params["y_unload_position"])}, wait_until_done=True,
        use_internal_position=False)
    return {}


def _center_sample(core, a):
    params = _cfg_dict(getattr(core, "cfg", None), "stage_parameters")
    targets = {
        axis + "_abs": float(params[key])
        for axis, key in (("x", "x_center_position"), ("z", "z_center_position"))
        if key in params
    }
    if not targets:
        raise ValueError("stage configuration has no x_center_position or z_center_position")
    core.move_absolute(
        targets, wait_until_done=True, use_internal_position=False)
    return {}


def _execute_stage_program(core, a):
    core.execute_galil_program()
    return {}


def _save_etl_config(core, a):
    core.sig_save_etl_config.emit()
    return {}


def _time_lapse_start(core, a):
    core.run_time_lapse(tpoints=int(a.get("timepoints", 1)),
                        time_interval_sec=int(a.get("interval_sec", 0)))
    return {"started": True}


def _time_lapse_stop(core, a):
    core.stop_time_lapse()
    return {"stopped": True}


COMMANDS = {
    "hello": _hello, "ping": _ping,
    "get_state": _get_state, "get_position": _get_position,
    "get_state_all": _get_state_all, "get_config": _get_config, "get_info": _get_info,
    "get_limits": _get_limits,
    "get_capabilities": _get_capabilities, "get_progress": _get_progress,
    "get_snap_image": _get_snap_image,
    "self_test": _self_test,
    "move_absolute": _move_absolute, "move_relative": _move_relative,
    "zero": _zero, "unzero": _unzero,
    "stop": _stop, "stop_activity": _stop_activity, "set_state": _set_state,
    "set_filter": _set_filter, "set_zoom": _set_zoom, "set_laser": _set_laser,
    "set_intensity": _set_intensity, "set_shutterconfig": _set_shutterconfig,
    "set_camera": _set_camera, "set_etl": _set_etl, "set_galvo": _set_galvo,
    "set_laser_timing": _set_laser_timing,
    "reload_etl_config": _reload_etl_config,
    "update_etl_from_laser": _update_etl_from_laser,
    "update_etl_from_zoom": _update_etl_from_zoom,
    "open_shutters": _open_shutters, "close_shutters": _close_shutters,
    "snap": _snap, "set_mode": _set_mode, "start_live": _start_live,
    "start_visual_mode": _start_visual_mode,
    "start_lightsheet_alignment_mode": _start_lightsheet_alignment_mode,
    "load_sample": _load_sample, "unload_sample": _unload_sample,
    "center_sample": _center_sample, "execute_stage_program": _execute_stage_program,
    "save_etl_config": _save_etl_config,
    "get_acquisition_list": _get_acquisition_list, "set_acquisition_list": _set_acquisition_list,
    "run_acquisition_list": _run_acquisition_list, "run_selected_acquisition": _run_selected_acquisition,
    "preview_acquisition": _preview_acquisition,
    "acquire_start": _acquire_start, "stat_files": _stat_files, "acquire_finish": _acquire_finish,
    "get_disk_space": _get_disk_space, "check_motion_limits": _check_motion_limits,
    "time_lapse_start": _time_lapse_start, "time_lapse_stop": _time_lapse_stop,
    "procedure": _procedure,
}

_HINTS = {
    "get_info": "report extensible microscope info including save paths and latest snapshot metadata.",
    "get_snap_image": "read the latest remote snapshot in bounded base64 chunks. args: "
                      "{operation_id?: str, offset?: int, max_bytes?: 1..524288}",
    "self_test": "verify (against a mock Core with this server's real cfg) that the loaded "
                 "limits are enforced. args: none. returns {ok, report}. Never moves hardware.",
    "move_absolute": "move axes to absolute targets. args: {targets: {x,y,z,f,theta: um/deg}}",
    "move_relative": "move axes by relative offsets. args: {deltas: {axis: um/deg}}",
    "zero": "define the current position as zero. args: {axes: [..]} (omit = all)",
    "unzero": "restore physical coordinates for axes. args: {axes: [..]} (omit = all)",
    "set_state": "change settings. args: {settings: {one or more settable_state_keys}}",
    "set_camera": "change camera timing/display settings. args: one or more camera_* keys",
    "set_etl": "change ETL timing/amplitude/offset settings. args: one or more etl_* keys",
    "set_galvo": "change galvo waveform settings. args: one or more galvo_* keys",
    "set_laser_timing": "change laser waveform timing/amplitude settings.",
    "reload_etl_config": "reload ETL values from a CSV. args: {path?: str, wait?: bool}",
    "update_etl_from_laser": "load ETL values for a laser. args: {laser?: str, wait?: bool}",
    "update_etl_from_zoom": "load ETL values for a zoom. args: {zoom?: str, wait?: bool}",
    "set_mode": "run a named mesoSPIM mode. args: {mode: live|snap|run_acquisition_list|...}",
    "snap": "capture one remote snapshot without GUI dialogs; poll get_progress, then "
            "read get_snap_image chunks. args: {laser_blanking?: bool, write?: false}",
    "acquire_start": "start one acquisition. args: {acquisition: {...}}",
    "set_acquisition_list": "replace the acquisition list. args: {acquisitions: [{...}], selected_row?: int}",
    "stat_files": "report which output files exist and their sizes. args: {files: [path,..]}",
    "procedure": "run a named site procedure. args: {name: <procedure>}",
}


def _num(v):
    """True for finite real numbers (JSON booleans are ints -- exclude them)."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _cfg_options(core):
    """The options the live cfg allows, so we accept exactly what this scope has."""
    cfg = getattr(core, "cfg", None)
    return {"filter": list(_cfg_dict(cfg, "filterdict")),
            "zoom": list(_cfg_dict(cfg, "zoomdict")),
            "laser": list(_cfg_dict(cfg, "laserdict")),
            "shutterconfig": list(getattr(cfg, "shutteroptions", []) or [])}


# Settable parameters that are text, not numbers (filter/zoom/laser/shutterconfig are
# handled as cfg-driven enums below, so they are not repeated here).
_STRING_KEYS = ("state", "ETL_cfg_file", "camera_sensor_mode", "camera_binning")
# Settable parameters that are a percentage: a number the instrument reads as 0..100.
_PERCENT_KEYS = tuple(k for k in _STATE_REQUEST_KEYS if k.endswith("%")) + (
    "intensity", "galvo_l_duty_cycle", "galvo_r_duty_cycle")


def _param_constraints(core):
    """The type + allowed values of every settable parameter, in one place, so the SAME
    rules that ``_validate`` enforces can be read back by a script/LLM (``get_limits``).
    Each entry is ``{"type": "number"|"string", "options": [...] or None, "range":
    [lo, hi] or None}``. ``range`` is ``None`` when no numeric bound is enforced -- the
    range check is OFF for that key (only its type is checked), which the caller can see.
    """
    opts = _cfg_options(core)  # cfg-driven enums for this loaded config
    spec = {}
    for k in _STATE_REQUEST_KEYS:
        if k in opts:
            spec[k] = {"type": "string", "options": opts[k], "range": None}
        elif k == "state":
            spec[k] = {"type": "string", "options": list(_MODES), "range": None}
        elif k in _PERCENT_KEYS:
            spec[k] = {"type": "number", "options": None, "range": [0, 100]}
        elif k in _STRING_KEYS:
            spec[k] = {"type": "string", "options": None, "range": None}
        else:
            spec[k] = {"type": "number", "options": None, "range": None}
    return spec


def _validate(core, call, args, limits):
    """Reject a call whose ARGS are the wrong shape, not an allowed option, or out of
    range -- with a clear message -- BEFORE the handler runs, so a bad value never
    reaches the instrument. Allowed options come from the live ``cfg``; axis ranges
    come from ``limits`` (per-axis ``{axis: (lo, hi)}``) -- by default the travel
    envelope of the config the operator loaded at startup (``cfg.stage_parameters``),
    optionally tightened per axis by ``MESOSPIM_RS_LIMITS``. No limit for an axis =
    the Core's own hardware bound is the backstop.
    """
    def bad(msg):
        raise ValueError(f"{call}: {msg}")

    spec = _param_constraints(core)

    def check(key, value):
        """Type + range + option check for one settable parameter, from the shared spec.
        A key with no spec entry is not our contract -- leave it to the handler."""
        s = spec.get(key)
        if s is None:
            return
        if s["type"] == "number":
            if not _num(value):
                bad(f"{key} must be a number, got {value!r}")
            rng = s["range"]
            if rng and not (rng[0] <= value <= rng[1]):
                bad(f"{key}={value} is outside the allowed range {tuple(rng)}")
        elif s["type"] == "string" and not isinstance(value, str):
            bad(f"{key} must be a string, got {value!r}")
        if s["options"] and value not in s["options"]:
            bad(f"{key}={value!r} is not one of {s['options']}")

    def check_acquisition(acquisition, label):
        if not isinstance(acquisition, dict):
            bad(f"{label} must be an object")
        unknown = sorted(set(acquisition) - set(_ACQUISITION_FIELDS))
        if unknown:
            bad(f"{label} has unknown field(s): {', '.join(unknown)}")
        for key, value in acquisition.items():
            check(key, value)
        for field, axis in _ACQUISITION_AXIS_FIELDS.items():
            if field not in acquisition:
                continue
            value = acquisition[field]
            if not _num(value):
                bad(f"{label}.{field} must be a number, got {value!r}")
            lo_hi = limits.get(axis)
            if lo_hi and not (lo_hi[0] <= value <= lo_hi[1]):
                bad(
                    f"{label}.{field}={value} is outside the allowed "
                    f"{axis} range {tuple(lo_hi)}")
        if "z_step" in acquisition:
            value = acquisition["z_step"]
            if not _num(value) or value <= 0:
                bad(f"{label}.z_step must be a positive number, got {value!r}")
        if "planes" in acquisition:
            value = acquisition["planes"]
            if not _num(value) or value < 1 or int(value) != value:
                bad(f"{label}.planes must be a positive integer, got {value!r}")
        for field in _ACQUISITION_STRING_FIELDS:
            if field in acquisition and not isinstance(acquisition[field], str):
                bad(f"{label}.{field} must be a string, got {acquisition[field]!r}")

    def check_row(value, count, key="row"):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            bad(f"'{key}' must be a non-negative integer")
        # An empty list plus row 0 is the existing clear-list contract. Otherwise
        # require a real list index so deferred Core calls cannot fail later.
        if (count == 0 and value != 0) or (count > 0 and value >= count):
            bad(f"'{key}'={value} is outside the acquisition-list range 0..{max(0, count - 1)}")

    if call in ("move_absolute", "move_relative"):
        key = "targets" if call == "move_absolute" else "deltas"
        moves = args.get(key)
        if not isinstance(moves, dict) or not moves:
            bad(f"'{key}' must be a non-empty object of axis -> number")
        for axis, value in moves.items():
            if axis not in _AXES:
                bad(f"unknown axis {axis!r}; allowed: {list(_AXES)}")
            if not _num(value):
                bad(f"axis {axis!r} value must be a number, got {value!r}")
            lo_hi = limits.get(axis)
            if call == "move_absolute" and lo_hi and not (lo_hi[0] <= value <= lo_hi[1]):
                bad(f"{axis}={value} is outside the allowed range {tuple(lo_hi)}")
            if call == "move_relative" and lo_hi:
                current = _state_position(core).get(axis)
                if not _num(current):
                    bad(f"cannot verify relative move for {axis!r}: current position unavailable")
                destination = current + value
                if not _num(destination) or not (lo_hi[0] <= destination <= lo_hi[1]):
                    bad(f"{axis} destination {destination} is outside the allowed range {tuple(lo_hi)}")
    elif call in ("zero", "unzero"):
        axes = args.get("axes")
        if axes is not None and (not isinstance(axes, list) or any(a not in _AXES for a in axes)):
            bad(f"'axes' must be a list of {list(_AXES)}")
    elif call in ("set_filter", "set_zoom", "set_laser", "set_shutterconfig"):
        field = call[len("set_"):]
        if field not in args:
            bad(f"'{field}' is required")
        check(field, args[field])
    elif call == "set_intensity":
        if "intensity" not in args:
            bad("'intensity' is required")
        check("intensity", args["intensity"])
    elif call == "set_state":
        settings = args.get("settings")
        if not isinstance(settings, dict) or not settings:
            bad("'settings' must be a non-empty object")
        for k, v in settings.items():
            check(k, v)
        if settings.get("state") == "snap":
            bad("state='snap' is GUI-oriented; use the snap command for remote pixels")
    elif call in ("set_camera", "set_etl", "set_galvo", "set_laser_timing"):
        for k, v in args.items():  # args ARE the settings for these calls
            check(k, v)
    elif call == "acquire_start":
        check_acquisition(args.get("acquisition"), "acquisition")
    elif call == "set_acquisition_list":
        acquisitions = args.get("acquisitions")
        if not isinstance(acquisitions, list):
            bad("'acquisitions' must be a list")
        for index, acquisition in enumerate(acquisitions):
            check_acquisition(acquisition, f"acquisitions[{index}]")
        if "selected_row" in args:
            check_row(args["selected_row"], len(acquisitions), "selected_row")
    elif call in ("run_selected_acquisition", "preview_acquisition"):
        acquisitions = _state_get(core, "acq_list", []) or []
        try:
            count = len(acquisitions)
        except TypeError:
            count = 0
        check_row(args.get("row", _state_get(core, "selected_row", 0)), count)
        if call == "preview_acquisition" and "z_update" in args and not isinstance(
                args["z_update"], bool):
            bad("'z_update' must be a boolean")
    elif call == "time_lapse_start":
        timepoints = args.get("timepoints", 1)
        interval = args.get("interval_sec", 0)
        if not isinstance(timepoints, int) or isinstance(timepoints, bool) or timepoints < 1:
            bad("'timepoints' must be a positive integer")
        if not _num(interval) or interval < 0:
            bad("'interval_sec' must be a non-negative number")
    elif call == "snap":
        unknown = sorted(set(args) - {"write", "laser_blanking"})
        if unknown:
            bad(f"unknown argument(s): {', '.join(unknown)}")
        if "write" in args and not isinstance(args["write"], bool):
            bad("'write' must be a boolean")
        if args.get("write") is True:
            bad("'write=true' is GUI-only; remote snapshots are returned by get_snap_image")
        if "laser_blanking" in args and not isinstance(args["laser_blanking"], bool):
            bad("'laser_blanking' must be a boolean")
    elif call == "get_snap_image":
        unknown = sorted(set(args) - {"operation_id", "offset", "max_bytes"})
        if unknown:
            bad(f"unknown argument(s): {', '.join(unknown)}")
        operation_id = args.get("operation_id")
        if operation_id is not None and not isinstance(operation_id, str):
            bad("'operation_id' must be a string")
        offset = args.get("offset", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            bad("'offset' must be a non-negative integer")
        max_bytes = args.get("max_bytes", _SNAPSHOT_CHUNK_BYTES)
        if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
                or not 1 <= max_bytes <= _MAX_SNAPSHOT_CHUNK_BYTES):
            bad(f"'max_bytes' must be an integer in 1..{_MAX_SNAPSHOT_CHUNK_BYTES}")
    # reads / stop / hello / ping / stat_files / procedure: no arg contract


def _limits_from_env():
    """Optional per-axis soft travel limits so ``move_absolute`` refuses out-of-range
    targets. Read from ``MESOSPIM_RS_LIMITS`` -- a JSON object ``{"x": [lo, hi], ...}``
    or a path to a file holding one. Unset or invalid -> no soft limit (the Core's own
    hardware limit is the backstop).
    """
    raw = os.environ.get("MESOSPIM_RS_LIMITS", "").strip()
    if not raw:
        return {}
    try:
        if os.path.isfile(raw):
            with open(raw, encoding="utf-8") as f:
                raw = f.read()
        return {axis: (float(lo), float(hi))
                for axis, (lo, hi) in strict_json_loads(raw).items()}
    except Exception:
        logger.warning("MESOSPIM_RS_LIMITS ignored (want JSON of axis -> [lo, hi])")
        return {}


_LIMITS = _limits_from_env()


def _limits_from_cfg(core):
    """Per-axis soft travel limits from the config the operator loaded at startup.
    mesoSPIM keeps them in ``cfg.stage_parameters`` as ``{axis}_min`` / ``{axis}_max``
    (e.g. ``x_min`` / ``x_max``); we turn each pair into ``{axis: (lo, hi)}``. This is
    why range validation just works out of the box -- if the operator picked the config
    for this scope, its real travel envelope is already the limit, no extra setup.
    """
    sp = _cfg_dict(getattr(core, "cfg", None), "stage_parameters")
    out = {}
    for axis in _AXES:
        lo, hi = sp.get(f"{axis}_min"), sp.get(f"{axis}_max")
        if _num(lo) and _num(hi):
            out[axis] = (float(lo), float(hi))
    return out


def _effective_limits(core):
    """The limits actually enforced: the loaded config's travel envelope, with any
    ``MESOSPIM_RS_LIMITS`` axis overriding it (so an operator can set a tighter soft
    limit than the hardware allows). Env-only axes are honoured too.
    """
    limits = _limits_from_cfg(core)
    limits.update(_LIMITS)
    return limits


def run(core, call, args=None):
    handler = COMMANDS.get(call)
    if handler is None:
        raise KeyError(f"unknown command {call!r}; not in the allowlist")
    args = args or {}
    _validate(core, call, args, _effective_limits(core))
    if call in READ_ONLY_COMMANDS:
        return handler(core, args)

    active = _active_operation(core)
    if _is_emergency(call, args) and active is not None:
        if call in {"stop", "stop_activity", "time_lapse_stop"} or (
                call == "set_mode" and args.get("mode") == "idle"):
            active["status"] = "stopping"
            active["stop_requested"] = True
        result = handler(core, args)
        return _accepted(result, call, core, active)

    completion = _completion_kind(core, call, args)
    operation = _begin_operation(core, call, completion)
    try:
        result = handler(core, args)
    except Exception as exc:
        _finish_operation(core, "failed", exc)
        raise
    if completion is None:
        _finish_operation(core)
    return _accepted(result, call, core, operation)


class SimCore:
    """A stand-in Core that carries the REAL ``cfg`` (so the REAL limits) but SIMULATES the
    hardware: a move just updates an in-memory position -- nothing physical happens. The
    startup self-test drives this instead of the instrument, so it can prove -- on THIS
    machine, with THIS loaded config -- that a good call is accepted and an out-of-limit one
    is refused, before the real server ever goes live.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.state = {"state": "idle", "position": {a + "_pos": 0.0 for a in _AXES}}
        self.moves = []  # every move that reached the (mock) hardware -- rejected ones never do

    def move_absolute(self, sdict, wait_until_done=False, **_):
        self.moves.append(sdict)
        for key, val in sdict.items():
            self.state["position"][key.replace("_abs", "") + "_pos"] = float(val)

    def set_intensity(self, value, wait_until_done=False, **_):
        self.state["intensity"] = value

    def set_filter(self, value, wait_until_done=False, **_):
        self.state["filter"] = value


def self_test(cfg):
    """Pre-flight: prove the loaded config's limits are actually ENFORCED before going live.

    Runs a battery through the SAME ``run()`` dispatch both transports use -- so this one
    check covers the TCP and the MCP lane alike -- against a :class:`SimCore` that mimics the
    hardware. A valid move must be accepted (and reach the mock stage); an out-of-limit move,
    a bad option, and an unknown command must all be refused (and NOT reach the mock stage).
    This is the guard against a drifted limits file or a validation quirk. Returns
    ``(ok, report_lines)`` and never touches the real instrument.
    """
    sim = SimCore(cfg)
    report, ok = [], True

    def note(good, line):
        nonlocal ok
        ok = ok and good
        report.append(("PASS " if good else "FAIL ") + line)

    def accepts(call, args):
        try:
            run(sim, call, args)
            return True
        except Exception:
            return False

    note(accepts("get_limits", {}), "get_limits responds")
    note(accepts("get_position", {}), "get_position responds")

    limits = _effective_limits(sim)
    if not limits:
        note(False, "no axis has a limit -- cannot verify enforcement (check the config's "
                    "stage_parameters); refusing to go live")
        return ok, report

    sim.moves.clear()
    expected_moves = 0
    for axis, (lo, hi) in limits.items():
        note(accepts("move_absolute", {"targets": {axis: hi}}), f"in-range move {axis}={hi} accepted")
        expected_moves += 1
        note(not accepts("move_absolute", {"targets": {axis: hi + 1}}), f"over-max move {axis}={hi + 1} refused")
        note(not accepts("move_absolute", {"targets": {axis: lo - 1}}), f"under-min move {axis}={lo - 1} refused")
    note(not accepts("set_intensity", {"intensity": 250}), "over-range intensity=250 refused")
    note(not accepts("move_absolute", {"targets": {"nope": 0}}), "unknown axis refused")
    note(not accepts("__import__", {}), "unknown command refused")
    # the clincher: only the in-range moves reached the mock hardware; nothing rejected leaked.
    note(len(sim.moves) == expected_moves,
         f"only the {expected_moves} in-range move(s) reached the mock hardware ({len(sim.moves)} recorded)")
    return ok, report
