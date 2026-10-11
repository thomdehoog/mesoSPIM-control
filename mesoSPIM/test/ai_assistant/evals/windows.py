"""The windows of the simulated instrument: what gui_handles reaches in mesoSPIM's windows, kept in
memory for the evaluation.

A display call is carried out at once and its outcome kept, as gui_handles keeps it; get_display
reads the same shape gui_handles reads from the real windows (the live view in frame pixels and um,
the contrast window, the tile overview, the main window's readouts, the acquisition table's view,
which windows are open). The window features that change the instrument (the alignment toggles,
the optimizer) end their operation at once, as the GUI thread ends it. What a case expects of the
windows afterwards is scored with "display" and "display_near" (harness.score).
"""
from __future__ import annotations

import base64

from mesoSPIM.src.remote_control import dispatcher
from mesoSPIM.src.remote_control.gui_handles import OVERLAYS, frame_size

TABS = ["Movement", "ETL", "Parameters", "Alignment", "Timelapse", "Scripting", "Remote Control", "AI Assistant"]
# The contrast window's 8 x 8 map of the simulated frame: even, but for its top-left corner.
CONTRAST_MAP = [[0.12 if row < 2 and column < 2 else 0.55 for column in range(8)] for row in range(8)]
PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode("ascii")


class SimulatedWindows:
    """gui_handles' interface (request, display, operation) over windows held in memory."""

    def __init__(self, core):
        self.core = core
        self.requests, self.outcomes, self.operations = [], {}, []
        width, height = frame_size(core, core.cfg)
        self.camera = {"levels": [100.0, 3000.0], "colormap": "grey", "colormap_mode": "rgb", "colormap_ticks": None,
                       "histogram_range": [0.0, 4000.0], "view": {"x": 0.0, "y": 0.0, "width": float(width), "height": float(height)},
                       "invert_x": False, "invert_y": False, "overlay": "light_sheet_marker", "box": None, "line": None,
                       "profile_roi": {"shown": False}, "normalization": {"mode": "off", "roi": False, "frame": False,
                                                                         "time_range": False}, "frames": None}
        self.contrast = {"colormap": "jet", "levels": [0.0, 1.0]}
        self.tile_overview = {"scale": 0.01, "scroll": {"x": 0.5, "y": 0.5}}
        self.tab = "Movement"
        self.table = {"grouping": "none", "selected_rows": [0]}
        self.open = {"main": True, "camera": True, "acquisition_manager": True, "tile_overview": True, "webcam": False,
                     "contrast": False, "processor_chain": False, "optimizer": False}
        self.toggles = {"zero_left_etl": False, "zero_right_etl": False, "freeze_galvo": False}
        self.optimization = None

    # --- gui_handles' interface ---------------------------------------------------------------
    def request(self, name, args):
        self.requests.append((name, args))
        number = len(self.requests)
        try:
            result = getattr(self, name)(dict(args))
            self.outcomes[number] = {"status": "applied", **({"result": result} if result is not None else {})}
        except dispatcher.ValidationError as error:
            self.outcomes[number] = {"status": "failed", "error": str(error)}
        return number

    def display(self, number=None):
        shown = self.read()
        if number is not None:
            shown["request"] = {"number": number, **self.outcomes.get(number, {"status": "unknown"})}
        return shown

    def operation(self, core, operation_id, name, args):
        self.operations.append((operation_id, name, args))
        try:
            result = getattr(self, name)(dict(args))
        except dispatcher.ValidationError as error:
            dispatcher.fail_operation(core, operation_id, error)
        else:
            dispatcher.finish_operation(core, operation_id, result)

    # --- what the windows show ----------------------------------------------------------------
    def pixel_um(self):
        return float(self.core.cfg.pixelsize[self.core.state["zoom"]])

    def read(self):
        width, height = frame_size(self.core, self.core.cfg)
        um = self.pixel_um()
        camera = {key: value for key, value in self.camera.items() if key not in ("box", "line", "frames")}
        camera["colormap"] = self.camera["colormap"] if self.camera["colormap_ticks"] is None else "custom"
        camera.update(visible=self.open["camera"], frame={"width": width, "height": height, "shown_every": 2, "pixel_um": um},
                      status="Status: OK")
        if self.camera["overlay"] == "box" and self.camera["box"]:
            box = dict(self.camera["box"])
            box.update(width_um=box["width"] * um, height_um=box["height"] * um,
                       offset_x=box["x"] + box["width"] / 2 - width / 2, offset_y=box["y"] + box["height"] / 2 - height / 2)
            box.update(offset_x_um=box["offset_x"] * um, offset_y_um=box["offset_y"] * um)
            camera["box_roi"] = box
        if self.camera["overlay"] == "line" and self.camera["line"]:
            line = dict(self.camera["line"])
            line["length"] = ((line["x2"] - line["x1"]) ** 2 + (line["y2"] - line["y1"]) ** 2) ** 0.5
            line["length_um"] = line["length"] * um
            camera["line_roi"] = line
        if self.camera["frames"]:
            camera["frames"] = dict(self.camera["frames"])
        rows = len(self.core.state["acq_list"])
        return {
            "camera_window": camera,
            "contrast_window": {"visible": self.open["contrast"], **self.contrast,
                                **({"map": CONTRAST_MAP, "cell": {"width": width / 8, "height": height / 8}}
                                   if self.open["contrast"] else {})},
            "tile_overview": {"visible": self.open["tile_overview"], **self.tile_overview},
            "main_window": {"visible": True, "tab": self.tab, "tabs": TABS, "status_message": "",
                            "progress": {name: {"percent": 0, "text": "0%"} for name in ("acquisition", "time_point", "total")},
                            "time_lapse_estimate": "Est. timelapse duration (hh:mm:ss): 00:00:02",
                            "alignment_toggles": dict(self.toggles)},
            "acquisition_manager": {"visible": self.open["acquisition_manager"], **self.table,
                                    "time_estimate": "00:00:02", "size_estimate": f"{rows * 157} MB"},
            "windows": dict(self.open),
        }

    # --- display requests, accepted by commands.py's own checks ------------------------------
    def set_display(self, args):
        window = args.pop("window", "camera")
        if window == "camera":
            overlay = args.pop("overlay", None) or ("box" if "box_roi" in args else "line" if "line_roi" in args else None)
            if overlay is not None:
                self.camera["overlay"] = overlay if overlay in OVERLAYS else self.camera["overlay"]
            if "box_roi" in args:
                self.camera["box"] = args.pop("box_roi")
            if "line_roi" in args:
                self.camera["line"] = args.pop("line_roi")
            if "colormap" in args:
                self.camera["colormap"], self.camera["colormap_ticks"] = args.pop("colormap"), None
            if "colormap_ticks" in args:
                self.camera["colormap_ticks"] = args.pop("colormap_ticks")
            if "adjust_levels" in args:
                args.pop("adjust_levels")
                self.camera["levels"] = [900.0, 1600.0]                    # a stretch of the synthetic frame
            if args.get("view") == "fit":
                args.pop("view")
                width, height = frame_size(self.core, self.core.cfg)
                self.camera["view"] = {"x": 0.0, "y": 0.0, "width": float(width), "height": float(height)}
            if "frame_index" in args:
                frames = self.camera["frames"]
                if not frames:
                    raise dispatcher.ValidationError("the camera window shows a single frame; frame_index is for a stack "
                                                     "opened with open_image_file")
                if not 0 <= args["frame_index"] < frames["count"]:
                    raise dispatcher.ValidationError(f"frame_index must be 0..{frames['count'] - 1}")
                frames["index"] = args.pop("frame_index")
            self.camera.update(args)
        elif window == "contrast":
            if not self.open["contrast"]:
                raise dispatcher.ValidationError("the contrast window is not open: set_windows {show: ['contrast']} opens it")
            self.contrast.update(args)
        elif window == "tile_overview":
            self.tile_overview.update(args)
        elif window == "main":
            if args["tab"] not in TABS:
                raise dispatcher.ValidationError(f"tab {args['tab']!r} is not one of {TABS}")
            self.tab = args["tab"]

    def set_windows(self, args):
        if "optimizer" in args["show"]:
            raise dispatcher.ValidationError("the optimizer opens with optimize, which sets what it searches")
        if "main" in args["hide"]:
            raise dispatcher.ValidationError("the main window stays: hiding it would hide mesoSPIM")
        for name in args["show"]:
            self.open[name] = True
        for name in args["hide"]:
            self.open[name] = False

    def get_picture(self, args):
        if not self.open[args["window"]]:
            raise dispatcher.ValidationError(f"the {args['window']} window is not open: "
                                             f"set_windows {{show: [{args['window']!r}]}} opens it")
        if "path" in args:
            return {"path": args["path"], "width": 800, "height": 600}
        return {"format": "png", "width": 1, "height": 1, "base64": PNG}

    def open_image_file(self, args):
        self.camera["frames"] = {"count": 3, "index": 0}
        return {"path": args["path"], "shape": [3, 512, 512]}

    def set_table_view(self, args):
        rows = len(self.core.state["acq_list"])
        if "select_rows" in args:
            if any(row >= rows for row in args["select_rows"]):
                raise dispatcher.ValidationError(f"select_rows must be rows 0..{rows - 1}")
            self.table["selected_rows"] = sorted(args["select_rows"])
        if "group" in args:
            if args["group"] == "selected" and len(self.table["selected_rows"]) < 2:
                raise dispatcher.ValidationError("grouping selected rows needs two or more selected rows: select_rows first")
            self.table["grouping"] = args["group"]

    def refresh_processor_chain(self, args):
        return None

    def analyse_psf(self, args):
        """The PSF tool's result for a stack of three beads, as built for the case."""
        beads = [{"bead_id": i + 1, "z": 10.0, "y": y, "x": x, "max_intensity": 5300.0, "fwhm_lateral_um": 1.0,
                  "fwhm_axial_um": 2.0} for i, (y, x) in enumerate(((16.0, 16.0), (16.0, 48.0), (48.0, 32.0)))]
        spread = {"median": None, "std": 0.0, "count": 3}
        return {"path": args["path"], "shape": [21, 64, 64], "beads_found": 3, "beads": beads,
                "fwhm_lateral_um": {**spread, "median": 1.0}, "fwhm_axial_um": {**spread, "median": 2.0}}

    def analyse_field_curvature(self, args):
        channels = [{**channel, "metrics": {"dof_um": 28.5, "sag_x_um": 0.0 if i == 0 else 5.8, "sag_y_um": 0.0,
                                            "ave_sag_um": 0.0 if i == 0 else 3.1, "z_best_um": 100.0, "z_best_index": 10,
                                            "max_contrast": 0.6, "mean_contrast_best_plane": 0.4}}
                    for i, channel in enumerate(args["channels"])]
        return {"field_of_view_mm": [2.4, 2.4], "channels": channels}

    # --- the window features that change the instrument --------------------------------------
    def set_alignment_toggles(self, args):
        for key, value in args.items():
            self.toggles[key] = value
        if "freeze_galvo" in args:
            self.core.state["galvo_l_amplitude"] = 0 if args["freeze_galvo"] else 2.5
        return dict(self.toggles)

    def optimize(self, args):
        """The optimizer's sweep over the simulated instrument: the sharpness peaks where the
        simulator's focus is best (at the current focus without a sample)."""
        if not self.core.frame_queue_display:
            raise dispatcher.ValidationError("the camera window has shown no frame yet: start live first, as for AUTO Focus")
        key = "f_pos" if args["mode"] == "focus" else ("etl_l_offset" if args["mode"] == "etl_offset" else "etl_l_amplitude")
        old = float(self.core.state["position"]["f_pos"] if key == "f_pos" else self.core.state[key])
        error = float(self.core.truth()["focus_error_um"]) if hasattr(self.core, "truth") and key == "f_pos" else 0.0
        best = old - error
        step = 2 * args["range"] / (args["points"] - 1)
        points = [old - args["range"] + i * step for i in range(args["points"])]
        sharpness = [1.0 / (1.0 + ((p - best) / (args["range"] / 3)) ** 2) for p in points]
        self.optimization = {"key": key, "new": best}
        self.open["optimizer"] = True
        return {"mode": args["mode"], "parameter": key, "old": old, "new": best, "points": points, "sharpness": sharpness,
                "waiting_for": "finish_optimization {accept}"}

    def finish_optimization(self, args):
        if self.optimization is None:
            raise dispatcher.ValidationError("there is no optimization result to accept or discard: optimize first")
        found, self.optimization = self.optimization, None
        if args["accept"]:
            if found["key"] == "f_pos":
                self.core.state["position"]["f_pos"] = found["new"]
                self.core.state["position_absolute"]["f_pos"] = found["new"]
            else:
                self.core.state[found["key"]] = found["new"]
        return {"accepted": args["accept"], "value": found["new"] if args["accept"] else None}
