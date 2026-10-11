"""Every control of the main window and the acquisition manager has its Remote Control call (plan 3.0).

The table maps each control in the two .ui files to exactly one of: the call that does what it does
("call", or "call:key" for a setting the call takes), "display" (it shows or arranges, it does not
change the instrument), or "not exposed: <why>". The test fails when a control has no entry (a new
button upstream), when an entry names a call or a key that does not exist, and when the table names
a control the .ui files no longer have: comprehensive, checked rather than promised. Buttons that
differ only by a value are one call (the ten jog buttons are move_relative)."""
import re
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

# The other windows designed in .ui files (gui_handles works them).
CAMERA_WINDOW = {"adjustLevelsButton": "set_display", "overlayCombo": "set_display"}
TILE_OVERVIEW = {"doubleSpinBox_scale": "set_display"}
OPTIMIZER = {**{name: "optimize" for name in ("comboBoxMode", "searchAmpDoubleSpinBox", "nPointsSpinBox", "runButton")},
             "closeButton": "set_windows"}
OPTIMIZER_RESULTS = {"acceptButton": "finish_optimization", "discardButton": "finish_optimization"}

WINDOWS = {"mesoSPIM_MainWindow.ui": MAIN_WINDOW, "mesoSPIM_AcquisitionManagerWindow.ui": ACQUISITION_MANAGER,
           "mesoSPIM_CameraWindow.ui": CAMERA_WINDOW, "mesoSPIM_Tile_Overview.ui": TILE_OVERVIEW,
           "mesoSPIM_Optimizer.ui": OPTIMIZER, "mesoSPIM_Optimizer_Results.ui": OPTIMIZER_RESULTS,
           "WebcamWindow.ui": {}}

# Controls built in code rather than in a .ui file: the wizards' pages, the table's cell editors,
# the processor chain window, the Utils tools, the menu actions added in code. Per file, how many
# controls it builds and the calls that reach what they do; a control added to any of them changes
# its count and fails the test until it is mapped here.
SRC = Path(__file__).resolve().parents[2] / "src"
BUILT = re.compile(r"Qt(?:Widgets)?\.(QPushButton|QToolButton|QCheckBox|QComboBox|QSpinBox|QDoubleSpinBox|QSlider|QLineEdit|"
                   r"QRadioButton|QAction|QShortcut)\(")
CODE_BUILT = {
    "mesoSPIM_MainWindow.py": (4, ["set_processor_chain", "analyse_psf", "analyse_field_curvature"]),   # Plugins, Utils menus
    "mesoSPIM_ProcessorChainWindow.py": (12, ["get_processor_chain", "set_processor_chain"]),
    "mesoSPIM_ScriptWindow.py": (3, "not exposed: the script editor runs code"),
    "utils/delegates.py": (7, ["update_acquisition_row", "get_position"]),           # the table's cell editors
    "utils/widgets.py": (3, ["update_acquisition_row", "get_position"]),             # their M button and slider
    "utils/multicolor_acquisition_wizard.py": (36, ["build_tiling_list", "get_position", "move_absolute"]),
    "utils/filename_wizard.py": (15, ["name_acquisition_rows", "get_writer_parameters", "set_writer_parameters"]),
    "utils/focus_tracking_wizard.py": (8, ["track_focus", "get_position"]),
    "utils/image_processing_wizard.py": (1, ["update_acquisition_row"]),
    "utils/psf_gui_qt.py": (17, ["analyse_psf"]),                                    # and its plots' own ranges
    "utils/field_curvature_gui_qt.py": (17, ["analyse_field_curvature"]),
}
NOT_GUI = ("remote_control", "ai_assistant", "mesospim_viewer")


def built_in_code():
    """{file: number of controls it builds}, for every module of mesoSPIM that builds any."""
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        if any(part in path.parts for part in NOT_GUI):
            continue
        count = len(BUILT.findall(path.read_text(encoding="utf-8", errors="ignore")))
        if count:
            found[path.relative_to(SRC).as_posix()] = count
    return found


def test_every_control_built_in_code_is_counted_and_reaches_a_call():
    found = built_in_code()
    assert found == {name: count for name, (count, _) in CODE_BUILT.items()}, \
        "controls built in code changed: map the new ones to their calls in CODE_BUILT"
    for name, (_, calls) in CODE_BUILT.items():
        if isinstance(calls, str):
            assert calls.startswith("not exposed: "), name
        else:
            assert all(call in COMMANDS for call in calls), (name, calls)


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
    """Everything the windows designed in .ui files do is a call, but three things on purpose: the jog
    step boxes (move_relative takes the step), the script editor (it runs code) and closing mesoSPIM."""
    entries = [entry for table in WINDOWS.values() for entry in table.values()]
    calls = {entry.partition(":")[0] for entry in entries if entry != "display" and not entry.startswith("not")}
    left = sorted({entry for entry in entries if entry.startswith("not exposed: ")})
    assert len(calls) == 48, len(calls)
    assert left == ["not exposed: closes mesoSPIM", "not exposed: the jog buttons' step; move_relative takes the step itself",
                    "not exposed: the script editor runs code"], left
