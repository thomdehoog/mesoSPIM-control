"""The frame history, look over chosen frames, the map and calibrate (roadmap C1 to C4), scored
against the simulator's truth (evals/sim.py) where there is one."""
import json
import threading

import numpy as np
import pytest

from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant import frames as fh
from mesoSPIM.test.ai_assistant.evals import harness
from mesoSPIM.test.ai_assistant.evals.sim import SampleInstrument
from mesoSPIM.test.ai_assistant.test_evals import SCRIPTED, scripted

pytest.importorskip("pydantic_ai")


def instrument(axes=None, **sample):
    core = SampleInstrument(axes)
    for key in ("position", "position_absolute"):
        core.state[key].update(x_pos=0.0, y_pos=0.0, z_pos=0.0, f_pos=5000.0)
    core.place_sample(dict({"x": 0.0, "y": 0.0, "f": 5000.0}, **sample))
    return core


def tools_on(core, tmp_path, axes=None, answer=True):
    """The assistant's tools on the simulator, with a history whose calibration is in tmp_path."""
    acceptor = harness.SimulatedAcceptor(core)
    store = ai.SessionStore(core.clock, fh.Calibration(tmp_path / "calibration.json"))
    gate = ai.ConfirmationGate(on_ask=lambda name, args: gate.answer(answer))
    tools = ai.build_tools(acceptor, threading.Event(), endpoint=SCRIPTED, gate=gate, store=store,
                           axes=dict(ai.config.DEFAULT_AXES, **(axes or {})))
    ai.with_state(acceptor, "a turn", store)                 # the guard counts turns; the moves get their Run
    return acceptor, store, {t.name: t for t in tools}


def call(tools, name, **args):
    import asyncio
    result = tools[name].function(**args)
    return json.loads(asyncio.run(result) if asyncio.iscoroutine(result) else result)


# --- C1: the history and its measures ---

def test_every_frame_is_kept_numbered_with_its_measures_and_the_move_that_centres_it(tmp_path):
    core = instrument(x=-400.0, y=250.0)                    # the sample sits 400 um right and 250 um down
    _, store, tools = tools_on(core, tmp_path)
    first = call(tools, "look", question="where is it?", label="start")
    assert first["kept"]["n"] == 1 and first["kept"]["label"] == "start" and first["kept"]["scale"] == "nominal"
    move = first["kept"]["centre_move_um"]
    assert move["x"] == pytest.approx(-400, abs=40) and move["y"] == pytest.approx(250, abs=40)
    assert call(tools, "snap")["kept"] == {"n": 2}           # a snap is kept too
    entry = store.frames.frames[-1]
    assert entry["source"] == "snap" and entry["image"].shape == (256, 256) and entry["position"]["f"] == 5000.0
    assert entry["settings"]["zoom"] == "1x" and entry["measures"]["offset_um"]["right"] == pytest.approx(400, abs=40)
    call(tools, "move_relative", deltas=move)
    assert core.truth()["off_centre_um"] < 40                # the measured move centres it


def test_the_history_keeps_what_fits_and_numbers_on():
    history = fh.FrameHistory(max_bytes=3 * 64 * 64 * 2)
    stats = {"max": 1000, "mean": 10, "full_scale": 65535, "saturated_fraction": 0, "focus_measure": 1,
             "signal_centroid": {"row": 0.5, "col": 0.5}, "shape": [64, 64]}
    for _ in range(5):
        history.add(np.zeros((64, 64), np.uint16), stats, "look", {}, {})
    assert [f["n"] for f in history.frames] == [3, 4, 5]
    assert [f["n"] for f in history.pick(2)] == [4, 5] and [f["n"] for f in history.pick("3-4")] == [3, 4]
    assert [f["n"] for f in history.pick([5, 3])] == [5, 3]
    with pytest.raises(ValueError, match="no frame 1; the history holds 3 to 5"):
        history.pick([1])
    with pytest.raises(ValueError):
        history.pick("most of them")
    assert history.listing()["count"] == 3 and history.listing()["numbers"] == "3-5"


# --- C2: look over chosen frames ---

def test_look_over_frames_measures_the_drift_between_them(tmp_path):
    core = instrument(drift_um_per_min={"x": 30.0})
    _, store, tools = tools_on(core, tmp_path)
    call(tools, "look", question="first")
    core.wait(300)                                           # five minutes: 150 um of drift
    later = call(tools, "look", question="moved?", frames="1")
    assert [f["n"] for f in later["frames"]] == [1, 2]
    shift = later["changes"][0]["since_first"]["image_shift_um"]
    assert shift["right"] == pytest.approx(-150, abs=25) and abs(shift["up"]) < 25   # x drifts on: the sample left
    reused = call(tools, "look", question="again", snap=False, frames="1,2")
    assert len(store.frames.frames) == 2 and [f["n"] for f in reused["frames"]] == [1, 2]   # no new exposure
    assert [f["n"] for f in call(tools, "look", question="?", snap=False, frames="last 2")["frames"]] == [1, 2]
    assert call(tools, "look", question="?", frames="9")["error"]["code"] == "validation"


def test_the_eyes_get_the_frames_a_look_shows(tmp_path):
    from mesoSPIM.test.ai_assistant.test_assistant import _counting_eyes_model
    seen = []
    core = instrument()
    acceptor = harness.SimulatedAcceptor(core)
    store = ai.SessionStore(core.clock)
    seeing = ai.Endpoint(provider="Scripted", kind="openai-compatible", model="m", vision=True)
    eyes = ai.VisionSession(seeing, model=_counting_eyes_model(seen))
    tools = {t.name: t for t in ai.build_tools(acceptor, threading.Event(), endpoint=seeing, store=store,
                                               vision_session=eyes)}
    call(tools, "look", question="one")
    call(tools, "look", question="two")
    call(tools, "look", question="compare", frames="1-2")
    assert [s["images"] for s in seen] == [1, 1, 3]
    assert "Frame 1," in " ".join(seen[-1]["texts"]) and "Frame 3," in " ".join(seen[-1]["texts"])


# --- C3: the map, against the simulator's truth ---

def test_the_map_finds_the_sample_and_its_best_focus(tmp_path):
    core = instrument(x=300.0, y=-200.0, f=5130.0)
    acceptor, store, tools = tools_on(core, tmp_path)
    for f in (4950, 5050, 5100, 5150, 5250):
        core.state["position"]["f_pos"] = float(f)
        call(tools, "look", question="?")
    truth_x, truth_y = core.sample.x, core.sample.y
    group = fh.sample_map(store.frames)["groups"][0]
    assert group["best_focus"]["f"] == pytest.approx(5130, abs=20) and group["best_focus"]["plus_minus"] <= 25
    assert group["sample_at"]["x"] == pytest.approx(truth_x, abs=50)
    assert group["sample_at"]["y"] == pytest.approx(truth_y, abs=50)
    text = ai.with_state(acceptor, "next", store)
    assert '"map"' in text and '"frames"' in text            # the readout carries both


def test_the_map_leaves_out_saturated_frames_and_says_which_way_to_search(tmp_path):
    core = instrument(f=5400.0)
    _, store, tools = tools_on(core, tmp_path)
    for f in (5000, 5100, 5200):
        core.state["position"]["f_pos"] = float(f)
        call(tools, "look", question="?")
    group = fh.sample_map(store.frames)["groups"][0]
    assert group["best_focus"]["edge"] == "search higher f"
    core.state["intensity"] = 100
    core.state["camera_exposure_time"] = 1.0
    call(tools, "look", question="?")
    group = fh.sample_map(store.frames)["groups"][0]
    assert group["left_out"] == {4: "saturated"} and group["good_light"]["frame"] == 3


# --- C4: calibrate ---

def test_calibrate_measures_a_wrong_coordinate_system_and_the_moves_follow_it(tmp_path):
    """The operator's coordinate system says x moves the sample right; on this microscope it moves it
    left. The nominal move goes the wrong way; after calibrate the move centres the sample."""
    core = instrument({"x": "left"}, x=-300.0, y=150.0)
    _, store, tools = tools_on(core, tmp_path)               # the assistant believes the default: x right
    wrong = call(tools, "look", question="?")["kept"]["centre_move_um"]
    report = call(tools, "calibrate")
    assert report["x"]["sample_moves"] == "left" and report["y"]["sample_moves"] == "up"
    assert report["matches_the_coordinate_system"] is False
    assert report["x"]["image_um_per_stage_um"] == pytest.approx(1.0, abs=0.05)
    assert json.loads((tmp_path / "calibration.json").read_text())["1x"]["step_um"] == 205.0
    after = call(tools, "look", question="?")["kept"]
    assert after["scale"] == "calibrated" and after["centre_move_um"]["x"] == pytest.approx(-wrong["x"], abs=40)
    call(tools, "move_relative", deltas=after["centre_move_um"])
    assert core.truth()["off_centre_um"] < 40


def test_calibrate_waits_for_run_and_needs_a_sample(tmp_path):
    core = instrument()
    _, _, tools = tools_on(core, tmp_path, answer=False)
    assert call(tools, "calibrate")["error"]["code"] == "refused" and "move_relative" not in str(core.calls())
    empty = instrument(x=8000.0)                             # nothing in the field
    _, _, tools = tools_on(empty, tmp_path)
    assert "visible sample" in call(tools, "calibrate")["error"]["message"]


def test_phase_correlation_finds_a_known_shift():
    rng = np.random.default_rng(2)
    image = rng.random((128, 128))
    moved = np.roll(np.roll(image, 7, axis=1), -4, axis=0)
    found = fh.shift(image, moved)
    assert (found["right"], found["down"]) == (pytest.approx(7, abs=0.3), pytest.approx(-4, abs=0.3))
    assert found["confidence"] > 0.5


def test_a_case_on_the_simulator_centres_with_the_kept_move():
    """End to end: look, move by the centre_move_um it reports, and the truth says centred."""
    case = {"id": "c", "prompt": "Centre the sample.", "setup": {"sample": {"x": -450, "y": 300},
            "position": {"x_pos": 0.0, "y_pos": 0.0}}, "expect": {"truth": {"off_centre_um_max": 60}}}
    probe = harness.run_case(case, scripted((("look", {"question": "where?"}), "Seen.")), SCRIPTED)
    move = json.loads(probe["tools"][0]["result"])["kept"]["centre_move_um"]
    trace = harness.run_case(case, scripted((("look", {"question": "where?"}), ("move_relative", {"deltas": move}),
                                             "Centred.")), SCRIPTED)
    assert harness.score(case, trace) == [], trace["truth"]
