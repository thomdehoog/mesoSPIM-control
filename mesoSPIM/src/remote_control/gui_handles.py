"""The Remote Control's handles on mesoSPIM's windows: what an operator can do there, by call.

Calls over TCP, MCP and the AI Assistant reach the instrument through Core. Some of what the operator
does lives only in the windows, though: the camera window's levels, lookup table, view and regions of
interest. This module reaches those windows from outside and works them with their own controls and
methods (the overlay drop-down, set_roi, the histogram's gradient), so the window behaves exactly as
when the operator does the same, and nothing in mesoSPIM's own files changes for it.

Window work happens on the GUI thread, and Core must never wait for that thread (it may itself be
waiting for Core). So a display call is handed to the GUI thread and returns at once with a request
number; the GUI thread applies it and keeps a copy of what the windows show, which get_display reads
without waiting. Positions are spoken in the camera frame's own pixels (x the column, y the row, as
get_frame gives the frame) and in micrometres, whatever subsampling the window shows the frame at.

Maintainer (2026):
    Thom de Hoog
    Center for Microscopy and Image Analysis
    thom.dehoog@zmb.uzh.ch
    thomdehoog@gmail.com
"""
import importlib.util
import itertools
import logging
import threading
from pathlib import Path

import numpy as np
from PyQt5 import QtCore

from .dispatcher import ValidationError

# The camera window's overlay drop-down, by the names the calls use.
OVERLAYS = {"none": "Overlay: none", "box": "Box roi", "line": "Line roi", "light_sheet_marker": "LS marker"}
OVERLAY_OF_WINDOW = {None: "none", "box": "box", "line": "line", "LS marker": "light_sheet_marker"}
KEPT_OUTCOMES = 50

logger = logging.getLogger(__name__)
REFRESH_MS = 250


def presets():
    """The lookup tables the camera window's histogram offers: pyqtgraph's gradient presets. Read from
    pyqtgraph's own table of them without importing pyqtgraph, which needs a running Qt; the calls
    check names on Core's thread too."""
    spec = importlib.util.find_spec("pyqtgraph")
    path = Path(spec.origin).parent / "graphicsItems" / "GradientPresets.py"
    module = importlib.util.module_from_spec(importlib.util.spec_from_file_location("gradient_presets", path))
    module.__spec__.loader.exec_module(module)
    return module.Gradients


def colormaps():
    return list(presets())


class CameraWindowHandle:
    """The camera window as a remote caller sees it: everything in frame pixels and um."""

    def __init__(self, main_window):
        self.main_window = main_window
        self.colormap = None
        self.colormap_ticks = None

    @property
    def window(self):
        return self.main_window.camera_window

    def shown_every(self):
        """How many frame pixels one window pixel stands for: the window shows every n-th pixel."""
        shown = self.window.image_view.getImageItem().image
        queue = getattr(self.main_window.core, "frame_queue_display", None)
        if shown is not None and queue:
            return max(1, int(round(queue[0].shape[-1] / shown.shape[-1])))
        return int(self.main_window.state["camera_display_live_subsampling"])   # what set_image will use

    def frame_size(self):
        return frame_size(self.main_window.core, self.main_window.cfg)

    def pixel_um(self):
        return float(self.main_window.cfg.pixelsize[self.main_window.state["zoom"]])

    # --- what the window shows -------------------------------------------------------------
    def read(self):
        window, every, um = self.window, self.shown_every(), self.pixel_um()
        width, height = self.frame_size()
        (x0, x1), (y0, y1) = window.image_view.getView().viewRange()
        low, high = window.histogram.getLevels()
        shown = {
            "visible": window.isVisible(),
            "frame": {"width": width, "height": height, "shown_every": every, "pixel_um": um},
            "levels": [float(low), float(high)],
            "colormap": self.current_colormap(),
            "overlay": OVERLAY_OF_WINDOW.get(window.overlay, str(window.overlay)),
            "view": {"x": x0 * every, "y": y0 * every, "width": (x1 - x0) * every, "height": (y1 - y0) * every},
            "status": window.status_label.text(),
        }
        if window.overlay == "box":
            (x, y), (w, h) = tuple(window.roi_box.pos()), tuple(window.roi_box.size())
            box = {"x": x * every, "y": y * every, "width": w * every, "height": h * every}
            box.update(width_um=box["width"] * um, height_um=box["height"] * um,
                       offset_x=box["x"] + box["width"] / 2 - width / 2, offset_y=box["y"] + box["height"] / 2 - height / 2)
            box.update(offset_x_um=box["offset_x"] * um, offset_y_um=box["offset_y"] * um)
            shown["box_roi"] = box
        if window.overlay == "line":
            (x1_, y1_), (x2_, y2_) = self.line_ends()
            line = {"x1": x1_ * every, "y1": y1_ * every, "x2": x2_ * every, "y2": y2_ * every}
            line["length"] = float(np.hypot(line["x2"] - line["x1"], line["y2"] - line["y1"]))
            line["length_um"] = line["length"] * um
            shown["line_roi"] = line
        return shown

    def line_ends(self):
        image = self.window.image_view.getImageItem()
        ends = []
        for handle in self.window.roi_line.handles[:2]:
            point = self.window.roi_line.mapToItem(image, handle["pos"])
            ends.append((point.x(), point.y()))
        return ends

    def current_colormap(self):
        """The name of the lookup table the histogram holds: the one set here, else the preset its
        ticks match, else "custom" (the operator edited the gradient by hand)."""
        ticks = self.window.histogram.gradient.saveState()["ticks"]
        if self.colormap is not None and ticks == self.colormap_ticks:
            return self.colormap
        for name, preset in presets().items():
            if [list(map(float, (t[0],))) + list(t[1]) for t in sorted(preset["ticks"])] == \
                    [list(map(float, (t[0],))) + list(t[1]) for t in sorted(ticks)]:
                return name
        return "custom"

    # --- what a caller asks of it ----------------------------------------------------------
    def check(self, args):
        """The call's own check (commands.check_display), again on the GUI thread: the frame may have
        changed size (binning) since the call was accepted."""
        from .commands import check_display
        return check_display(self.frame_size(), args)

    def apply(self, args):
        args = self.check(args)
        window, every = self.window, self.shown_every()
        overlay = args.get("overlay") or ("box" if "box_roi" in args else "line" if "line_roi" in args else None)
        if overlay is not None:
            window.overlayCombo.setCurrentText(OVERLAYS[overlay])      # the window's own choice of overlay
        if "box_roi" in args:
            box = args["box_roi"]
            window.set_roi("box", (box["x"] / every, box["y"] / every, box["width"] / every, box["height"] / every))
        if "line_roi" in args:
            line, image = args["line_roi"], window.image_view.getImageItem()
            for handle, (x, y) in zip(window.roi_line.getHandles()[:2], ((line["x1"], line["y1"]), (line["x2"], line["y2"]))):
                point = window.roi_line.mapFromItem(image, QtCore.QPointF(x / every, y / every))
                window.roi_line.movePoint(handle, window.roi_line.mapToParent(point), finish=False)
            window.roi_line.sigRegionChangeFinished.emit(window.roi_line)
        if "colormap" in args:
            window.histogram.gradient.loadPreset(args["colormap"])
            self.colormap = args["colormap"]
            self.colormap_ticks = window.histogram.gradient.saveState()["ticks"]
        if "levels" in args:
            window.image_view.setLevels(*args["levels"])
        if "adjust_levels" in args:
            adjust = args["adjust_levels"] if isinstance(args["adjust_levels"], dict) else {}
            window.adjust_levels(adjust.get("low_percentile", 25), adjust.get("high_percentile", 99.99))
        if "view" in args:
            box = window.image_view.getView()
            if args["view"] == "fit":
                box.autoRange(padding=0)
            else:
                view = args["view"]
                box.setRange(xRange=(view["x"] / every, (view["x"] + view["width"]) / every),
                             yRange=(view["y"] / every, (view["y"] + view["height"]) / every), padding=0)


class GuiHandles(QtCore.QObject):
    """Lives on the GUI thread. apply() carries out one request there; read() is what the windows
    show. In mesoSPIM, requests arrive from Core's thread through request(), queued, and the copy
    that get_display reads is refreshed after each request and every REFRESH_MS."""

    sig_request = QtCore.pyqtSignal(int, str, object)

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.camera = CameraWindowHandle(main_window)
        self.numbers = itertools.count(1)
        self.lock = threading.Lock()
        self.outcomes = {}
        self.shown = {}
        self.sig_request.connect(self.perform, type=QtCore.Qt.QueuedConnection)
        self.timer = None

    def start(self):
        """Keep the copy fresh while mesoSPIM runs (not in tests, which call read() themselves)."""
        self.refresh()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.setInterval(REFRESH_MS)
        self.timer.start()

    # --- from any thread ----------------------------------------------------------------------
    def request(self, name, args):
        """Hand one request to the GUI thread and return its number at once."""
        number = next(self.numbers)
        with self.lock:
            self.outcomes[number] = {"status": "queued"}
        self.sig_request.emit(number, name, args)
        return number

    def display(self, number=None):
        """The latest copy of what the windows show, with one request's outcome when asked for."""
        with self.lock:
            shown = dict(self.shown)
            if number is not None:
                shown["request"] = {"number": number, **self.outcomes.get(number, {"status": "unknown"})}
        return shown

    # --- on the GUI thread --------------------------------------------------------------------
    @QtCore.pyqtSlot(int, str, object)
    def perform(self, number, name, args):
        outcome = self.apply(name, args)
        with self.lock:
            self.outcomes[number] = outcome
            for old in sorted(self.outcomes)[:-KEPT_OUTCOMES]:
                del self.outcomes[old]
        self.refresh()

    def apply(self, name, args):
        try:
            if name == "set_display":
                self.camera.apply(args or {})
            else:
                raise ValidationError(f"no window handle for {name!r}")
        except ValidationError as error:
            return {"status": "failed", "error": str(error)}
        return {"status": "applied"}

    def read(self):
        """What each window shows; a window the main window does not have (yet) is left out."""
        shown = {}
        if getattr(self.main_window, "camera_window", None) is not None:
            shown["camera_window"] = self.camera.read()
        return shown

    @QtCore.pyqtSlot()
    def refresh(self):
        """Renew the copy get_display reads. Reading a window must never stop mesoSPIM (this runs while
        it starts, and on a timer): a failure is logged and is what get_display then reports."""
        try:
            shown = self.read()
        except Exception as error:
            logger.exception("Remote Control: reading the windows for get_display failed")
            shown = {"error": f"reading the windows failed: {type(error).__name__}: {error}"}
        with self.lock:
            self.shown = shown



def frame_size(core, cfg):
    """(width, height) of the camera frame as the windows show it: the frame on display, else the
    camera's configured size, turned as the camera window turns it (its x_image_width is the
    configured y_pixels: "flipped to account for image rotation")."""
    queue = getattr(core, "frame_queue_display", None)
    if queue:
        height, width = queue[0].shape[-2:]
    else:
        camera = cfg.camera_parameters
        width, height = camera["y_pixels"], camera["x_pixels"]
    return int(width), int(height)

