"""Validation tests for the Remote Control command module.

Qt-free and no live Core: exercises ``_validate`` (the arg gate that runs at the
single ``run()`` choke point, so both the TCP and MCP lanes enforce it) against a
tiny fake ``cfg``, plus the ``MESOSPIM_RS_LIMITS`` env parser. Run either way::

    pytest mesoSPIM/src/test_remote_control_validation.py
    python  mesoSPIM/src/test_remote_control_validation.py
"""
import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import types

_MOD = Path(__file__).with_name("mesoSPIM_RemoteControl_ValidateAndRunCommands.py")
_spec = importlib.util.spec_from_file_location("_rc_vrc", _MOD)
vrc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vrc)


class _Cfg:
    filterdict = {"Empty": 0, "515LP": 1}
    zoomdict = {"1x": 1, "2x": 2}
    laserdict = {"488 nm": 0, "561 nm": 1}
    shutteroptions = ["Left", "Right", "Both"]
    # the per-axis travel envelope mesoSPIM loads at startup (theta omitted on purpose,
    # so the tests can see an axis whose range check is OFF)
    stage_parameters = {"x_min": -25000, "x_max": 25000, "y_min": -50000, "y_max": 50000,
                        "z_min": -25000, "z_max": 25000, "f_min": 0, "f_max": 98000}


class _Core:
    cfg = _Cfg()


_core = _Core()
_LIMITS = {"x": (-1000.0, 1000.0), "y": (-1000.0, 1000.0), "z": (-1000.0, 1000.0)}


def _rejects(call, args):
    try:
        vrc._validate(_core, call, args, _LIMITS)
    except ValueError:
        return True
    return False


def test_valid_calls_pass():
    vrc._validate(_core, "move_absolute", {"targets": {"x": 100, "y": -50}}, _LIMITS)
    vrc._validate(_core, "set_filter", {"filter": "Empty"}, _LIMITS)
    vrc._validate(_core, "set_zoom", {"zoom": "2x"}, _LIMITS)
    vrc._validate(_core, "set_intensity", {"intensity": 50}, _LIMITS)
    vrc._validate(_core, "get_state", {}, _LIMITS)  # no arg contract -> passes


def test_out_of_range_move_rejected():
    assert _rejects("move_absolute", {"targets": {"x": 999999}})


def test_unknown_axis_rejected():
    assert _rejects("move_absolute", {"targets": {"q": 1}})


def test_non_number_move_rejected():
    assert _rejects("move_absolute", {"targets": {"x": "far"}})


def test_empty_targets_rejected():
    assert _rejects("move_absolute", {"targets": {}})


def test_bad_filter_option_rejected():
    assert _rejects("set_filter", {"filter": "NOPE"})


def test_bad_zoom_option_rejected():
    assert _rejects("set_zoom", {"zoom": "99x"})


def test_bad_shutter_option_rejected():
    assert _rejects("set_shutterconfig", {"shutterconfig": "Sideways"})


def test_bad_intensity_rejected():
    assert _rejects("set_intensity", {"intensity": 250})


def test_bad_set_state_option_rejected():
    assert _rejects("set_state", {"settings": {"zoom": "77x"}})


def test_bad_set_state_mode_rejected():
    assert _rejects(
        "set_state", {"settings": {"state": "__invalid_remote_state__"}})


def test_limits_from_env_parses_json():
    os.environ["MESOSPIM_RS_LIMITS"] = '{"x": [-5, 5], "y": [-5, 5]}'
    try:
        assert vrc._limits_from_env() == {"x": (-5.0, 5.0), "y": (-5.0, 5.0)}
    finally:
        del os.environ["MESOSPIM_RS_LIMITS"]


# -- limits come from the loaded config (cfg.stage_parameters), no env var needed -----

def test_limits_default_to_cfg_stage_parameters():
    lim = vrc._effective_limits(_core)
    assert lim["x"] == (-25000.0, 25000.0) and lim["f"] == (0.0, 98000.0)
    assert "theta" not in lim  # no theta_min/max in this cfg -> range OFF for theta


def test_run_enforces_cfg_range_without_env():
    # run() derives limits from cfg, so an out-of-envelope move is refused end-to-end.
    try:
        vrc.run(_core, "move_absolute", {"targets": {"x": 999999}})
    except ValueError:
        return
    raise AssertionError("run() did not enforce the cfg stage range")


def test_env_override_tightens_cfg_range():
    saved = vrc._LIMITS
    vrc._LIMITS = {"x": (-10.0, 10.0)}  # what MESOSPIM_RS_LIMITS would parse to
    try:
        assert vrc._effective_limits(_core)["x"] == (-10.0, 10.0)        # env wins for x
        assert vrc._effective_limits(_core)["y"] == (-50000.0, 50000.0)  # cfg for the rest
    finally:
        vrc._LIMITS = saved


# -- types + ranges are checked for EVERY settable parameter, not just the stage -------

def test_numeric_setting_type_is_checked():
    assert _rejects("set_etl", {"etl_l_amplitude": "loud"})       # must be a number
    assert _rejects("set_camera", {"camera_exposure_time": "x"})  # must be a number


def test_percent_setting_range_is_checked():
    assert _rejects("set_etl", {"etl_l_delay_%": 250})            # 0..100
    assert not _rejects("set_etl", {"etl_l_amplitude": 1.5})      # no range -> type only


def test_acquisition_entrypoints_enforce_ranges_options_and_fields():
    for call, arguments in (
        ("acquire_start", {"acquisition": {"intensity": 101}}),
        ("set_acquisition_list", {
            "acquisitions": [{"intensity": 101}], "selected_row": 0}),
        ("acquire_start", {"acquisition": {"x_pos": 1001}}),
        ("set_acquisition_list", {
            "acquisitions": [{"filter": "NOPE"}], "selected_row": 0}),
        ("set_acquisition_list", {
            "acquisitions": [{"unknown_remote_field": 1}], "selected_row": 0}),
    ):
        assert _rejects(call, arguments)


def test_acquisition_rows_and_time_lapse_values_are_bounded():
    assert _rejects(
        "set_acquisition_list", {"acquisitions": [{}], "selected_row": -1})
    assert _rejects("run_selected_acquisition", {"row": -1})
    assert _rejects("preview_acquisition", {"row": 0, "z_update": "yes"})
    assert _rejects("time_lapse_start", {"timepoints": 0, "interval_sec": 0})
    assert _rejects("time_lapse_start", {"timepoints": 1, "interval_sec": -1})


def test_remote_snap_never_requests_gui_saving_and_chunks_are_bounded():
    assert not _rejects("snap", {})
    assert not _rejects("snap", {"write": False, "laser_blanking": True})
    assert _rejects("snap", {"write": True})
    assert _rejects("snap", {"laser_blanking": "yes"})
    assert _rejects("set_state", {"settings": {"state": "snap"}})
    assert _rejects("get_snap_image", {"offset": -1})
    assert _rejects("get_snap_image", {"max_bytes": 0})
    assert _rejects("get_snap_image", {"max_bytes": 512 * 1024 + 1})


def test_snapshot_capture_chunks_reconstruct_exact_pixels_and_get_info_reports_it():
    pixels = b"abcdefghijkl"

    class _Image:
        dtype = types.SimpleNamespace(str="<u2", hasobject=False)
        shape = (2, 3)

        def tobytes(self, order="C"):
            assert order == "C"
            return pixels

    class _SnapshotCore:
        cfg = _Cfg()
        state = {
            "state": "idle", "folder": "save", "snap_folder": "snaps",
            "ETL_cfg_file": "etl.csv",
        }
        frame_queue_display = [_Image()]

    core = _SnapshotCore()
    core._mesospim_remote_operation = {
        "id": "op-snap", "command": "snap", "status": "processing",
        "_completion": "snap_image", "warnings": ["remote warning"],
    }
    assert vrc.capture_snap_image(core) is True
    assert vrc.operation_snapshot(core)["status"] == "completed"

    chunks = []
    offset = 0
    while True:
        result = vrc.run(core, "get_snap_image", {
            "operation_id": "op-snap", "offset": offset, "max_bytes": 5})
        chunks.append(base64.b64decode(result["data"]))
        if result["complete"]:
            break
        offset = result["next_offset"]
    reconstructed = b"".join(chunks)
    assert reconstructed == pixels
    assert result["dtype"] == "<u2" and result["shape"] == [2, 3]
    assert result["sha256"] == hashlib.sha256(pixels).hexdigest()

    info = vrc.run(core, "get_info")
    assert info["save_path"] == "save" and info["snap_folder"] == "snaps"
    assert info["warnings"] == ["remote warning"]
    assert "latest_snapshot" not in info
    assert "latest_snapshot" not in vrc.run(core, "get_progress")
    core.state["folder"] = "updated-save"
    assert vrc.run(core, "get_info")["save_path"] == "updated-save"


def test_range_error_names_the_limits():
    # a too-high value must tell the caller WHAT the limit was, not just "out of range".
    try:
        vrc._validate(_core, "move_absolute", {"targets": {"x": 999999}}, {"x": (-25000.0, 25000.0)})
    except ValueError as e:
        assert "25000" in str(e) and "-25000" in str(e)
    else:
        raise AssertionError("no error raised")
    try:
        vrc._validate(_core, "set_intensity", {"intensity": 250}, {})
    except ValueError as e:
        assert "100" in str(e)
    else:
        raise AssertionError("no error raised")


def test_self_test_passes_on_a_good_cfg():
    # the pre-flight the server runs before going live: against a mock Core with this cfg,
    # every check must pass (limits enforced), and nothing touches real hardware.
    ok, report = vrc.self_test(_Cfg())
    assert ok and all(line.startswith("PASS") for line in report)


def test_self_test_fails_closed_when_no_limits():
    class _NoLimits:
        filterdict = {}; zoomdict = {}; laserdict = {}; shutteroptions = []  # noqa: E702
    ok, report = vrc.self_test(_NoLimits())
    assert not ok and any("cannot verify" in line for line in report)  # refuse to go live


def test_get_limits_reports_enforced_rules():
    enforced = vrc._get_limits(_core, {})["enforced"]
    assert enforced["axes"]["x"] == [-25000.0, 25000.0]
    assert enforced["axes"]["theta"] is None                       # range OFF, made visible
    assert enforced["parameters"]["intensity"]["range"] == [0, 100]
    assert enforced["parameters"]["etl_l_amplitude"]["range"] is None
    assert enforced["parameters"]["filter"]["options"] == ["Empty", "515LP"]
    assert enforced["parameters"]["state"]["options"] == list(vrc._MODES)


if __name__ == "__main__":
    _passed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok   {_name}")
            _passed += 1
    print(f"\nALL {_passed} VALIDATION TESTS PASSED")
