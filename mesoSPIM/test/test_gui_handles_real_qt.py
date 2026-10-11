"""The Remote Control's handles on mesoSPIM's windows, against the real windows (offscreen).

The camera window is the one the operator watches: these tests build it as mesoSPIM does, show it a
frame, and check that each handle does what the window's own controls do, and that what get_display
reports is what the window shows, in the frame's own pixels (as get_frame gives them) and in um.
"""
import collections
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets  # noqa: E402

from mesoSPIM.src.mesoSPIM_CameraWindow import mesoSPIM_CameraWindow  # noqa: E402
from mesoSPIM.src.mesoSPIM_State import mesoSPIM_StateSingleton  # noqa: E402
from mesoSPIM.src.remote_control import gui_handles  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1]
PIXEL_UM = 6.5


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def main(app):
    """What the camera window and the handles need of mesoSPIM's main window: its config, state and
    Core's display queue, holding one 2048 x 2048 frame shown at subsampling 2."""
    cfg = SimpleNamespace(camera_parameters={"x_pixels": 2048, "y_pixels": 2048},
                          startup={"camera_display_live_subsampling": 2},
                          pixelsize={"1x": PIXEL_UM}, ui_options={"dark_mode": False})
    state = mesoSPIM_StateSingleton()          # mesoSPIM's own state object: a mapping without dict's methods
    for key, value in {"state": "idle", "camera_display_live_subsampling": 2, "zoom": "1x", "shutterconfig": "Left"}.items():
        state[key] = value
    frame = (np.arange(2048 * 2048, dtype=np.uint32) % 4000).astype(np.uint16).reshape(2048, 2048)
    core = SimpleNamespace(frame_queue_display=collections.deque([frame]))
    window = SimpleNamespace(cfg=cfg, state=state, core=core, package_directory=str(PACKAGE))
    window.camera_window = mesoSPIM_CameraWindow(window)
    window.camera_window.update_image_from_deque()
    yield window
    window.camera_window.close()


def apply(main, name, args):
    handles = gui_handles.GuiHandles(main)
    outcome = handles.apply(name, args)
    return outcome, handles.read()


def test_levels_set_remotely_are_the_window_s_levels(main):
    outcome, display = apply(main, "set_display", {"levels": [150, 2500]})
    assert outcome["status"] == "applied"
    assert tuple(main.camera_window.histogram.getLevels()) == pytest.approx((150, 2500))
    assert display["camera_window"]["levels"] == pytest.approx([150, 2500])


def test_adjust_levels_is_the_window_s_own_percentile_stretch(main):
    apply(main, "set_display", {"adjust_levels": {"low_percentile": 10, "high_percentile": 90}})
    shown = main.camera_window.image_view.getImageItem().image
    assert tuple(main.camera_window.histogram.getLevels()) == pytest.approx(
        (np.percentile(shown, 10), np.percentile(shown, 90)))


@pytest.mark.parametrize("name", ["grey", "viridis", "thermal", "magma"])
def test_a_lookup_table_is_applied_and_reported_by_name(main, name):
    outcome, display = apply(main, "set_display", {"colormap": name})
    assert outcome["status"] == "applied" and display["camera_window"]["colormap"] == name
    from pyqtgraph.graphicsItems.GradientEditorItem import Gradients
    shown = main.camera_window.histogram.gradient.saveState()     # the gradient the histogram draws with
    assert sorted((round(p, 6), tuple(c)) for p, c in shown["ticks"]) == \
        sorted((round(p, 6), tuple(c)) for p, c in Gradients[name]["ticks"])


def test_an_unknown_lookup_table_is_refused_with_the_names_there_are(main):
    outcome, _ = apply(main, "set_display", {"colormap": "rainbowish"})
    assert outcome["status"] == "failed" and "viridis" in outcome["error"]


def test_a_box_roi_is_placed_in_frame_pixels_and_read_back_in_frame_pixels_and_um(main):
    """The window shows every 2nd pixel: frame pixels in, frame pixels out; the window holds half."""
    outcome, display = apply(main, "set_display", {"overlay": "box", "box_roi": {"x": 400, "y": 600, "width": 200, "height": 100}})
    assert outcome["status"] == "applied"
    cw = main.camera_window
    assert cw.overlay == "box" and cw.overlayCombo.currentText() == "Box roi"
    assert tuple(cw.roi_box.pos()) == pytest.approx((200, 300)) and tuple(cw.roi_box.size()) == pytest.approx((100, 50))
    box = display["camera_window"]["box_roi"]
    assert (box["x"], box["y"], box["width"], box["height"]) == pytest.approx((400, 600, 200, 100))
    assert (box["width_um"], box["height_um"]) == pytest.approx((200 * PIXEL_UM, 100 * PIXEL_UM))
    # its centre, from the frame's centre: (500 - 1024, 650 - 1024) pixels
    assert (box["offset_x"], box["offset_y"]) == pytest.approx((-524, -374))
    assert (box["offset_x_um"], box["offset_y_um"]) == pytest.approx((-524 * PIXEL_UM, -374 * PIXEL_UM))


def test_a_box_dragged_in_the_window_is_reported_where_it_now_is(main):
    apply(main, "set_display", {"overlay": "box", "box_roi": {"x": 400, "y": 600, "width": 200, "height": 100}})
    main.camera_window.roi_box.setPos((50, 60))            # the operator drags it
    _, display = apply(main, "set_display", {})
    box = display["camera_window"]["box_roi"]
    assert (box["x"], box["y"]) == pytest.approx((100, 120))


def test_a_line_roi_is_placed_and_its_length_read_in_pixels_and_um(main):
    outcome, display = apply(main, "set_display", {"overlay": "line", "line_roi": {"x1": 100, "y1": 200, "x2": 400, "y2": 600}})
    assert outcome["status"] == "applied" and main.camera_window.overlay == "line"
    line = display["camera_window"]["line_roi"]
    assert (line["x1"], line["y1"], line["x2"], line["y2"]) == pytest.approx((100, 200, 400, 600))
    assert line["length"] == pytest.approx(500) and line["length_um"] == pytest.approx(500 * PIXEL_UM)
    assert main.camera_window.get_line_length_px() == pytest.approx(250)      # the window's own measure, display pixels


def test_the_overlay_follows_the_window_s_own_choice(main):
    for overlay, shown in (("none", "Overlay: none"), ("light_sheet_marker", "LS marker"), ("line", "Line roi")):
        outcome, display = apply(main, "set_display", {"overlay": overlay})
        assert outcome["status"] == "applied"
        assert main.camera_window.overlayCombo.currentText() == shown and display["camera_window"]["overlay"] == overlay


def test_the_view_is_set_in_frame_pixels_and_fit_brings_back_the_whole_frame(main):
    """The view keeps square pixels, as the window does: the asked region is shown whole and centred,
    one side fits it exactly, the other widens to the window's shape."""
    _, display = apply(main, "set_display", {"view": {"x": 0, "y": 0, "width": 512, "height": 512}})
    (x0, x1), (y0, y1) = main.camera_window.image_view.getView().viewRange()
    assert x0 <= 0.5 and x1 >= 255.5 and y0 <= 0.5 and y1 >= 255.5             # the asked region, whole
    assert min(x1 - x0, y1 - y0) == pytest.approx(256, abs=1)                    # one side fits it
    assert (x0 + x1) / 2 == pytest.approx(128, abs=1) and (y0 + y1) / 2 == pytest.approx(128, abs=1)
    view = display["camera_window"]["view"]
    assert view["x"] == pytest.approx(x0 * 2) and view["width"] == pytest.approx((x1 - x0) * 2)   # reported as shown
    _, display = apply(main, "set_display", {"view": "fit"})
    view = display["camera_window"]["view"]
    assert view["x"] <= 0 and view["y"] <= 0 and view["width"] >= 2048 and view["height"] >= 2048


def test_a_box_outside_the_frame_is_refused_and_nothing_changes(main):
    apply(main, "set_display", {"overlay": "box", "box_roi": {"x": 0, "y": 0, "width": 100, "height": 100}})
    outcome, display = apply(main, "set_display", {"box_roi": {"x": 2000, "y": 0, "width": 100, "height": 100}})
    assert outcome["status"] == "failed" and "outside the frame" in outcome["error"]
    assert display["camera_window"]["box_roi"]["x"] == pytest.approx(0)


def test_the_display_names_the_frame_it_is_measured_in(main):
    _, display = apply(main, "set_display", {})
    camera = display["camera_window"]
    assert camera["frame"] == {"width": 2048, "height": 2048, "shown_every": 2, "pixel_um": PIXEL_UM}
    assert "status" in camera and camera["visible"] in (True, False)


def test_a_failing_read_is_reported_and_never_stops_mesospim(main):
    """The copy of what the windows show is a convenience: if reading a window fails, get_display says
    why and mesoSPIM carries on (the handles are built while mesoSPIM starts)."""
    handles = gui_handles.GuiHandles(main)
    main.camera_window.histogram = None                      # a window in a state the handles cannot read
    handles.refresh()
    assert "getLevels" in handles.display()["error"]


def test_before_the_first_frame_the_display_is_read_from_the_configured_camera(app):
    """As mesoSPIM starts: the camera window is built, no frame has come yet."""
    state = mesoSPIM_StateSingleton()
    for key, value in {"state": "init", "camera_display_live_subsampling": 2, "zoom": "1x", "shutterconfig": "Left"}.items():
        state[key] = value
    # a camera that is not square: the window shows its frames turned, x_pixels as the height
    cfg = SimpleNamespace(camera_parameters={"x_pixels": 2960, "y_pixels": 5056},
                          startup={"camera_display_live_subsampling": 2},
                          pixelsize={"1x": PIXEL_UM}, ui_options={"dark_mode": False})
    window = SimpleNamespace(cfg=cfg, state=state, core=SimpleNamespace(frame_queue_display=collections.deque()),
                             package_directory=str(PACKAGE))
    window.camera_window = mesoSPIM_CameraWindow(window)
    try:
        handles = gui_handles.GuiHandles(window)
        handles.refresh()
        shown = handles.display()
        assert "error" not in shown, shown.get("error")
        assert shown["camera_window"]["frame"] == {"width": 5056, "height": 2960, "shown_every": 2, "pixel_um": PIXEL_UM}
        assert (window.camera_window.x_image_width, window.camera_window.y_image_width) == (5056, 2960)   # as the window has it
    finally:
        window.camera_window.close()
