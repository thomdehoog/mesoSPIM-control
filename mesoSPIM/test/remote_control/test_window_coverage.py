"""Every control of the main window and the acquisition manager has its Remote Control call (plan 3.0).

The table maps each control in the two .ui files to exactly one of: the call that does what it does
("call", or "call:key" for a setting the call takes), "display" (it shows or arranges, it does not
change the instrument), or "not exposed: <why>". The test fails when a control has no entry (a new
button upstream), when an entry names a call or a key that does not exist, and when the table names
a control the .ui files no longer have: comprehensive, checked rather than promised. Buttons that
differ only by a value are one call (the ten jog buttons are move_relative)."""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mesoSPIM.src.remote_control import (
    commands,  # noqa: F401  (registers the calls)
    config,
)
from mesoSPIM.src.remote_control.dispatcher import COMMANDS

GUI = Path(__file__).resolve().parents[2] / "gui"
CONTROLS = ("QPushButton", "QCheckBox", "QComboBox", "QSpinBox", "QDoubleSpinBox", "QSlider", "QLineEdit")
JOG = "move_relative"
ZERO = "zero"

MAIN_WINDOW = {
    # the position read-outs
    **{f"{axis}_Position_Indicator": "get_position" for axis in ("X", "Y", "Z", "Rotation", "Focus")},
    # the light
    "FilterComboBox": "set_filter", "ZoomComboBox": "set_zoom", "ShutterComboBox": "set_shutterconfig",
    "LaserComboBox": "set_laser", "LaserIntensitySlider": "set_intensity", "LaserIntensitySpinBox": "set_intensity",
    # modes and runs
    "LiveButton": "start_live", "SnapButton": "snap", "RunSelectedAcquisitionButton": "run_selected_acquisition",
    "RunAcquisitionListButton": "run_acquisition_list", "RunTimelapseButton": "time_lapse_start",
    "StopButton": "stop_activity", "VisualModeButton": "start_visual_mode",
    "LightsheetSwitchingModeButton": "start_lightsheet_alignment_mode",
    # the stage
    **{name: JOG for name in ("xPlusButton", "xMinusButton", "yPlusButton", "yMinusButton", "zPlusButton",
                              "zMinusButton", "focusPlusButton", "focusMinusButton", "rotPlusButton", "rotMinusButton")},
    **{name: ZERO for name in ("xyZeroButton", "zZeroButton", "focusZeroButton", "rotZeroButton")},
    **{name: "not exposed: the jog buttons' step; move_relative takes the step itself"
       for name in ("xyzIncrementSpinbox", "focusIncrementSpinbox", "rotIncrementSpinbox")},
    "xyzLoadButton": "load_sample", "xyzUnloadButton": "unload_sample", "centerButton": "center_sample",
    "xyzrotStopButton": "stop",
    "focusAutoButton": "optimize", "launchOptimizerButton": "optimize",
    # the ETL
    "LeftETLOffsetSpinBox": "set_etl:etl_l_offset", "RightETLOffsetSpinBox": "set_etl:etl_r_offset",
    "LeftETLAmplitudeSpinBox": "set_etl:etl_l_amplitude", "RightETLAmplitudeSpinBox": "set_etl:etl_r_amplitude",
    "LeftETLDelaySpinBox": "set_etl:etl_l_delay_%", "RightETLDelaySpinBox": "set_etl:etl_r_delay_%",
    "LeftETLRampRisingSpinBox": "set_etl:etl_l_ramp_rising_%", "RightETLRampRisingSpinBox": "set_etl:etl_r_ramp_rising_%",
    "LeftETLRampFallingSpinBox": "set_etl:etl_l_ramp_falling_%",
    "RightETLRampFallingSpinBox": "set_etl:etl_r_ramp_falling_%",
    "ChooseETLcfgButton": "reload_etl_config", "SaveETLParametersButton": "save_etl_config",
    "ETLIncrementSpinBox": "display",
    **{name: "set_alignment_toggles" for name in ("ZeroLeftETLButton", "ZeroRightETLButton", "freezeGalvoButton")},
    # camera, timing and galvos
    "SweeptimeSpinBox": "set_state:sweeptime",
    "CameraExposureTimeSpinBox": "set_camera:camera_exposure_time",
    "CameraTriggerDelaySpinBox": "set_camera:camera_delay_%", "CameraTriggerPulseLengthSpinBox": "set_camera:camera_pulse_%",
    "BinningComboBox": "set_camera:camera_binning",
    "LiveSubSamplingComboBox": "set_camera:camera_display_live_subsampling",
    "AcquisitionSubSamplingComboBox": "set_camera:camera_display_acquisition_subsampling",
    "LeftLaserPulseDelaySpinBox": "set_laser_timing:laser_l_delay_%",
    "RightLaserPulseDelaySpinBox": "set_laser_timing:laser_r_delay_%",
    "LeftLaserPulseLengthSpinBox": "set_laser_timing:laser_l_pulse_%",
    "RightLaserPulseLengthSpinBox": "set_laser_timing:laser_r_pulse_%",
    "LeftLaserPulseMaxAmplitudeSpinBox": "set_laser_timing:laser_l_max_amplitude_%",
    "RightLaserPulseMaxAmplitudeSpinBox": "set_laser_timing:laser_r_max_amplitude_%",
    "GalvoFrequencySpinBox": "set_galvo:galvo_l_frequency", "LeftGalvoAmplitudeSpinBox": "set_galvo:galvo_l_amplitude",
    "LeftGalvoOffsetSpinBox": "set_galvo:galvo_l_offset", "RightGalvoOffsetSpinBox": "set_galvo:galvo_r_offset",
    "LeftGalvoPhaseSpinBox": "set_galvo:galvo_l_phase", "RightGalvoPhaseSpinBox": "set_galvo:galvo_r_phase",
    "checkBoxScaleWZoom": "set_galvo:galvo_amp_scale_w_zoom",
    # files and settings
    "ChooseSnapFolderButton": "set_snap_folder",
    "SaveToConfigButton": "save_parameters_to_config",
    # the time-lapse tab: time_lapse_start takes the interval and the number of points
    "EnableTimelapseCheckBox": "display", "AsFastAsPossibleCheckBox": "time_lapse_start",
    "TimelapseHoursSpinBox": "time_lapse_start", "TimelapseMinutesSpinBox": "time_lapse_start",
    "TimelapseSecondsSpinBox": "time_lapse_start", "TimelapseTimepointsSpinBox": "time_lapse_start",
    # windows of their own
    "ContrastWindowButton": "set_windows",
    "openScriptEditorButton": "not exposed: the script editor runs code",
    "actionScriptWindow": "not exposed: the script editor runs code",
    **{name: "set_windows" for name in ("actionOpen_Camera_Window", "actionOpen_Acquisition_Manager",
                                        "actionCascade_windows", "actionOpen_Webcam_Window", "actionOpen_Tile_Overview")},
    "actionOpen_TIFF": "open_image_file",
    "actionAbout": "display",                                   # the version: get_info
    **{name: "not exposed: closes mesoSPIM" for name in ("actionClose", "actionClose_2", "actionExit")},
}

ACQUISITION_MANAGER = {
    "AddButton": "add_acquisition_rows", "CopyButton": "add_acquisition_rows",
    "DeleteButton": "delete_acquisition_rows", "DeleteAllButton": "delete_acquisition_rows",
    "MoveUpButton": "move_acquisition_row", "MoveDownButton": "move_acquisition_row",
    **{name: "mark_acquisition_rows" for name in ("MarkCurrentXYButton", "MarkCurrentRotationButton",
                                                  "MarkCurrentFocusButton", "MarkCurrentETLParametersButton",
                                                  "MarkCurrentStateButton", "MarkAllButton")},
    "SaveButton": "save_acquisition_list", "LoadButton": "load_acquisition_list",
    "PreviewSelectionButton": "preview_acquisition", "PreviewZCheckBox": "preview_acquisition",
    "TilingWizardButton": "build_tiling_list", "FilenameWizardButton": "name_acquisition_rows",
    "FocusTrackingWizardButton": "track_focus", "SetFoldersButton": "update_acquisition_row",
    "AutoIlliminationButton": "auto_illumination",
    "ImageProcessingWizardButton": "update_acquisition_row",     # it sets each row's Processing (MAX or none)
    **{name: "set_table_view" for name in ("GroupByChannelButton", "GroupByIlluminationButton", "GroupSelectedButton")},
}

WINDOWS = {"mesoSPIM_MainWindow.ui": MAIN_WINDOW, "mesoSPIM_AcquisitionManagerWindow.ui": ACQUISITION_MANAGER}


def controls(ui):
    root = ET.parse(GUI / ui).getroot()
    return ({w.get("name") for w in root.iter("widget") if w.get("class") in CONTROLS}
            | {a.get("name") for a in root.iter("action")})


@pytest.mark.parametrize("ui", sorted(WINDOWS))
def test_every_control_has_one_entry_and_every_entry_a_control(ui):
    found, table = controls(ui), WINDOWS[ui]
    assert sorted(found - set(table)) == [], "controls with no entry: give each its call, display or not exposed"
    assert sorted(set(table) - found) == [], "entries for controls the window no longer has"


def test_every_entry_names_a_call_and_a_key_that_exist():
    for ui, table in WINDOWS.items():
        for control, entry in table.items():
            if entry == "display" or entry.startswith("not exposed: "):
                continue
            name, _, key = entry.partition(":")
            assert name in COMMANDS, (ui, control, entry)
            if key:
                settable = config.SETTING_GROUPS.get(name, config.SETTABLE_STATE_KEYS)
                assert key in settable, (ui, control, entry)


def test_the_window_s_features_are_calls_and_what_is_left_is_named():
    """Everything the two windows do is a call, but three things on purpose: the jog step boxes
    (move_relative takes the step), the script editor (it runs code) and closing mesoSPIM."""
    entries = [entry for table in WINDOWS.values() for entry in table.values()]
    calls = {entry.partition(":")[0] for entry in entries if entry != "display" and not entry.startswith("not")}
    left = sorted({entry for entry in entries if entry.startswith("not exposed: ")})
    assert len(calls) == 46, len(calls)
    assert left == ["not exposed: closes mesoSPIM", "not exposed: the jog buttons' step; move_relative takes the step itself",
                    "not exposed: the script editor runs code"], left
