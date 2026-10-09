"""Drive the real mesoSPIM demo GUI's AI Assistant tab, step 2 a-g of the Windows demo test.

Starts mesoSPIM in demo mode in this process (the same steps as mesoSPIM_Control.main, the config
given instead of chosen), shows the window, and works the Remote Control and AI Assistant tabs
through their own widgets and slots, with the model the Gemini preset names. Every check reads
Core's state, the main window's widgets, the tab's transcript and request line.

    python drive_gui.py --steps a,b,c,d,e,f,g [--config path] [--probe folder]   # step 2 (and m: step 4)
    python drive_gui.py --steps r                                                 # every setting by tool shows in the window
    python drive_gui.py --serve TCP|MCP                                           # a demo for step 3

The key comes from GEMINI_API_KEY in the environment only. Refuses anything but DemoStage.
"""
import argparse
import logging
import os
import sys
import time

# This file lives in <repo>/docs/test-reports/evidence/tools/.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *[".."] * 4))
PACKAGE = os.path.join(REPO, "mesoSPIM")
sys.path.insert(0, REPO)
CALLER_CWD = os.getcwd()   # a relative --config or --probe is the caller's, not mesoSPIM's
os.chdir(PACKAGE)          # mesoSPIM loads gui/*.ui relative to the working directory

from PyQt5 import QtCore, QtWidgets  # noqa: E402

from mesoSPIM.mesoSPIM_Control import dark_mode_check, get_logger  # noqa: E402
from mesoSPIM.src.plugins.manager import PluginRegistry  # noqa: E402
from mesoSPIM.src.utils.config_loader import load_config_from_file  # noqa: E402

RESULTS = []


def say(text=""):
    print(text, flush=True)


def record(step, ok, seen):
    RESULTS.append((step, ok, seen))
    say(f"  {'PASS' if ok else 'FAIL'} {step}: {seen}")


def pump(seconds):
    end = time.monotonic() + seconds
    while True:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)
        if time.monotonic() >= end:
            return
        time.sleep(0.02)


def wait_until(condition, seconds):
    end = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > end:
            return False
        pump(0.1)
    return True


class Counter(logging.Handler):
    """Counts log lines that contain a text: trips of the focus to the objective exchange position."""

    def __init__(self, text):
        super().__init__(logging.DEBUG)
        self.text, self.count = text, 0

    def emit(self, record):
        if self.text in record.getMessage():
            self.count += 1


class Driver:
    def __init__(self, window, probe):
        self.window = window
        self.core = window.core
        self.tab = window.ai_assistant
        self.rc = window.remote_control
        self.chat = self.tab.chat_window
        self.probe = probe
        self.requests_seen = []       # every distinct request-line text
        self.state_requests = []      # what the main window sent to Core
        window.sig_state_request.connect(lambda request: self.state_requests.append(dict(request)))

    # --- reading ---
    def pos(self, axis):
        return self.core.state["position"][f"{axis}_pos"]

    def request_line(self):
        if self.chat.request_label.isHidden():
            return None
        return self.chat.request_label.text()

    def sample_request(self):
        text = self.request_line()
        if text and (not self.requests_seen or self.requests_seen[-1] != text):
            self.requests_seen.append(text)
            say("    request line: " + text.replace("\n", " | "))

    def transcript(self):
        return self.chat.output.toPlainText()

    def last_turn(self):
        turns = [b for b in self.tab._blocks if isinstance(b, dict)]
        return turns[-1] if turns else {"tools": [], "reply": None, "error": None}

    # --- acting ---
    def ask(self, text, answer=lambda label: False, timeout=240):
        """Type a message and press Enter; answer each Run / Cancel question with `answer`."""
        say(f"\n> {text}")
        asked = []
        self.chat.input.setText(text)
        self.tab.on_submit()
        start = time.monotonic()
        while self.tab._running:
            if not self.chat.confirm_run.isHidden():
                label = self.chat.confirm_label.text()
                allowed = answer(label)
                asked.append((label, allowed))
                say(f"    Run / Cancel: {label} -> {'Run' if allowed else 'Cancel'}")
                (self.chat.confirm_run if allowed else self.chat.confirm_cancel).click()
            self.sample_request()
            pump(0.05)
            if time.monotonic() - start > timeout:
                say("    turn timed out: Cancel prompt")
                self.tab.on_interrupt()
                wait_until(lambda: not self.tab._running, 30)
        turn = self.last_turn()
        for name, args in turn["tools"]:
            say(f"    tool: {name}({args})")
        if turn.get("error"):
            say(f"    error: {turn['error']}")
        say(f"    reply: {(turn.get('reply') or '').strip()[:600]}")
        self.sample_request()
        return turn, asked

    def connect(self):
        self.tab.on_connect()
        ok = wait_until(lambda: self.tab._state == "ready", 30)
        say(f"  status: {self.tab.status_label.text()}")
        return ok

    def disconnect(self):
        self.tab.on_disconnect()
        pump(0.5)


def step_setup(d):
    say("\n== setup: Remote Control TCP, then the AI Assistant ==")
    d.rc.RemoteControlModeComboBox.setCurrentText("TCP")
    d.rc.start()
    running = wait_until(lambda: d.rc.running, 15)
    record("setup: TCP starts", running, d.rc.RemoteControlStatusLabel.text())
    d.tab.on_connect()
    pump(1)
    refused = d.tab._state != "ready"
    record("setup: Connect while TCP runs is refused", refused, d.tab.status_label.text())
    if not refused:
        d.disconnect()
    d.rc.stop()
    pump(1)
    ok = d.connect()
    record("setup: Gemini connects after TCP stops", ok and "gemini" in d.tab.status_label.text().lower(),
           d.tab.status_label.text())
    return ok


def step_a(d):
    say("\n== a: a simple request ==")
    x0 = d.pos("x")
    turn, asked = d.ask("move x by 100 um")
    moved = wait_until(lambda: abs(d.pos("x") - (x0 + 100)) < 0.5, 20)
    shown = d.window.X_Position_Indicator.text()
    record("a: x moved by 100 um, no question", moved and not asked,
           f"x {x0} -> {d.pos('x')}, display '{shown}', questions {len(asked)}, tools {[n for n, _ in turn['tools']]}")


def step_b(d):
    say("\n== b: a value the operator did not give ==")
    intensity0 = d.core.state["intensity"]
    x0 = d.pos("x")
    turn, asked = d.ask("Make the laser a bit brighter; choose the new intensity yourself.")
    pump(2)
    kept = d.core.state["intensity"] == intensity0 and d.pos("x") == x0
    record("b: Run / Cancel asked and Cancel refused it", bool(asked) and kept,
           f"questions {[label for label, _ in asked]}, intensity {intensity0} -> {d.core.state['intensity']}, "
           f"tools {[n for n, _ in turn['tools']]}")


def step_c(d):
    say("\n== c: the coordinate system box ==")
    from mesoSPIM.src.ai_assistant import assistant as ai
    from mesoSPIM.src.ai_assistant import config as aiconfig
    box = d.tab.axis_boxes["x"]
    record("c: the box is locked while connected", not box.isEnabled(), f"enabled={box.isEnabled()}")
    before = box.currentText()
    other = next(c for c in aiconfig.AXIS_CHOICES["x"] if c != before)
    d.disconnect()
    box.setCurrentText(other)
    d.connect()
    worker = d.tab._worker
    prompt = ai.build_system_prompt(profile=worker._profile, axes=worker.axes)
    in_prompt = f"toward the {other} of the image" in prompt
    record("c: the worker holds the new axes and the prompt follows", worker.axes["x"] == other and in_prompt,
           f"worker x={worker.axes['x']}, prompt says 'toward the {other}': {in_prompt}")
    turn, asked = d.ask("Without moving anything: in the image, which way does a positive x move carry the sample? "
                        "Answer with one word.")
    reply = (turn.get("reply") or "").lower()
    record("c: the agent was rebuilt with them and the model answers by them",
           worker._agent_axes == worker.axes and other in reply and not turn["tools"],
           f"agent axes x={(worker._agent_axes or {}).get('x')}, reply '{reply.strip()[:80]}'")
    d.disconnect()
    box.setCurrentText(before)
    d.connect()
    record("c: put back", d.tab._worker.axes["x"] == before, f"x={d.tab._worker.axes['x']}")


PROBE_LIMIT_BYTES = 5 * 1024 ** 3


def trim_probe(probe, limit=PROBE_LIMIT_BYTES):
    """Keep the probe folder at most `limit` bytes: the oldest files go first."""
    if not os.path.isdir(probe):
        return
    files = []
    for root, _dirs, names in os.walk(probe):
        for name in names:
            path = os.path.join(root, name)
            try:
                files.append((os.path.getmtime(path), os.path.getsize(path), path))
            except OSError:
                pass
    total = sum(size for _, size, _ in files)
    for _mtime, size, path in sorted(files):
        if total <= limit:
            return
        try:
            os.remove(path)
            total -= size
            say(f"  probe over {limit // 1024 ** 3} GB: removed {path}")
        except OSError:
            pass                   # still being written: the next pass takes it


def dispatch(d, name, args):
    """A Remote Control command through the assistant's own Acceptor, waited out, off the GUI thread."""
    import threading
    from mesoSPIM.src.ai_assistant import assistant as ai
    from mesoSPIM.src.remote_control.dispatcher import ACTION, COMMANDS, WAIT
    kind = COMMANDS[name].kind
    out = {}
    thread = threading.Thread(target=lambda: out.update(result=ai.dispatch_and_wait(
        d.core._assistant_acceptor, name, args, WAIT if kind == ACTION else kind, threading.Event())))
    thread.start()
    wait_until(lambda: not thread.is_alive(), 60)
    return out.get("result")


def prepare_probe(d):
    """Snaps and the acquisition go to the probe folder, not the demo config's D:/tmp/ (no such folder here)."""
    os.makedirs(d.probe, exist_ok=True)
    # What the main window's Choose snap folder does (choose_snap_folder), without its dialog:
    # snap_folder is not a state request Core takes.
    d.window.state["snap_folder"] = d.probe
    d.window.SnapFolderIndicator.setText(d.probe)
    pump(1)
    say(f"  snap folder: {d.core.state['snap_folder']}")


def acq_rows(d):
    return list(d.core.state["acq_list"])


def step_d(d):
    say("\n== d: run the acquisition list and look once it is done ==")
    prepare_probe(d)
    rows = acq_rows(d)
    if not rows:
        record("d: the demo acquisition list has a row", False, "empty")
        return
    # One small row (three planes) into the probe folder, so the run cannot write anywhere else.
    name = f"step_d_{time.strftime('%H%M%S')}.tif"
    row = {key: value for key, value in dict(rows[0]).items()}
    row.update(folder=d.probe, filename=name, z_start=0, z_end=20, z_step=10)
    dispatch(d, "set_acquisition_list", {"acquisitions": [row], "selected_row": 0})
    pump(1)
    rows = acq_rows(d)
    record("d: the list is one small row in the probe folder",
           len(rows) == 1 and os.path.normcase(os.path.normpath(rows[0]["folder"])) == os.path.normcase(os.path.normpath(d.probe)),
           f"{len(rows)} row(s); folder {rows[0]['folder']}, filename {rows[0]['filename']}, "
           f"z {rows[0]['z_start']}..{rows[0]['z_end']} step {rows[0]['z_step']}")
    d.requests_seen.clear()
    turn, asked = d.ask("Run the acquisition list and look once it is done.", answer=lambda label: True)
    seen_wait = any("waiting until done" in t for t in d.requests_seen)
    record("d: the request line shows turns, tokens and the wait", seen_wait and any("tokens" in t for t in d.requests_seen),
           " || ".join(t.replace("\n", " | ") for t in d.requests_seen[-3:]))
    fired = wait_until(lambda: "continues:" in d.transcript(), 300)
    if fired:
        wait_until(lambda: d.tab._running, 10)
        wait_until(lambda: not d.tab._running, 240)
    turn = d.last_turn()
    written = os.path.exists(os.path.join(d.probe, name))
    record("d: the continuation fired after the run ended", fired,
           f"file {name} written {written}; continuation tools {[n for n, _ in turn['tools']]}; "
           f"reply '{(turn.get('reply') or '').strip()[:200]}'")
    plans = [t for t in d.requests_seen if "[" in t and "]" in t.split("\n", 1)[-1]]
    say(f"  plan shown: {bool(plans)}")
    d.sample_request()


WAIT_PROMPT = "Wait 30 seconds, then tell me the x position."


def a_waiting_request(d):
    """A request whose wait is pending; the model sometimes schedules instead, so ask again."""
    for prompt in (WAIT_PROMPT, "Use the wait tool to wait 30 seconds, then tell me the x position."):
        turn, asked = d.ask(prompt)
        if d.tab._worker.requests.waiting is not None:
            return True, d.request_line()
        d.tab.scheduler.clear()
    return False, d.request_line()


def step_e(d):
    say("\n== e: what ends a request ==")
    enders = [("the request line's Cancel", lambda: d.chat.request_cancel.click()),
              ("Stop microscope", lambda: d.chat.stop_button.click()),
              ("Clear context", lambda: d.chat.clear_button.click()),
              ("Disconnect", lambda: d.disconnect())]
    for label, end in enders:
        if d.tab._state != "ready":
            d.connect()
        waiting, line = a_waiting_request(d)
        say(f"  waiting: {waiting}, line: {(line or '').replace(chr(10), ' | ')}")
        requests = d.tab._worker.requests
        end()
        pump(1)
        # The request itself is ended; on screen the line is hidden, or the whole window after Disconnect.
        shown = d.request_line() is not None and d.chat.isVisible()
        gone = requests.open() is None and not shown
        blocks = len(d.tab._blocks)
        pump(40)
        late = [b for b in d.tab._blocks[blocks:] if isinstance(b, str) and "continues:" in b]
        record(f"e: {label} ends the waiting request", waiting and gone and not late,
               f"waiting before {waiting}, line hidden after {gone}, continuations after it {len(late)}")
    if d.tab._state != "ready":
        d.connect()


def step_f(d):
    say("\n== f: a schedule ==")
    prepare_probe(d)
    snaps = lambda: len([f for f in os.listdir(d.probe) if f.lower().endswith((".tif", ".tiff"))])  # noqa: E731
    before = snaps()
    turn, asked = d.ask("Take a snap every minute for three minutes.")
    listing = d.tab.scheduler.listing()
    say(f"  schedules: {listing}")
    fired = lambda: d.transcript().count("Scheduled: ")  # noqa: E731
    two = wait_until(lambda: fired() >= 2 and not d.tab._running, 150)
    record("f: scheduled turns fire", two, f"{fired()} scheduled turns, {snaps() - before} new snaps")
    d.chat.stop_button.click()
    wait_until(lambda: not d.tab._running, 30)
    count = fired()
    pump(75)
    record("f: none after Stop microscope", fired() == count and d.tab.scheduler.listing() == [],
           f"scheduled turns {count} -> {fired()}, schedules left {d.tab.scheduler.listing()}")


def step_g(d):
    say("\n== g: combo boxes: a change Core makes is not sent back ==")
    w = d.window
    exchange = d.core.cfg.stage_parameters.get("f_objective_exchange")
    trips = Counter(f"Moving to f_abs: {exchange}")
    logging.getLogger().addHandler(trips)
    boxes = {"zoom": w.ZoomComboBox, "filter": w.FilterComboBox, "laser": w.LaserComboBox, "shutterconfig": w.ShutterComboBox}
    start = {key: d.core.state[key] for key in boxes}
    say(f"  start: {start}")

    def choose(key):
        return next(box_text for box_text in (boxes[key].itemText(i) for i in range(boxes[key].count()))
                    if box_text != start[key])

    target = {key: choose(key) for key in boxes}
    d.state_requests.clear()
    trips.count = 0
    turn, asked = d.ask(f"Set the zoom to {target['zoom']}.")
    wait_until(lambda: d.core.state["zoom"] == target["zoom"] and boxes["zoom"].currentText() == target["zoom"], 30)
    pump(3)
    echoed = [r for r in d.state_requests if "zoom" in r]
    record("g: a zoom change by the assistant is not sent back, one trip to the exchange position",
           d.core.state["zoom"] == target["zoom"] and not echoed and trips.count == 1,
           f"zoom {d.core.state['zoom']}, box '{boxes['zoom'].currentText()}', requests from the window {echoed}, "
           f"exchange trips {trips.count}")
    d.state_requests.clear()
    turn, asked = d.ask(f"Switch the filter to {target['filter']}, the laser to {target['laser']} and the shutter "
                        f"configuration to {target['shutterconfig']}.")
    pump(3)
    keys = ("filter", "laser", "shutterconfig")
    echoed = [r for r in d.state_requests if any(k in r for k in keys)]
    record("g: filter, laser and shutter set by the assistant are not sent back",
           all(d.core.state[k] == target[k] == boxes[k].currentText() for k in keys) and not echoed,
           f"state {[d.core.state[k] for k in keys]}, boxes {[boxes[k].currentText() for k in keys]}, echoes {echoed}")
    # The operator's own change: one request per box, one trip for the zoom.
    d.state_requests.clear()
    trips.count = 0
    for key in boxes:
        boxes[key].setCurrentText(start[key])
        wait_until(lambda: d.core.state[key] == start[key], 30)
    pump(3)
    per_key = {key: sum(1 for r in d.state_requests if key in r) for key in boxes}
    record("g: the operator's own box changes are sent once each, one trip for the zoom",
           all(v == 1 for v in per_key.values()) and trips.count == 1 and all(d.core.state[k] == start[k] for k in boxes),
           f"requests per box {per_key}, exchange trips {trips.count}, state back {[d.core.state[k] for k in boxes]}")
    logging.getLogger().removeHandler(trips)


def step_m(d):
    """Step 4, E1: the setting as read from the config, the prompt's paragraph, and "centre the sample"
    with an operator who cancels every question."""
    from mesoSPIM.src.ai_assistant import assistant as ai
    from mesoSPIM.src.ai_assistant import config as aiconfig
    configured = bool(getattr(d.core.cfg, aiconfig.MEASURED_VALUES_CONFIG_KEY, False))
    worker = d.tab._worker
    say(f"\n== m: measured values (E1), config says {configured} ==")
    prompt = ai.build_system_prompt(profile=worker._profile, axes=worker.axes, measured=worker.measured_values)
    paragraph = "# Measured values" in prompt
    record(f"m: setting read ({configured}) and the prompt follows",
           worker.measured_values == configured and paragraph == configured,
           f"worker.measured_values={worker.measured_values}, paragraph in prompt {paragraph}, prompt {len(prompt)} chars")
    prepare_probe(d)
    start = {axis: d.pos(axis) for axis in ("x", "y", "z", "f")}
    turn, asked = d.ask("Centre the sample.", timeout=300)
    if d.tab._worker.requests.waiting is not None or "continues:" in d.transcript():
        wait_until(lambda: not d.tab._running and d.tab._worker.requests.open() is None, 240)
    end = {axis: d.pos(axis) for axis in ("x", "y", "z", "f")}
    tools = [n for b in d.tab._blocks if isinstance(b, dict) for n, _ in b["tools"]]
    moved = {axis: round(end[axis] - start[axis], 1) for axis in end if end[axis] != start[axis]}
    say(f"  questions {[label for label, _ in asked]}")
    say(f"  tools over the request {tools}; moved {moved}")
    errors = [b.get("error") for b in d.tab._blocks if isinstance(b, dict) and b.get("error")]
    record("m: centre the sample: nothing breaks", not errors and d.tab._state == "ready",
           f"errors {errors}; questions {len(asked)}; moved {moved or 'nothing'}; "
           f"reply '{(d.last_turn().get('reply') or '').strip()[:300]}'")


def step_r(d):
    """Every setting made by tool (no model: the Remote Control commands through the tab's acceptor)
    shows in the main window's widget within READ_BACK_S, and the window sends nothing back to Core."""
    from mesoSPIM.src.remote_control import config as rc_config
    say("\n== r: every setting made by tool shows in the window ==")
    w, cfg, state = d.window, d.core.cfg, d.core.state

    def other(options, current):
        return next(option for option in options if option != current)

    start = {key: state[key] for key in ("camera_exposure_time", "camera_binning", "camera_display_live_subsampling",
                                         "intensity", "etl_l_offset", "galvo_l_frequency", "galvo_amp_scale_w_zoom",
                                         "zoom", "laser", "filter", "shutterconfig")}
    target = {"camera_exposure_time": round(start["camera_exposure_time"] * 2, 4),
              "camera_binning": other(list(cfg.binning_dict), start["camera_binning"]),
              "camera_display_live_subsampling": other(cfg.camera_parameters["subsampling"], start["camera_display_live_subsampling"]),
              "intensity": 20 if start["intensity"] != 20 else 30,
              "etl_l_offset": round(start["etl_l_offset"] + 0.1, 3),
              "galvo_l_frequency": round(start["galvo_l_frequency"] + 1, 2),
              "galvo_amp_scale_w_zoom": not start["galvo_amp_scale_w_zoom"],
              "zoom": other(list(cfg.zoomdict), start["zoom"]),
              "laser": other(list(cfg.laserdict), start["laser"]),
              "filter": other(list(cfg.filterdict), start["filter"]),
              "shutterconfig": other(list(cfg.shutteroptions), start["shutterconfig"])}
    # (what the tool is called, its arguments, the widget's reading, what it should read)
    probes = lambda values: [  # noqa: E731
        ("set_camera", {k: values[k] for k in ("camera_exposure_time", "camera_binning", "camera_display_live_subsampling")},
         lambda: (w.CameraExposureTimeSpinBox.value(), w.BinningComboBox.currentText(), w.LiveSubSamplingComboBox.currentText()),
         (values["camera_exposure_time"] * 1000, values["camera_binning"], str(values["camera_display_live_subsampling"]))),
        ("set_intensity", {"intensity": values["intensity"]},
         lambda: (w.LaserIntensitySlider.value(), w.LaserIntensitySpinBox.value()), (values["intensity"],) * 2),
        ("set_etl", {"etl_l_offset": values["etl_l_offset"]}, lambda: w.LeftETLOffsetSpinBox.value(), values["etl_l_offset"]),
        ("set_galvo", {"galvo_l_frequency": values["galvo_l_frequency"]}, lambda: w.GalvoFrequencySpinBox.value(), values["galvo_l_frequency"]),
        ("set_state", {"settings": {"galvo_amp_scale_w_zoom": values["galvo_amp_scale_w_zoom"]}},
         lambda: w.checkBoxScaleWZoom.isChecked(), values["galvo_amp_scale_w_zoom"]),
        ("set_zoom", {"zoom": values["zoom"]}, lambda: w.ZoomComboBox.currentText(), values["zoom"]),
        ("set_laser", {"laser": values["laser"]}, lambda: w.LaserComboBox.currentText(), values["laser"]),
        ("set_filter", {"filter": values["filter"]}, lambda: w.FilterComboBox.currentText(), values["filter"]),
        ("set_shutterconfig", {"shutterconfig": values["shutterconfig"]}, lambda: w.ShutterComboBox.currentText(), values["shutterconfig"]),
    ]

    def close(shown, expected):
        if isinstance(shown, tuple):
            return len(shown) == len(expected) and all(close(a, b) for a, b in zip(shown, expected))
        return abs(shown - expected) < 1e-6 if isinstance(shown, float) else shown == expected

    for values, label in ((target, "r"), (start, "r: put back")):
        d.state_requests.clear()
        for name, args, reading, expected in probes(values):
            result = dispatch(d, name, args)
            followed = wait_until(lambda: close(reading(), expected), rc_config.READ_BACK_S + 2)
            record(f"{label}: {name} shows in the window", followed,
                   f"{args} -> widget reads {reading()!r}, expected {expected!r}; result {result}")
        pump(3)
        record(f"{label}: the window sent nothing back to Core", not d.state_requests, f"requests {d.state_requests}")


STEPS = {"m": step_m, "a": step_a, "b": step_b, "c": step_c, "d": step_d, "e": step_e, "f": step_f, "g": step_g, "r": step_r}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.path.join(PACKAGE, "config", "demo_config.py"))
    parser.add_argument("--probe", default=os.path.join(os.path.dirname(REPO), "mesospim-probe"),
                        help="folder for snaps and acquisitions (created; capped at 5 GB, oldest removed)")
    parser.add_argument("--steps", default="a,b,c,d,e,f,g")
    parser.add_argument("--keep-open", action="store_true", help="leave the window open at the end")
    parser.add_argument("--serve", choices=("TCP", "MCP"), help="no steps: start this transport and keep running")
    arguments = parser.parse_args()
    arguments.config = os.path.join(CALLER_CWD, arguments.config)
    arguments.probe = os.path.join(CALLER_CWD, arguments.probe)
    if not arguments.serve and not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is not set")

    cfg = load_config_from_file(arguments.config)
    if cfg.stage_parameters.get("stage_type") != "DemoStage":
        raise SystemExit(f"refused: stage_type is {cfg.stage_parameters.get('stage_type')!r}, not DemoStage")
    get_logger(cfg, PACKAGE)
    QtCore.QThread.currentThread().setObjectName("MainThread")
    app = QtWidgets.QApplication(sys.argv)
    dark_mode_check(cfg, app)
    PluginRegistry(cfg)
    from mesoSPIM.src.mesoSPIM_MainWindow import mesoSPIM_MainWindow
    window = mesoSPIM_MainWindow(PACKAGE, cfg, "mesoSPIM Main Window (driven demo)")
    window.show()
    pump(8)
    stage = type(window.core.serial_worker.stage).__name__
    say(f"config {arguments.config}; stage {stage}")
    if stage != "mesoSPIM_DemoStage":
        raise SystemExit("refused: the stage is not DemoStage")

    d = Driver(window, arguments.probe)
    os.makedirs(arguments.probe, exist_ok=True)
    trimmer = QtCore.QTimer()
    trimmer.timeout.connect(lambda: trim_probe(arguments.probe))
    trimmer.start(5000)
    if arguments.serve:
        prepare_probe(d)
        d.rc.RemoteControlModeComboBox.setCurrentText(arguments.serve)
        d.rc.start()
        ok = wait_until(lambda: d.rc.running, 15)
        say(f"SERVING {arguments.serve}: {ok}, {d.rc.RemoteControlStatusLabel.text()}")
        app.exec_()
        os._exit(0)
    try:
        if step_setup(d):
            for key in arguments.steps.split(","):
                try:
                    STEPS[key.strip()](d)
                except Exception as error:  # one step's crash must not hide the others
                    logging.exception("step %s", key)
                    record(f"{key}: crashed", False, repr(error))
    finally:
        say("\n== summary ==")
        for step, ok, seen in RESULTS:
            say(f"{'PASS' if ok else 'FAIL'}  {step}")
        say(f"{sum(ok for _, ok, _ in RESULTS)} of {len(RESULTS)} checks passed")
        if arguments.keep_open:
            app.exec_()
        else:
            d.disconnect()
            window.close()
            pump(2)
    os._exit(0)


if __name__ == "__main__":
    main()
