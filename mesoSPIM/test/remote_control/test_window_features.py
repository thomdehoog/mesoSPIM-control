"""The windows' own instrument features as calls (gui_handles), on the Qt substitute: the writer
parameters of the filename wizard, the laser pulse's max amplitude, the processor chain, the ETL and
galvo toggles, the optimizer, Save to config and auto illumination.

What the window-driven ones do in the windows, and that the two handlers whose dialogs stand in the
way (Save to config, auto illumination) are repeated exactly, is tested against the real code in
test/test_gui_handles_real_qt.py."""
import pytest

from mesoSPIM.src.remote_control import commands, dispatcher  # noqa: F401  (registers the calls)
from mesoSPIM.test.remote_control.support.fakes import RecordingChain as Chain, RecordingCore, RecordingHandles
from mesoSPIM.test.remote_control.support.writers import ZARR, use_tiff_writer, use_zarr_writer

@pytest.fixture
def core():
    core = RecordingCore()
    core._remote_control_gui_handles = RecordingHandles()
    return core


# --- the filename wizard's Writer parameters --------------------------------------------------
def test_a_writer_s_parameters_are_its_wizard_page_s_rows():
    core = RecordingCore()
    shown = dispatcher.run(core, "get_writer_parameters", {"writer": use_zarr_writer()})["parameters"]
    assert shown["compression"] == {"value": "zstd", "default": "zstd", "help": shown["compression"]["help"]}
    assert shown["shards"]["value"] == [64, 6000, 6000] and "shards" in shown and len(shown) == 11


def test_writer_parameters_set_for_the_session_are_what_the_writer_reads():
    core = RecordingCore()
    writer = use_zarr_writer()
    dispatcher.run(core, "set_writer_parameters", {"writer": writer, "parameters": {
        "compression": None, "compression_level": 3, "shards": [32, 2048, 2048], "generate_multiscales": False}})
    assert getattr(core.cfg, writer) == {"compression": None, "compression_level": 3, "shards": (32, 2048, 2048),
                                         "generate_multiscales": False}
    shown = dispatcher.run(core, "get_writer_parameters", {"writer": writer})["parameters"]
    assert shown["shards"]["value"] == [32, 2048, 2048] and shown["compression_level"]["value"] == 3


@pytest.mark.parametrize("args, says", [
    ({"writer": "Nobody", "parameters": {"a": 1}}, "no image writer"),
    ({"writer": ZARR, "parameters": {"chunks_please": 1}}, "has no setting chunks_please"),
    ({"writer": ZARR, "parameters": {"generate_multiscales": 1}}, "true or false"),
    ({"writer": ZARR, "parameters": {"compression_level": -1}}, "whole number"),
    ({"writer": ZARR, "parameters": {"compression": 5}}, "text, or null"),
    ({"writer": ZARR, "parameters": {}}, "at least one"),
])
def test_a_writer_parameter_the_page_would_not_take_is_refused(args, says):
    use_zarr_writer(), use_tiff_writer()
    core = RecordingCore()
    with pytest.raises(dispatcher.ValidationError, match=says):
        dispatcher.run(core, "set_writer_parameters", args)
    assert not hasattr(core.cfg, ZARR)


# --- the laser pulse's max amplitude -----------------------------------------------------------
def test_the_laser_pulse_max_amplitude_is_set_as_the_parameters_tab_sets_it():
    core = RecordingCore()
    dispatcher.run(core, "set_laser_timing", {"laser_l_max_amplitude_%": 80, "laser_r_max_amplitude_%": 90})
    requests = [call for call in core.calls() if call[0] == "state_request"] or core.calls()
    assert any("laser_l_max_amplitude_%" in str(call) for call in requests)
    with pytest.raises(dispatcher.ValidationError, match="outside the allowed range"):
        dispatcher.run(RecordingCore(), "set_laser_timing", {"laser_l_max_amplitude_%": 150})


# --- the processor chain -----------------------------------------------------------------------
def test_the_processor_chain_is_read_and_set_as_its_window_does(core):
    core.camera_worker = type("Worker", (), {"processor_chain": Chain()})()
    shown = dispatcher.run(core, "get_processor_chain", {})
    assert shown["available"][0]["name"] == "GaussianBlur" and shown["available"][0]["parameters"]["sigma"]["max"] == 100.0
    assert shown["available"][1] == {"name": "Identity", "description": "as is", "parameters": {}}
    dispatcher.run(core, "set_processor_chain", {"processors": [{"name": "GaussianBlur", "config": {"sigma": 2.5}}]})
    chain = core.camera_worker.processor_chain
    assert chain.config == {"processors": [{"name": "GaussianBlur", "enabled": True, "config": {"sigma": 2.5}}]}
    assert ("refresh_processor_chain", {}) in core._remote_control_gui_handles.requests      # an open window shows it


@pytest.mark.parametrize("processors, says", [
    ([{"name": "Sharpen"}], "no processor 'Sharpen'"),
    ([{"name": "GaussianBlur", "config": {"radius": 1}}], "has no parameter radius"),
    ([{"name": "GaussianBlur", "config": {"sigma": 500}}], "outside"),
    ([{"name": "GaussianBlur", "config": {"mode": "wrap"}}], "not one of"),
    ([{"name": "GaussianBlur", "config": {"sigma": "big"}}], "must be a number"),
    ([{"name": "Identity", "config": {"gain": 2}}], "has no parameter gain"),
])
def test_a_chain_its_window_would_not_take_is_refused_and_nothing_changes(core, processors, says):
    core.camera_worker = type("Worker", (), {"processor_chain": Chain()})()
    with pytest.raises(dispatcher.ValidationError, match=says):
        dispatcher.run(core, "set_processor_chain", {"processors": processors})
    assert core.camera_worker.processor_chain.config == {"processors": []}


# --- the toggles and the optimizer: operations the GUI thread carries out ----------------------
@pytest.mark.parametrize("name, args", [
    ("set_alignment_toggles", {"zero_left_etl": True}),
    ("optimize", {"mode": "focus", "range": 300, "points": 7}),
    ("finish_optimization", {"accept": False}),
])
def test_a_window_s_instrument_feature_holds_the_gate_until_the_gui_thread_finishes_it(core, name, args):
    reply = dispatcher.run(core, name, args)
    operation = reply["operation"]
    assert operation["status"] == "processing"
    handles = core._remote_control_gui_handles
    assert handles.operations == [(operation["id"], name, args)]
    with pytest.raises(dispatcher.BusyError):                      # an instrument operation: gated
        dispatcher.run(core, "set_intensity", {"intensity": 20})
    dispatcher.finish_operation(core, operation["id"], {"done": name})
    finished = dispatcher.operation_snapshot(core)
    assert finished["status"] == "completed" and finished["result"]["done"] == name


def test_a_window_feature_that_fails_ends_its_operation_with_the_reason(core):
    operation = dispatcher.run(core, "optimize", {"mode": "focus", "range": 300})["operation"]
    dispatcher.fail_operation(core, operation["id"], "the optimizer refused: no camera")
    finished = dispatcher.operation_snapshot(core)
    assert finished["status"] == "failed" and "no camera" in finished["error"]


@pytest.mark.parametrize("name, args, says", [
    ("set_alignment_toggles", {}, "name one or more"),
    ("set_alignment_toggles", {"zero_middle_etl": True}, "unknown argument"),
    ("optimize", {"mode": "focus", "range": 300, "points": 8}, "odd"),
    ("optimize", {"mode": "sharpness", "range": 300}, "mode is one of"),
    ("optimize", {"mode": "focus", "range": 300, "points": 99}, "must be <="),
    ("optimize", {"mode": "focus", "range": 300, "roi": {"x": 0, "y": 0, "width": 99999, "height": 10}}, "outside the frame"),
    ("finish_optimization", {}, "'accept' is required"),
])
def test_a_window_feature_it_would_not_take_is_refused_at_once(core, name, args, says):
    with pytest.raises(dispatcher.ValidationError, match=says):
        dispatcher.run(core, name, args)
    assert core._remote_control_gui_handles.operations == []


def test_an_etl_optimization_needs_one_light_sheet_as_the_optimizer_asks(core):
    core.state["shutterconfig"] = "Both"
    with pytest.raises(dispatcher.ValidationError, match="one light sheet"):
        dispatcher.run(core, "optimize", {"mode": "etl_offset", "range": 0.5})


# --- Save to config ----------------------------------------------------------------------------
def test_the_parameters_are_saved_into_the_config_file(core, tmp_path):
    config_file = tmp_path / "my_config.py"
    config_file.write_text("startup = {\n    'sweeptime': 0.1,\n    'camera_binning': '1x1',\n}\n", encoding="utf-8")
    core.cfg.__file__ = str(config_file)
    from mesoSPIM.src.remote_control.gui_handles import CONFIG_PARAMETERS
    for key in CONFIG_PARAMETERS:                                  # mesoSPIM's state has no dict methods
        core.state[key] = 1
    core.state["sweeptime"], core.state["camera_binning"] = 0.25, "2x2"
    with pytest.raises(dispatcher.ValidationError, match="overwrite=true"):
        dispatcher.run(core, "save_parameters_to_config", {})
    dispatcher.run(core, "save_parameters_to_config", {"overwrite": True})
    saved = config_file.read_text(encoding="utf-8")
    assert "'sweeptime': 0.25" in saved and "'camera_binning': '2x2'" in saved and "startup.update({" in saved
    with pytest.raises(dispatcher.ValidationError, match="end in .py"):
        dispatcher.run(core, "save_parameters_to_config", {"path": str(tmp_path / "x.txt")})


# --- auto illumination -------------------------------------------------------------------------
@pytest.mark.parametrize("flip, sides", [(False, ["Right", "Left", "Left"]), (True, ["Left", "Right", "Right"])])
def test_auto_illumination_lights_each_tile_from_the_side_its_x_says(core, flip, sides):
    use_tiff_writer()
    row = {"z_start": 0, "z_end": 20, "z_step": 10, "laser": "488 nm", "filter": "Empty", "zoom": "1x",
           "intensity": 10, "shutterconfig": "Both", "folder": "tmp"}
    dispatcher.run(core, "set_acquisition_list", {"acquisitions": [dict(row, x_pos=x, filename=f"{x}.tif") for x in (0, 1000, 2000)]})
    core.cfg.ui_options = {"flip_auto_LR_illumination": flip}
    dispatcher.run(core, "auto_illumination", {})
    assert [r["shutterconfig"] for r in core.state["acq_list"]] == sides



# --- the Utils menu's analyses ----------------------------------------------------------------
@pytest.mark.parametrize("name, args, says", [
    ("analyse_psf", {"path": "beads.png"}, "TIFF z-stack"),
    ("analyse_psf", {"path": "Z:/no/such/beads.tif"}, "not an existing file"),
    ("analyse_field_curvature", {"channels": []}, "one or more"),
    ("analyse_field_curvature", {"channels": [{"path": "a.png"}]}, "TIFF z-stack"),
])
def test_an_analysis_of_a_file_it_cannot_read_is_refused_at_once(core, name, args, says):
    with pytest.raises(dispatcher.ValidationError, match=says):
        dispatcher.run(core, name, args)
    assert core._remote_control_gui_handles.requests == []


def test_an_analysis_is_never_busy_and_names_its_tilt_reference(core, tmp_path):
    stack = tmp_path / "stack.tif"
    stack.write_bytes(b"II*" + bytes(1))
    core.state["state"] = "run_acquisition_list"
    assert dispatcher.run(core, "analyse_psf", {"path": str(stack)}) == {"request": 1}
    with pytest.raises(dispatcher.ValidationError, match="z_step_um=0.0 is outside the allowed range"):
        dispatcher.run(core, "analyse_psf", {"path": str(stack), "z_step_um": 0})
    with pytest.raises(dispatcher.ValidationError, match="tilt_reference"):
        dispatcher.run(core, "analyse_field_curvature", {"channels": [{"path": str(stack)}], "tilt_reference": "other.tif"})
    accepted = core._remote_control_gui_handles.requests[0][1]
    assert accepted["magnification"] == 5.0 and accepted["pixel_pitch_um"] == 4.25 and accepted["z_step_um"] == 1.0
