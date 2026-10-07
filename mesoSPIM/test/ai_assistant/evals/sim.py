"""A simulated microscope that behaves over time, for the evaluation (roadmap B1).

The sample is a cluster of spots at a place in stage coordinates. Each frame is rendered from the
instrument's state at that moment: the sample sits where x and y put it in the image (in the
case's coordinate system), it is sharp only at its focus position on f and blurs with the distance
from it, it is sectioned in z, and its brightness follows intensity times exposure up to the
camera's full scale. Drift moves the sample and its focus with time; bleaching dims it with the
light it has received. Both are off unless a case switches them on.

Time is the simulator's own clock, which the assistant reads through its scheduler (A2). It moves
only when something takes time: a poll of a running operation, an exposure, a stage move, an
acquisition, and the operator's and the model's time per turn (the harness). Acquisitions and
time lapses run on it as events: a run holds its state until its planes are taken, a time lapse
starts its points at their interval and is active between them, as Core's are.

A case switches the simulator on with a "sample" in its setup:
    {"x": um, "y": um, "z": um, "f": um,      the stage position at which the sample is centred,
                                              sectioned and in focus; the current one when omitted
     "radius_um": 300, "spots": 40, "seed": 1, "brightness": 20000 (counts at 100% and 20 ms),
     "drift_um_per_min": {"x": .., "y": .., "f": ..}, "bleach": 0.0 (loss per unit of light dose)}
"""
from __future__ import annotations

import math
import time

import numpy as np

from mesoSPIM.src.remote_control import config as rc_config
from mesoSPIM.test.ai_assistant.evals import harness

START = time.mktime((2026, 10, 7, 9, 0, 0, 0, 0, -1))   # a fixed morning, so readouts repeat
ROWS = COLS = 256                     # the rendered frame; the camera's 2048 pixels binned by 8
FULL_SCALE = 65535
BACKGROUND = 100.0
REFERENCE_EXPOSURE_S = 0.02
DEFAULT_EXPOSURE_S = 0.02
FOCUS_BLUR = 0.1                      # um of blur per um of defocus
DEPTH_UM = 500.0                      # the sample's extent in z, as a Gaussian width
POLL_S = 1.0                          # a poll of a running operation
STAGE_SPEED_UM_S = 5000.0
PLANE_OVERHEAD_S = 0.01
ROW_OVERHEAD_S = 1.0


class SimClock:
    """Epoch seconds that pass only when the simulator says."""

    def __init__(self, start=START):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += max(0.0, float(seconds))


class Sample:
    """Spots in a disc, fixed by the seed, around a place in stage coordinates."""

    def __init__(self, spec, position):
        self.x = float(spec.get("x", position["x_pos"]))
        self.y = float(spec.get("y", position["y_pos"]))
        self.z = float(spec.get("z", position["z_pos"]))
        self.f = float(spec.get("f", position["f_pos"]))
        self.radius_um = float(spec.get("radius_um", 300.0))
        self.brightness = float(spec.get("brightness", 20000.0))
        self.drift = {axis: float(v) for axis, v in (spec.get("drift_um_per_min") or {}).items()}
        self.bleach = float(spec.get("bleach", 0.0))
        rng = np.random.default_rng(int(spec.get("seed", 1)))
        count = int(spec.get("spots", 40))
        angle, reach = rng.uniform(0, 2 * math.pi, count), self.radius_um * np.sqrt(rng.uniform(0, 1, count))
        self.spots = np.stack([reach * np.cos(angle), reach * np.sin(angle),          # um right, um up
                               rng.uniform(3.0, 8.0, count), rng.uniform(0.5, 1.0, count)], axis=1)

    def at(self, axis, elapsed_s):
        """Where the sample is on an axis after `elapsed_s` of drift."""
        return getattr(self, axis) + self.drift.get(axis, 0.0) * elapsed_s / 60.0


class SampleInstrument(harness.SimulatedInstrument):
    """The harness's simulated instrument with a sample, frames rendered from its state, and time
    that passes on its own clock (see the module)."""

    timed = True

    def __init__(self, axes=None, frame_seed=0):
        super().__init__()
        self.clock = SimClock()
        self.sample = None                    # placed once the case has set the position
        self.axes = dict(harness.ai.config.DEFAULT_AXES, **(axes or {}))
        self.dose = 0.0                       # light received: intensity fraction times seconds
        self._events = []                     # (time, order, callback): Core's timers, on this clock
        self._order = 0
        self._run = None                      # the acquisition in progress: its generation
        self._noise = np.random.default_rng(frame_seed)
        self.timelapse_tpoints = None
        self.timelapse_interval_sec = None
        self.time_counter = None

    def place_sample(self, spec):
        self.sample = Sample(spec, self.state["position"])

    # --- time ---
    def before_dispatch(self, name):
        """A poll of a running operation takes a moment; anything else only lets due events run."""
        if name == "get_progress":
            self.wait(POLL_S)
        else:
            self.tick()

    def elapsed(self):
        return self.clock() - START

    def after(self, seconds, callback):
        self._order += 1
        self._events.append((self.clock() + seconds, self._order, callback))

    def tick(self):
        """Run every event that is due, in order."""
        while True:
            due = sorted(e for e in self._events if e[0] <= self.clock())
            if not due:
                break
            self._events.remove(due[0])
            due[0][2]()
        if self.state["state"] in rc_config.LIVE_STATES:
            self.frame_queue_display.append(self.render())      # live shows the sample as it is now

    def wait(self, seconds):
        """Let `seconds` pass, running what falls due on the way, in order."""
        end = self.clock() + seconds
        while True:
            upcoming = [e[0] for e in self._events if e[0] <= end]
            self.clock.now = max(self.clock(), min(upcoming)) if upcoming else end
            self.tick()
            if not upcoming:
                return

    # --- the picture ---
    def _exposure(self):
        try:
            return float(self.state["camera_exposure_time"])
        except KeyError:
            return DEFAULT_EXPOSURE_S

    def um_per_pixel(self):
        pixel_um = self.cfg.pixelsize.get(self.state["zoom"], 1.0)
        return self.cfg.camera_parameters["x_pixels"] * pixel_um / COLS

    def offset_um(self):
        """Where the sample's centre is in the image, in um right of and above the centre."""
        position, elapsed = self.state["position"], self.elapsed()
        right = 1.0 if self.axes["x"] == "right" else -1.0
        up = 1.0 if self.axes["y"] == "up" else -1.0
        return (right * (position["x_pos"] - self.sample.at("x", elapsed)),
                up * (position["y_pos"] - self.sample.at("y", elapsed)))

    def focus_error_um(self):
        return self.state["position"]["f_pos"] - self.sample.at("f", self.elapsed())

    def render(self, noise=True):
        """The camera frame for the state now."""
        scale = self.um_per_pixel()
        right, up = self.offset_um()
        position = self.state["position"]
        section = math.exp(-((position["z_pos"] - self.sample.at("z", self.elapsed())) / DEPTH_UM) ** 2)
        light = (float(self.state["intensity"]) / 100.0) * (self._exposure() / REFERENCE_EXPOSURE_S)
        signal = self.sample.brightness * light * section * math.exp(-self.sample.bleach * self.dose)
        blur_px = FOCUS_BLUR * abs(self.focus_error_um()) / scale
        rows, cols = np.mgrid[0:ROWS, 0:COLS]
        frame = np.full((ROWS, COLS), BACKGROUND, dtype=np.float64)
        for dx, dy, size_um, weight in self.sample.spots:
            col = COLS / 2 + (right + dx) / scale
            row = ROWS / 2 - (up + dy) / scale
            sigma = math.hypot(size_um / scale, blur_px)
            if not (-4 * sigma < col < COLS + 4 * sigma and -4 * sigma < row < ROWS + 4 * sigma):
                continue
            peak = signal * weight * (size_um / scale / sigma) ** 2      # a blurred spot spreads its light
            frame += peak * np.exp(-((rows - row) ** 2 + (cols - col) ** 2) / (2 * sigma ** 2))
        if noise:
            frame += self._noise.normal(0.0, 1.0, frame.shape) * np.sqrt(frame)
        return np.clip(frame, 0, FULL_SCALE).astype(np.uint16)

    def truth(self):
        """What the scoring may know and the model may not: the sample's place, the focus error and
        the exposure of a noise-free frame now."""
        right, up = self.offset_um()
        frame = self.render(noise=False).astype(np.float64)
        return {"off_centre_um": round(math.hypot(right, up), 1), "offset_um": [round(right, 1), round(up, 1)],
                "focus_error_um": round(self.focus_error_um(), 1),
                "saturated_fraction": round(float(np.mean(frame >= FULL_SCALE)), 4),
                "peak_fraction": round(float(np.percentile(frame, 99.9)) / FULL_SCALE, 3),
                "bleached": round(1.0 - math.exp(-self.sample.bleach * self.dose), 3),
                "elapsed_s": round(self.elapsed(), 1)}

    # --- what takes time ---
    def snap(self, write_flag=True):
        self._record("snap", write_flag=write_flag)
        self.frame_queue_display.append(self.render())
        self._expose(1)
        self.clock.advance(self._exposure())

    def _expose(self, planes):
        self.dose += planes * float(self.state["intensity"]) / 100.0 * self._exposure()

    def _apply_move(self, moves, suffix):
        before = dict(self.state["position"])
        super()._apply_move(moves, suffix)
        distance = max(abs(self.state["position"][k] - before[k]) for k in before)
        self.clock.advance(distance / STAGE_SPEED_UM_S)

    def start(self, *args, **kwargs):
        """Core's start runs the run to the end before it returns; here it returns at once (in
        idle, as Core's does) and the run enters its state as the next event, so it takes its
        time on the clock."""
        run_state = self.state["state"]
        super().start(*args, **kwargs)
        rows = list(self.state["acq_list"])
        row = kwargs.get("row")
        if row is not None:
            rows = rows[row:row + 1]
        planes = sum(_planes(r) for r in rows)
        duration = planes * (self._exposure() + PLANE_OVERHEAD_S) + ROW_OVERHEAD_S * len(rows)
        self._run = generation = object()
        self._planes_pending = planes

        def enter():
            if self._run is generation:
                self.state["state"] = run_state
                self.after(duration, lambda: self._end_run(generation))
        self.after(0, enter)

    def _end_run(self, generation):
        if self._run is not generation:
            return                            # stopped meanwhile
        self._run = None
        self._expose(self._planes_pending)
        self.state["state"] = "idle"
        self.sig_finished.emit()
        if self.timelapse_active:
            self.after(self.timelapse_interval_sec or 0, self._time_point)

    def stop(self, *args, **kwargs):
        running = self.state["state"] != "idle"
        super().stop(*args, **kwargs)
        self._run = None
        if running:
            self.sig_finished.emit()

    def run_time_lapse(self, tpoints=1, time_interval_sec=60, **_):
        self._record("run_time_lapse", tpoints=tpoints, time_interval_sec=time_interval_sec)
        self.timelapse_active = True
        self.timelapse_tpoints, self.timelapse_interval_sec, self.time_counter = tpoints, time_interval_sec, 0
        self._time_point()

    def _time_point(self):
        if not self.timelapse_active:
            return
        if self.time_counter >= self.timelapse_tpoints:
            self.timelapse_active = False
            self.sig_time_lapse_finished.emit()
            return
        self.sig_run_timepoint.emit(self.time_counter)
        self.time_counter += 1
        self.state["state"] = "run_acquisition_list"
        self.start()

    def stop_time_lapse(self, *args, **kwargs):
        super().stop_time_lapse(*args, **kwargs)
        self._events = [e for e in self._events if getattr(e[2], "__name__", "") != "_time_point"]
        if self._run is None:
            self.sig_time_lapse_cancelled.emit()


def _planes(row):
    try:
        return int(row["planes"])
    except (KeyError, TypeError, ValueError):
        pass
    try:
        return abs(round((float(row["z_end"]) - float(row["z_start"])) / float(row["z_step"]))) + 1
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 100
