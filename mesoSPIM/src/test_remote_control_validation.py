"""Validation tests for the Remote Control command module.

Qt-free and no live Core: exercises ``_validate`` (the arg gate that runs at the
single ``run()`` choke point, so both the TCP and MCP lanes enforce it) against a
tiny fake ``cfg``, plus the ``MESOSPIM_RS_LIMITS`` env parser. Run either way::

    pytest mesoSPIM/src/test_remote_control_validation.py
    python  mesoSPIM/src/test_remote_control_validation.py
"""
import importlib.util
import os
from pathlib import Path

_MOD = Path(__file__).with_name("mesoSPIM_RemoteControl_ValidateAndRunCommands.py")
_spec = importlib.util.spec_from_file_location("_rc_vrc", _MOD)
vrc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vrc)


class _Cfg:
    filterdict = {"Empty": 0, "515LP": 1}
    zoomdict = {"1x": 1, "2x": 2}
    laserdict = {"488 nm": 0, "561 nm": 1}
    shutteroptions = ["Left", "Right", "Both"]


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


def test_limits_from_env_parses_json():
    os.environ["MESOSPIM_RS_LIMITS"] = '{"x": [-5, 5], "y": [-5, 5]}'
    try:
        assert vrc._limits_from_env() == {"x": (-5.0, 5.0), "y": (-5.0, 5.0)}
    finally:
        del os.environ["MESOSPIM_RS_LIMITS"]


if __name__ == "__main__":
    _passed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok   {_name}")
            _passed += 1
    print(f"\nALL {_passed} VALIDATION TESTS PASSED")
