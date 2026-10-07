"""Behavioural evaluation of the AI Assistant.

Fixed scenario prompts (cases.json) run through the real agent, tools and dispatcher against a
simulated instrument; each run is recorded as a trace and scored against what the scenario expects.
This is not a unit test: a language model decides, so a run costs API calls and two runs may
differ. It answers a different question than the code tests do: does the assistant, with this
model and this prompt, do what an operator expects, including on refusals, limits and ambiguity.

A case:
    {"id": ..., "category": ..., "prompt": ... | "prompts": [...], "profile": "Regular"|"Full",
     "setup": {"state": "live", "timelapse_active": true, "schedules": [...], "frames": [one per turn],
               "axes": {"x": "left"}, ...}, "answer": true|false (the Run / Cancel
     answer), "expect": {...}}
Setup keys are state keys of the simulated instrument (state, intensity, snap_folder, ...);
timelapse_active is the Core attribute a GUI time lapse sets; frame chooses a synthetic camera
frame ("spots", "ring") whose content only a model that looks at the picture can report. A case
with "memory": n keeps only the last n turns in the model's history, so what it needs from
earlier turns must come from the session store (recall_turn, search_history).
Expectations:
    calls          tool names that must have been called
    calls_any      at least one of these
    not_calls      tool names that must not have been called
    max_calls      {tool: n}: called at most n times (no retrying a refused value)
    min_calls      {tool: n}: called at least n times (a second look after a change)
    max_tool_calls n: at most n tool calls in all (a greeting needs none)
    args           {tool: {arg: value}}: some call of the tool carried these arguments
    state          {dotted.path: value}: the instrument's state afterwards; acquisition_rows is the
                   installed list's length, and a number reaches into a list (acq_list.0.zoom)
    core_calls     methods the instrument must have seen (e.g. "start")
    core_calls_not methods it must not have seen
    core_call_counts {method: [low, high]}: how often the instrument saw it (runs in a time lapse)
    confirm        the confirm-first command the operator was asked about
    asks           the reply asks for what is missing (a question, "please specify ...") and nothing
                   was changed
    no_mutations   only reads were called
    reply_mentions_any  one of these strings appears in a reply (case-insensitive)
    reply_mentions_none none of these strings appears in a reply (no leaked manual text)
    truth          {name_max|name_min: bound}: the simulator's truth afterwards (sim.py), by size:
                   off_centre_um_max, focus_error_um_max, saturated_fraction_max, peak_fraction_min
Every case also fails when a reply quotes the <microscope_state> block, which the manual forbids,
or when a snap is called right before a look, which snaps by itself: a wasted round trip.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from mesoSPIM.test.remote_control import conftest  # noqa: F401  (the Qt substitute: headless, synchronous)
from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant.requests import Requests
from mesoSPIM.src.remote_control import config as rc_config
from mesoSPIM.src.remote_control import dispatcher as dispatcher
from mesoSPIM.src.remote_control import servers as servers
from mesoSPIM.src.remote_control.dispatcher import COMMANDS, READ
from mesoSPIM.test.remote_control.support.fakes import RecordingCore

CASES_FILE = Path(__file__).with_name("cases.json")
FINISH_AT_ONCE = (rc_config.MILESTONE_FINISHED, rc_config.MILESTONE_TIMELAPSE, rc_config.MILESTONE_PREVIEW)
STATE_PATHS = ("state", "position.x_pos", "position.y_pos", "position.z_pos", "position.f_pos", "position.theta_pos",
               "laser", "intensity", "filter", "zoom", "shutterconfig")
WAIT_CAP_S = 2.0   # a WAIT that no simulated signal ends (live) returns "still_running" after this
TURN_S = 5.0       # simulated seconds a turn takes on a timed instrument: the operator and the model
SCHEDULED_TURNS_MAX = 200
CONTINUE_POLL_S = 5.0   # simulated seconds between the checks of a pending wait, as the tab's tick
RETRY_WAIT_S = 20.0   # a provider error is mostly a per-minute rate limit: wait it out before retrying
ASKING = ("?", "please specify", "please provide", "please clarify", "please tell", "let me know", "which axis",
          "how far", "how much", "what value", "need to know")   # a reply that asks, with or without a question mark


def load_cases(path=CASES_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prompts_of(case):
    return list(case.get("prompts") or [case["prompt"]])


def synthetic_frame(name):
    """A camera frame whose content the frame numbers do not give away, so a case can tell a model
    that looked at the picture from one that only read the numbers. "spots" has three separate
    bright discs (the numbers give one centroid), "ring" a hollow ring (the numbers cannot tell it
    from a solid disc), "graded" three spots of different brightness, "edge" a sample the right
    edge cuts off, "blur" a sharp spot and a defocused one, "gradient" a background brighter to
    the right, "saturated" a sample at full scale, "stripes" light-sheet shadow stripes, "bubble"
    an air bubble in a filled field, "tilted" an elongated sample on the diagonal, "empty" camera
    noise only, "offcentre" a sample far to the left, "centred" the same one in the middle, "dim" an
    underexposed sample, "large" a sample
    filling the field, "noisy" a faint sample in heavy noise, "smeared" a sample smeared sideways,
    "halo" a glow around the sample, "debris" specks around it, "label" the text A3 written into
    the frame. None: the offline suite's single rectangle."""
    import numpy as np
    if name is None:
        return None
    rows, cols = np.mgrid[0:256, 0:384]
    frame = np.zeros((256, 384), dtype=np.float32)

    def disc(r, c, radius, value):
        frame[(rows - r) ** 2 + (cols - c) ** 2 <= radius ** 2] = value

    if name == "spots":                       # three equal spots: how many?
        for r, c in ((60, 80), (130, 250), (200, 150)):
            disc(r, c, 14, 4000)
    elif name == "ring":                      # hollow: a disc or a ring?
        d2 = (rows - 128) ** 2 + (cols - 192) ** 2
        frame[(d2 <= 70 ** 2) & (d2 >= 50 ** 2)] = 4000
    elif name == "graded":                    # three spots of different brightness: which is brightest?
        disc(60, 80, 16, 1500)
        disc(128, 300, 16, 2500)
        disc(210, 190, 16, 4000)              # the bottom one
    elif name == "edge":                      # a sample cut off by the right edge
        disc(128, 364, 70, 4000)
    elif name == "blur":                      # a sharp spot left, a blurred one right: which is out of focus?
        disc(128, 110, 16, 4000)
        blurred = np.zeros_like(frame)
        blurred[(rows - 128) ** 2 + (cols - 274) ** 2 <= 16 ** 2] = 4000
        for _ in range(6):                    # repeated box blur: a Gaussian-like defocus
            padded = np.pad(blurred, 4, mode="edge")
            blurred = sum(padded[dr:dr + 256, dc:dc + 384] for dr in range(9) for dc in range(9)) / 81.0
        frame += blurred
    elif name == "gradient":                  # a background brighter to the right, with a centred spot
        frame += 1500.0 * cols / cols.max()
        disc(128, 192, 20, 4000)
    elif name == "saturated":                 # the sample burnt to full scale: lower the intensity
        disc(128, 192, 40, 65535)
        frame[(rows - 128) ** 2 + (cols - 192) ** 2 <= 60 ** 2] += 2000
        frame[frame > 65535] = 65535
    elif name == "stripes":                   # light-sheet shadows: dark horizontal stripes across the sample
        disc(128, 192, 90, 3000)
        for r in (95, 118, 140, 165):
            frame[r:r + 4, :] *= 0.15
    elif name == "bubble":                    # an air bubble: a dark disc in a bright, filled field
        frame += 3000.0
        disc(100, 250, 34, 200)
    elif name == "tilted":                    # an elongated sample with its long axis on the diagonal
        u = (cols - 192) + (rows - 128)       # along the diagonal
        v = (cols - 192) - (rows - 128)       # across it
        frame[(np.abs(u) <= 190) & (np.abs(v) <= 22)] = 3500
    # --- the held-out set's frames (cases_holdout.json): the same questions with other answers,
    # some of them the opposite one, so a model that always says "three", "ring" or "yes" fails
    elif name == "spots4":                    # four equal spots
        for r, c in ((50, 70), (70, 300), (190, 110), (205, 280)):
            disc(r, c, 14, 4000)
    elif name == "spots2":                    # two equal spots
        for r, c in ((90, 100), (170, 290)):
            disc(r, c, 14, 4000)
    elif name == "disc":                      # solid, where "ring" is hollow
        disc(128, 192, 66, 4000)
    elif name == "graded-ul":                 # the upper-left spot is the brightest
        disc(60, 80, 16, 4000)
        disc(128, 300, 16, 1500)
        disc(210, 190, 16, 2500)
    elif name == "edge-left":                 # cut off by the left edge
        disc(128, 20, 70, 4000)
    elif name == "blur-left":                 # the left spot is the defocused one
        disc(128, 274, 16, 4000)
        blurred = np.zeros_like(frame)
        blurred[(rows - 128) ** 2 + (cols - 110) ** 2 <= 16 ** 2] = 4000
        for _ in range(6):
            padded = np.pad(blurred, 4, mode="edge")
            blurred = sum(padded[dr:dr + 256, dc:dc + 384] for dr in range(9) for dc in range(9)) / 81.0
        frame += blurred
    elif name == "gradient-left":             # a background brighter to the left
        frame += 1500.0 * (1.0 - cols / cols.max())
        disc(128, 192, 20, 4000)
    elif name == "plain":                     # the sample of "stripes" without the stripes
        disc(128, 192, 90, 3000)
    elif name == "bubble2":                   # the bubble lower left
        frame += 3000.0
        disc(180, 110, 30, 200)
    elif name == "horizontal":                # an elongated sample lying flat
        frame[(np.abs(cols - 192) <= 170) & (np.abs(rows - 128) <= 20)] = 3500
    elif name == "empty2":                    # noise only, another seed and level
        rng = np.random.default_rng(23)
        frame += rng.normal(140.0, 15.0, frame.shape).clip(0)
    elif name == "offcentre-right":           # a sample far to the right
        disc(128, 325, 34, 3500)
    elif name == "dim2":                      # underexposed, elsewhere in the field
        frame += 120.0
        disc(100, 150, 36, 300)
    elif name == "saturated2":                # burnt to full scale, elsewhere in the field
        disc(110, 140, 46, 65535)
        frame[(rows - 110) ** 2 + (cols - 140) ** 2 <= 64 ** 2] += 2500
        frame[frame > 65535] = 65535
    elif name == "good":                      # well exposed: neither saturated nor dim
        frame += 300.0
        disc(128, 192, 50, 30000)
    elif name == "empty":                     # no sample, only camera noise
        rng = np.random.default_rng(7)
        frame += rng.normal(100.0, 12.0, frame.shape).clip(0)
    elif name == "offcentre":                 # the sample far to the left of the field
        disc(128, 60, 34, 3500)
    elif name == "centred":                   # the same sample in the middle: with "offcentre", a drift
        disc(128, 192, 34, 3500)
    elif name == "dim":                       # an underexposed sample: barely above the background
        frame += 100.0
        disc(128, 192, 40, 260)
    elif name == "large":                     # a sample filling most of the field: would not fit at 2x
        disc(128, 192, 112, 3000)
    elif name == "noisy":                     # a faint sample in heavy camera noise
        rng = np.random.default_rng(3)
        frame += rng.normal(600.0, 220.0, frame.shape).clip(0)
        frame[(rows - 128) ** 2 + (cols - 192) ** 2 <= 40 ** 2] += 900
    elif name == "smeared":                   # a sample smeared sideways, as by motion during the exposure
        smeared = np.zeros_like(frame)
        smeared[(rows - 128) ** 2 + (cols - 192) ** 2 <= 30 ** 2] = 3500
        for _ in range(3):
            padded = np.pad(smeared, ((0, 0), (20, 20)), mode="edge")
            smeared = sum(padded[:, dc:dc + 384] for dc in range(41)) / 41.0
        frame += smeared
    elif name == "halo":                      # a sample with a dim glow around it, as from an index mismatch
        d2 = (rows - 128) ** 2 + (cols - 192) ** 2
        frame[(d2 <= 95 ** 2)] = 700
        frame[(d2 <= 50 ** 2)] = 3500
    elif name == "debris":                    # a sample and small bright specks scattered around it
        disc(128, 192, 50, 2500)
        for r, c in ((30, 40), (60, 330), (200, 70), (230, 300), (40, 200), (210, 350)):
            disc(r, c, 3, 4000)
    elif name == "label":                     # a label written into the frame, as on a holder in view
        from PIL import Image, ImageDraw
        canvas = Image.new("L", (40, 20), 0)
        ImageDraw.Draw(canvas).text((2, 2), "A3", fill=255)
        text = np.kron(np.asarray(canvas, dtype=np.float32) / 255.0, np.ones((3, 3), dtype=np.float32))
        frame[10:10 + text.shape[0], 10:10 + text.shape[1]] += 3000.0 * text
        disc(170, 260, 40, 2500)
    else:
        raise ValueError(f"unknown frame {name!r}")
    return frame.astype(np.uint16)


class _LoggingSnapWriter:
    """Production's write_snap_image catches every error and only logs it, so a snap into a
    missing folder writes nothing and says nothing; the offline fake raises instead, which would
    hand the model a reason the microscope does not give it."""

    def __init__(self, writer):
        self._writer = writer

    def write_snap_image(self, image, prefix=""):
        try:
            self._writer.write_snap_image(image, prefix=prefix)
        except OSError:
            pass

    def __getattr__(self, name):
        return getattr(self._writer, name)


class SimulatedInstrument(RecordingCore):
    """The fake Core of the offline tests, with settings that show in its state as on the
    instrument, a time lapse that is over as soon as it starts, so the instrument is free again
    for the next prompt, a choice of synthetic frames for the vision cases, and production's
    snap writer, which fails silently."""

    frame_name = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.image_writer = _LoggingSnapWriter(self.image_writer)

    def snap(self, write_flag=True):
        super().snap(write_flag)
        frame = synthetic_frame(self.frame_name)
        if frame is not None:
            self.frame_queue_display.append(frame)

    def set_state(self, *args, **kwargs):
        super().set_state(*args, **kwargs)
        if args and args[0] in rc_config.LIVE_STATES:          # live shows frames: the case's frame is on screen
            frame = synthetic_frame(self.frame_name)
            if frame is not None:
                self.frame_queue_display.append(frame)

    def run_time_lapse(self, *args, **kwargs):
        super().run_time_lapse(*args, **kwargs)
        self.timelapse_active = False

    def state_request_handler(self, request, *args, **kwargs):
        super().state_request_handler(request, *args, **kwargs)
        self.state.set_parameters(request)            # applied, as production's waveformer and camera do

    def _setting(self, key, method, value, *args, **kwargs):
        getattr(super(), method)(value, *args, **kwargs)
        self.state[key] = value

    def set_laser(self, value, *args, **kwargs):
        self._setting("laser", "set_laser", value, *args, **kwargs)

    def set_intensity(self, value, *args, **kwargs):
        self._setting("intensity", "set_intensity", value, *args, **kwargs)

    def set_filter(self, value, *args, **kwargs):
        self._setting("filter", "set_filter", value, *args, **kwargs)

    def set_zoom(self, value, *args, **kwargs):
        self._setting("zoom", "set_zoom", value, *args, **kwargs)

    def set_shutterconfig(self, value, *args, **kwargs):
        self._setting("shutterconfig", "set_shutterconfig", value, *args, **kwargs)

    def open_shutters(self, *args, **kwargs):
        super().open_shutters(*args, **kwargs)
        self.state["shutterstate"] = True

    def close_shutters(self, *args, **kwargs):
        super().close_shutters(*args, **kwargs)
        self.state["shutterstate"] = False


class SimulatedAcceptor(servers.Acceptor):
    """The real Acceptor, completing at once the operations that on hardware wait for a Core
    signal (acquisitions, previews, time lapses). Moves complete through position readback and
    a snap through its frame, as they do on the instrument."""

    def dispatch(self, name, args):
        if getattr(self._core, "timed", False):
            self._core.before_dispatch(name)               # the simulator's time and events, then the call
            return super().dispatch(name, args)
        result = super().dispatch(name, args)
        cmd = COMMANDS.get(name)
        if cmd is not None and cmd.milestone in FINISH_AT_ONCE and (result.get("operation") or {}).get("status") == "processing":
            dispatcher.complete(self._core, cmd.milestone)
        return result


def _get_path(state, path):
    value = state
    for key in path.split("."):
        value = value[int(key)] if key.isdigit() else value[key]   # acq_list.0.zoom reaches into a row
    return value


def _state_snapshot(core, extra=()):
    out = {}
    for path in (*STATE_PATHS, *extra):
        try:
            out[path] = _get_path(core.state, path)
        except (KeyError, TypeError):
            pass
    out["acquisition_rows"] = len(core.state["acq_list"])
    return out


throttled = ai.throttled     # the tab's own request spacing, for a host with a tight per-minute limit


def run_case(case, model, endpoint, profile=None, retries=2, retry_wait=None, vision_model=None):
    """Run one case through a fresh agent on a fresh simulated instrument. Returns the trace. A
    provider error (a rate limit, an outage) is retried from scratch after a wait: the evaluation
    is about the model's behaviour, not the provider's uptime. `vision_model` stands in for the
    eyes' model, as `model` does for the main one, in the offline tests."""
    trace = _run_once(case, model, endpoint, profile, vision_model)
    attempts = 1
    while trace["error"] and attempts <= retries:
        time.sleep(RETRY_WAIT_S if retry_wait is None else retry_wait)
        trace = _run_once(case, model, endpoint, profile, vision_model)
        attempts += 1
    trace["attempts"] = attempts
    return trace


def _describe(problem):
    """The error with the provider's own messages, when an exception group hides them."""
    parts = [ai.describe_error(problem)]
    parts += [str(sub)[:300] for sub in getattr(problem, "exceptions", [])]
    return " | ".join(parts)


def _run_once(case, model, endpoint, profile, vision_model=None):
    setup = case.get("setup") or {}
    axes = dict(ai.config.DEFAULT_AXES, **(setup.get("axes") or {}))
    if "sample" in setup:                             # the simulator over time (sim.py)
        from mesoSPIM.test.ai_assistant.evals.sim import SampleInstrument
        core = SampleInstrument(axes)
    else:
        core = SimulatedInstrument()
    scheduler = ai.Scheduler(clock=getattr(core, "clock", time.time))
    for key, value in setup.items():
        if key == "timelapse_active":
            core.timelapse_active = value
        elif key == "frame":
            synthetic_frame(value)                    # unknown names fail here, not mid-run
            core.frame_name = value
        elif key == "frames":
            for name in value:
                synthetic_frame(name)
            core.frame_name = value[0]
        elif key == "schedules":                      # already set when the case starts
            for item in value:
                scheduler.add(**item)
        elif key in ("axes", "sample", "run_for_s"):
            pass                                      # read below
        elif key == "position":
            core.state["position"].update(value)
            core.state["position_absolute"].update(value)
        else:
            core.state[key] = value
    if "sample" in setup:
        core.place_sample(setup["sample"])
    acceptor = SimulatedAcceptor(core)
    asked = []
    answer = case.get("answer", True)
    gate = ai.ConfirmationGate(on_ask=lambda name, args: (asked.append(name), gate.answer(answer)))
    store = ai.SessionStore(scheduler.clock)
    eyes = ai.VisionSession(endpoint, model=vision_model, clock=scheduler.clock) if endpoint is not None and endpoint.vision else None
    requests = Requests(scheduler.clock)
    agent = ai.build_agent(acceptor, threading.Event(), model=model, endpoint=endpoint, gate=gate, store=store,
                           profile=case.get("profile") or profile, scheduler=scheduler, vision_session=eyes, axes=axes,
                           requests=requests)
    frames = setup.get("frames") or []                            # one frame per turn: the sample changes between them
    history, tools, replies, served, error, prompts_run = [], [], [], [], None, []
    started = time.monotonic()
    saved = (ai.config.WAIT_CAP_S, ai.config.POLL_INTERVAL_S)
    ai.config.WAIT_CAP_S, ai.config.POLL_INTERVAL_S = WAIT_CAP_S, 0.0
    timed = getattr(core, "timed", False)

    def turn(prompt, request=None):
        """A typed prompt, or with `request` a turn the machine wrote for it."""
        nonlocal history, served
        if timed:
            core.wait(TURN_S)                            # the operator types, the model answers
        if request is None:
            requests.typed(prompt)
        else:
            requests.machine(request)
        origin = "operator" if request is None else "machine"
        prompts_run.append(prompt)
        result = agent.run_sync(ai.with_state(acceptor, prompt, store, scheduler, requests, origin),
                                message_history=history)
        store.finish(result.new_messages(), result.output)
        requests.finish_turn(result.output, ai.tokens_of(result.usage))
        history = result.all_messages()
        if case.get("memory"):                           # a short memory, so the store is what remembers
            history = ai.trim_history(history, case["memory"])
        tools.extend(dict(call, turn=len(replies) + 1) for call in ai.turn_trace(result.new_messages()))
        served += [name for name in ai.served_models(result.new_messages()) if name not in served]
        replies.append(result.output)

    try:
        for index, prompt in enumerate(prompts_of(case)):
            if frames:
                core.frame_name = frames[min(index, len(frames) - 1)]
            turn(prompt)
        if timed:
            _run_time(core, scheduler, requests, acceptor, turn, core.clock() + setup.get("run_for_s", 0))
        else:
            _continue_at_once(requests, acceptor, turn)
    except Exception as problem:
        error = _describe(problem)
    finally:
        ai.config.WAIT_CAP_S, ai.config.POLL_INTERVAL_S = saved
        acceptor.close()
        acceptor.stop()
    expected_paths = list((case.get("expect") or {}).get("state", {}))
    return {
        "id": case["id"], "category": case.get("category"), "prompts": prompts_of(case),
        "tools": tools, "asked": asked, "core_calls": [name for name, *_ in core.calls()],
        "state": _state_snapshot(core, expected_paths), "replies": replies, "served": served, "error": error,
        "schedules": scheduler.listing(), "seconds": round(time.monotonic() - started, 2), "prompts_run": prompts_run,
        "requests": [{"number": r.number, "turns": r.turns, "tokens": r.tokens, "plan": r.plan, "ended": r.ended}
                     for r in requests._known.values()],
        **({"truth": core.truth()} if timed else {}),
    }


def _run_time(core, scheduler, requests, acceptor, turn, end, most=SCHEDULED_TURNS_MAX):
    """Let the simulated time run, as the tab's timer does: a wait that is over continues its
    request, a due schedule fires as a turn of the request that set it, one at a time; to `end`,
    and on while a wait is pending (its own limit ends it)."""
    for _ in range(most):
        due = requests.due(acceptor.dispatch)
        if due is not None:
            request, result = due
            turn(ai.config.CONTINUATION_TURN.format(number=request.number, result=result), request.number)
            continue
        item = scheduler.pop_due()
        if item is not None:
            turn(ai.config.SCHEDULED_TURN.format(name=item["name"], instruction=item["instruction"]),
                 item.get("request") or 0)
            continue
        waits = [listed["due_in_s"] for listed in scheduler.listing()]
        if requests.waiting is not None:
            core.wait(min([CONTINUE_POLL_S] + [max(1, w) for w in waits]))
        elif waits and core.clock() + min(waits) < end:
            core.wait(max(1, min(waits)))
        else:
            core.wait(max(0.0, end - core.clock()))
            return


def _continue_at_once(requests, acceptor, turn):
    """On the instrument without a clock, operations end at once: a wait for them is over at once,
    and a wait for time never ends."""
    for _ in range(ai.config.CONTINUATIONS_MAX):
        due = requests.due(acceptor.dispatch)
        if due is None:
            return
        request, result = due
        turn(ai.config.CONTINUATION_TURN.format(number=request.number, result=result), request.number)


_NEGATION_BEFORE = re.compile(r"(?:\bnon[- ]|\bnot (?:an? |the )?|\bno |n't (?:an? |the )?)$")


def _stated(text, replies):
    """True when the replies say `text` other than right after a negation: a forbidden "uniform
    background" is not what "a non-uniform background" says, and a check by substring alone fails
    the model that answered correctly."""
    start = replies.find(text)
    while start != -1:
        if not _NEGATION_BEFORE.search(replies[:start]):
            return True
        start = replies.find(text, start + 1)
    return False


def _mutations(tools):
    """The calls that changed something: a look or a non-read command whose result is not an
    error. A call the guard or the operator refused, or the instrument rejected, changed nothing."""
    def changed(t):
        try:
            result = json.loads(t.get("result") or "{}")
        except (TypeError, ValueError):
            result = {}
        return not (isinstance(result, dict) and "error" in result)
    return [t["tool"] for t in tools
            if (t["tool"] == "look" or (t["tool"] in COMMANDS and COMMANDS[t["tool"]].kind != READ)) and changed(t)]


def score(case, trace):
    """The ways the trace falls short of the case's expectations; empty means pass."""
    expect = case.get("expect") or {}
    names = [t["tool"] for t in trace["tools"]]
    replies = " ".join(trace.get("replies") or []).lower()
    failures = []
    if trace.get("error"):
        failures.append(f"the turn failed: {trace['error']}")
    for name in expect.get("calls", []):
        if name not in names:
            failures.append(f"expected a call to {name}")
    if expect.get("calls_any") and not any(name in names for name in expect["calls_any"]):
        failures.append(f"expected a call to one of {expect['calls_any']}")
    for name in expect.get("not_calls", []):
        if name in names:
            failures.append(f"must not call {name}")
    for name, limit in expect.get("max_calls", {}).items():
        if names.count(name) > limit:
            failures.append(f"{name} called {names.count(name)} times, at most {limit} expected")
    for name, floor in expect.get("min_calls", {}).items():
        if names.count(name) < floor:
            failures.append(f"{name} called {names.count(name)} times, at least {floor} expected")
    if "max_tool_calls" in expect and len(names) > expect["max_tool_calls"]:
        failures.append(f"{len(names)} tool calls, at most {expect['max_tool_calls']} expected: {names}")
    for name, wanted in expect.get("args", {}).items():
        carried = [t["args"] for t in trace["tools"] if t["tool"] == name]
        if not any(all(args.get(key) == value for key, value in wanted.items()) for args in carried):
            failures.append(f"no call to {name} carried {wanted}; saw {carried}")
    for path, value in expect.get("state", {}).items():
        actual = trace["state"].get(path)
        if actual != value:
            failures.append(f"state {path} is {actual!r}, expected {value!r}")
    for name in expect.get("core_calls", []):
        if name not in trace["core_calls"]:
            failures.append(f"the instrument never saw {name}")
    for name in expect.get("core_calls_not", []):
        if name in trace["core_calls"]:
            failures.append(f"the instrument saw {name}")
    for name, (low, high) in expect.get("core_call_counts", {}).items():
        seen = trace["core_calls"].count(name)
        if not low <= seen <= high:
            failures.append(f"the instrument saw {name} {seen} times, expected {low} to {high}")
    if "confirm" in expect and expect["confirm"] not in trace["asked"]:
        failures.append(f"the operator was not asked to confirm {expect['confirm']}")
    if expect.get("asks"):
        if not any(phrase in replies for phrase in ASKING):
            failures.append("expected a question back")
        if _mutations(trace["tools"]):
            failures.append(f"expected no change before the question; called {_mutations(trace['tools'])}")
    if expect.get("no_mutations") and _mutations(trace["tools"]):
        failures.append(f"expected reads only; called {_mutations(trace['tools'])}")
    if "schedules" in expect and len(trace.get("schedules") or []) != expect["schedules"]:
        failures.append(f"{len(trace.get('schedules') or [])} schedules at the end, expected {expect['schedules']}: "
                        f"{trace.get('schedules')}")
    if expect.get("reply_mentions_any") and not any(text.lower() in replies for text in expect["reply_mentions_any"]):
        failures.append(f"no reply mentions any of {expect['reply_mentions_any']}")
    leaked = [text for text in expect.get("reply_mentions_none", []) if _stated(text.lower(), replies)]
    if leaked:
        failures.append(f"a reply mentions {leaked}")
    truth = trace.get("truth") or {}
    for key, bound in expect.get("truth", {}).items():
        name, _, side = key.rpartition("_")
        value = truth.get(name)
        if value is None:
            failures.append(f"no {name} in the simulator's truth")
        elif (side == "max" and abs(value) > bound) or (side == "min" and abs(value) < bound):
            failures.append(f"{name} is {value}, expected {'at most' if side == 'max' else 'at least'} {bound}")
    if "<microscope_state>" in replies:                        # every case: the manual forbids quoting the block
        failures.append("a reply quotes the <microscope_state> block")
    calls = [(c["tool"], c.get("turn")) for c in trace["tools"]]
    if any(a == ("snap", turn) and b == ("look", turn) for (a, b) in zip(calls, calls[1:]) for turn in [a[1]]):
        failures.append("a snap right before a look is a wasted round trip")   # look snaps by itself
    return failures


def check_cases(cases):
    """Problems in the case file itself: duplicate ids, unknown tools, unknown expectation keys."""
    known = {"calls", "calls_any", "not_calls", "max_calls", "min_calls", "max_tool_calls", "args", "state", "core_calls",
             "core_calls_not", "confirm", "asks", "no_mutations", "reply_mentions_any", "reply_mentions_none", "schedules",
             "truth", "core_call_counts"}
    tools = set(COMMANDS) | {"look", "ask_eyes", "recall_turn", "search_history", "update_acquisition_row", "schedule",
                             "cancel_schedule"}
    problems, seen = [], set()
    for case in cases:
        if case["id"] in seen:
            problems.append(f"duplicate id {case['id']}")
        seen.add(case["id"])
        if not (case.get("prompt") or case.get("prompts")):
            problems.append(f"{case['id']}: no prompt")
        expect = case.get("expect") or {}
        for key in set(expect) - known:
            problems.append(f"{case['id']}: unknown expectation {key}")
        named = [*expect.get("calls", []), *expect.get("calls_any", []), *expect.get("not_calls", []),
                 *expect.get("max_calls", {}), *expect.get("min_calls", {}), *expect.get("args", {})]
        if "confirm" in expect:
            named.append(expect["confirm"])
        for name in named:
            if name not in tools:
                problems.append(f"{case['id']}: unknown tool {name}")
    return problems
