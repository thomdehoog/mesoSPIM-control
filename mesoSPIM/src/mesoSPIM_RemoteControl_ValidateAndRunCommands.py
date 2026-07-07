"""Remote-control command vocabulary and execution.

Network transports hand this module a decoded command name plus arguments. This
module validates the name against a fixed allowlist and executes the matching
mesoSPIM Core action.
"""

import json
import os

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
_ETL_READBACK_KEYS = (
    "ETL_cfg_file", "laser", "zoom",
    "etl_l_delay_%", "etl_l_ramp_rising_%", "etl_l_ramp_falling_%",
    "etl_l_amplitude", "etl_l_offset",
    "etl_r_delay_%", "etl_r_ramp_rising_%", "etl_r_ramp_falling_%",
    "etl_r_amplitude", "etl_r_offset",
)


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
    core.move_relative({k + "_rel": float(v) for k, v in a["deltas"].items()}, wait_until_done=True)
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
    core.set_zoom(str(a["zoom"]), wait_until_done=bool(a.get("wait", True)),
                  update_etl=bool(a.get("update_etl", True)))
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


def _get_limits(core, a):
    cfg = getattr(core, "cfg", None)
    return {"stage": _jsonable(getattr(cfg, "stage_parameters", {})),
            "camera": _jsonable(getattr(cfg, "camera_parameters", {})),
            "startup": _jsonable(getattr(cfg, "startup", {}))}


def _get_capabilities(core, a):
    return {
        "commands": list(COMMANDS),
        "axes": list(_AXES),
        "position_keys": dict(_POSITION_KEYS),
        "settable_state_keys": list(_STATE_REQUEST_KEYS),
        "modes": list(_MODES),
        "acquisition_fields": list(_ACQUISITION_FIELDS),
    }


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
    _defer(core.preview_acquisition, z_update=bool(a.get("z_update", True)))
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
    core.stop()
    return {"state": _state_get(core, "state")}


def _snap(core, a):
    _defer(core.snap, write_flag=bool(a.get("write", True)),
           laser_blanking=bool(a.get("laser_blanking", True)))
    return {"scheduled": True}


def _set_mode(core, a):
    mode = a["mode"]
    if mode not in _MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "idle":
        core.stop()
        return {"mode": mode}
    _defer(core.set_state, mode)
    return {"scheduled": True, "mode": mode}


def _start_live(core, a):
    return _set_mode(core, {"mode": "live"})


def _start_visual_mode(core, a):
    return _set_mode(core, {"mode": "visual_mode"})


def _start_lightsheet_alignment_mode(core, a):
    return _set_mode(core, {"mode": "lightsheet_alignment_mode"})


def _load_sample(core, a):
    core.sig_load_sample.emit()
    return {}


def _unload_sample(core, a):
    core.sig_unload_sample.emit()
    return {}


def _center_sample(core, a):
    core.sig_center_sample.emit()
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
    "get_state_all": _get_state_all, "get_config": _get_config, "get_limits": _get_limits,
    "get_capabilities": _get_capabilities, "get_progress": _get_progress,
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
    "acquire_start": "start one acquisition. args: {acquisition: {...}}",
    "set_acquisition_list": "replace the acquisition list. args: {acquisitions: [{...}], selected_row?: int}",
    "stat_files": "report which output files exist and their sizes. args: {files: [path,..]}",
    "procedure": "run a named site procedure. args: {name: <procedure>}",
}


def run(core, call, args=None):
    handler = COMMANDS.get(call)
    if handler is None:
        raise KeyError(f"unknown command {call!r}; not in the allowlist")
    return handler(core, args or {})
