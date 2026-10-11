"""The window calls (gui_handles) as TCP, MCP and the AI Assistant reach them, on the Qt substitute.

What the handles do in the windows is tested against the real windows in
test/test_gui_handles_real_qt.py; here: a display call is never refused as busy (the operator can
change the live view during a run too), a malformed one is refused at once, get_display reports a
request's outcome, both transports list and run them, and the assistant's tool waits for the change."""
import threading

import pytest

from mesoSPIM.src.ai_assistant.assistant import dispatch_and_wait
from mesoSPIM.src.remote_control import commands, dispatcher  # noqa: F401  (registers the calls)
from mesoSPIM.test.remote_control.support.fakes import RecordingCore, RecordingHandles
from mesoSPIM.test.remote_control.support.harness import Harness


@pytest.fixture
def core():
    core = RecordingCore()
    core._remote_control_gui_handles = RecordingHandles()
    return core


def test_a_display_change_goes_to_the_windows_and_get_display_reports_it(core):
    reply = dispatcher.run(core, "set_display", {"levels": [150, 2500]})
    assert reply == {"request": 1}
    assert core._remote_control_gui_handles.requests == [("set_display", {"levels": [150, 2500]})]
    shown = dispatcher.run(core, "get_display", {"request": 1})
    assert shown["camera_window"]["levels"] == [150.0, 2500.0] and shown["request"]["status"] == "applied"


@pytest.mark.parametrize("busy", ["operation", "acquiring", "live"])
def test_a_display_change_is_never_refused_as_busy(core, busy):
    if busy == "operation":
        dispatcher.run(core, "start_live", {})                 # a WAIT op holds the gate
    else:
        core.state["state"] = {"acquiring": "run_acquisition_list", "live": "live"}[busy]
    assert dispatcher.run(core, "set_display", {"levels": [1, 2]}) == {"request": 1}


@pytest.mark.parametrize("args, says", [
    ({"levels": [3000, 100]}, "low < high"),
    ({"colormap": "rainbowish"}, "viridis"),
    ({"box_roi": {"x": 2000, "y": 0, "width": 100, "height": 100}}, "outside the frame"),
    ({"overlay": "circle"}, "light_sheet_marker"),
    ({"zoom_to": 3}, "unknown argument"),
])
def test_a_malformed_display_change_is_refused_at_once_and_reaches_no_window(core, args, says):
    with pytest.raises(dispatcher.ValidationError, match=says):
        dispatcher.run(core, "set_display", args)
    assert core._remote_control_gui_handles.requests == []


def test_without_windows_the_display_calls_say_so(core):
    del core._remote_control_gui_handles
    with pytest.raises(dispatcher.ValidationError, match="no windows"):
        dispatcher.run(core, "get_display", {})


@pytest.mark.parametrize("lane", ["tcp", "mcp"])
def test_both_transports_reach_the_display_calls(lane):
    harness = Harness()
    try:
        harness.core._remote_control_gui_handles = RecordingHandles()
        ok, reply = harness.invoke(lane, "set_display", {"levels": [10, 20]})
        assert ok and reply == {"request": 1}
        ok, shown = harness.invoke(lane, "get_display", {"request": 1})
        assert ok and shown["camera_window"]["levels"] == [10.0, 20.0]
    finally:
        harness.stop()


class Acceptor:
    """The assistant's view of the acceptor: the request is applied on the second look."""

    def __init__(self):
        self.calls, self.looks = [], 0

    def dispatch(self, name, args):
        self.calls.append((name, args))
        if name == "set_display":
            return {"request": 7}
        self.looks += 1
        status = "applied" if self.looks >= 2 else "queued"
        return {"camera_window": {"overlay": "box"}, "request": {"number": 7, "status": status}}


class Cfg:
    POLL_INTERVAL_S = 0.0
    WAIT_CAP_S = 5


def test_the_assistant_s_tool_waits_until_the_window_shows_the_change():
    acceptor = Acceptor()
    out = dispatch_and_wait(acceptor, "set_display", {"overlay": "box"}, dispatcher.DISPLAY, threading.Event(), Cfg)
    assert out["request"]["status"] == "applied" and out["camera_window"]["overlay"] == "box"
    assert [c for c in acceptor.calls if c[0] == "get_display"] == [("get_display", {"request": 7})] * 2


@pytest.mark.parametrize("name, args, says", [
    ("set_display", {"window": "contrast", "box_roi": {"x": 0, "y": 0, "width": 1, "height": 1}}, "unknown argument"),
    ("set_display", {"window": "tile_overview", "scale": 2}, "outside the allowed range"),
    ("set_display", {"window": "elsewhere"}, "not one of"),
    ("set_display", {"colormap_ticks": [[0, [0, 0, 0]]]}, "two or more"),
    ("set_display", {"colormap_ticks": [[0, [0, 0, 0]], [1.5, [255, 255, 255]]]}, "between 0 and 1"),
    ("set_display", {"colormap_ticks": [[0, [0, 0, 0]], [1, [300, 0, 0]]]}, "0..255"),
    ("set_display", {"normalization": {"mode": "sideways"}}, "normalization mode"),
    ("set_display", {"profile_roi": {"x": 1}}, "shown"),
    ("set_windows", {}, "show or hide"),
    ("set_windows", {"show": ["camera"], "hide": ["camera"]}, "at once"),
    ("set_windows", {"show": ["somewhere"]}, "list of windows"),
    ("get_picture", {"window": "camera", "path": "D:/x.svg"}, "must end in"),
    ("get_picture", {"window": "camera", "path": "Z:/no/such/folder/x.png"}, "does not exist"),
    ("open_image_file", {"path": "image.png"}, "TIFF"),
    ("open_image_file", {"path": "Z:/no/such/file.tif"}, "not an existing file"),
    ("set_table_view", {}, "name a group"),
    ("set_table_view", {"group": "by colour"}, "group is one of"),
    ("set_table_view", {"select_rows": [-1]}, "row indices"),
])
def test_each_window_call_refuses_what_its_window_cannot_do_and_reaches_no_window(core, name, args, says):
    with pytest.raises(dispatcher.ValidationError, match=says):
        dispatcher.run(core, name, args)
    assert core._remote_control_gui_handles.requests == []


@pytest.mark.parametrize("name, args", [
    ("set_display", {"window": "contrast", "colormap": "magma"}),
    ("set_display", {"window": "tile_overview", "scale": 0.05, "scroll": {"x": 0.5}}),
    ("set_display", {"window": "main", "tab": "Parameters"}),
    ("set_windows", {"show": ["contrast"], "cascade": True}),
    ("get_picture", {"window": "webcam", "max_size": 512}),
    ("set_table_view", {"select_rows": [0, 1], "group": "selected"}),
])
def test_each_window_call_goes_to_the_windows_and_is_never_busy(core, name, args):
    core.state["state"] = "run_acquisition_list"
    assert dispatcher.run(core, name, args) == {"request": 1}
    assert core._remote_control_gui_handles.requests[0][0] == name
