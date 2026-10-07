"""Measured values through the turn guard (roadmap E1). The operator here always answers Cancel,
so a call that needs their Run is refused: what goes through is what the setting lets through.
Both sides: what follows from a fresh measurement passes, and a stale frame, a wrong calibration
sign, a value off the measurement and a change past the bounds wait for Run."""
import json
import threading

import pytest

from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant import config
from mesoSPIM.src.ai_assistant import frames as fh
from mesoSPIM.test.ai_assistant.evals import harness
from mesoSPIM.test.ai_assistant.test_evals import SCRIPTED
from mesoSPIM.test.ai_assistant.test_frames import call, instrument

pytest.importorskip("pydantic_ai")


def guarded(core, tmp_path, measured=True, prompt="a turn"):
    """The tools on the simulator with an operator who cancels every question."""
    acceptor = harness.SimulatedAcceptor(core)
    store = ai.SessionStore(core.clock, fh.Calibration(tmp_path / "calibration.json"))
    gate = ai.ConfirmationGate(on_ask=lambda name, args: gate.answer(False))
    tools = {t.name: t for t in ai.build_tools(acceptor, threading.Event(), endpoint=SCRIPTED, gate=gate, store=store,
                                               axes=dict(config.DEFAULT_AXES), profile="Full", measured=measured)}
    ai.with_state(acceptor, prompt, store)
    return acceptor, store, tools


def refused(result):
    return result.get("error", {}).get("code") == "refused"


def test_off_by_default_a_measured_move_still_waits_for_run(tmp_path):
    core = instrument(x=-400.0, y=250.0)
    _, _, tools = guarded(core, tmp_path, measured=False)
    move = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert refused(call(tools, "move_relative", deltas=move))
    assert "Measured values" not in ai.build_system_prompt() and "Measured values" in ai.build_system_prompt(measured=True)


def test_on_the_measured_move_goes_through_and_centres_the_sample(tmp_path):
    core = instrument(x=-400.0, y=250.0)
    _, _, tools = guarded(core, tmp_path)
    move = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert "error" not in call(tools, "move_relative", deltas=move)
    assert core.truth()["off_centre_um"] < 40


def test_a_stale_frame_does_not_count(tmp_path):
    core = instrument(x=-400.0, y=250.0)
    _, _, tools = guarded(core, tmp_path, prompt="set the intensity to 12")
    move = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert "error" not in call(tools, "set_intensity", intensity=12)          # theirs; the frame is now stale
    assert refused(call(tools, "move_relative", deltas=move))


def test_a_wrong_calibration_sign_stops_at_the_second_move(tmp_path):
    """The operator's coordinate system says x moves the sample right; here it moves it left. The
    first measured move goes the wrong way and doubles the offset; the next is not allowed."""
    core = instrument({"x": "left"}, x=-300.0, y=0.0)
    _, _, tools = guarded(core, tmp_path)
    first = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert "error" not in call(tools, "move_relative", deltas=first)
    assert core.truth()["off_centre_um"] > 500                                # worse, not better
    second = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert refused(call(tools, "move_relative", deltas=second))


def test_one_wrong_axis_is_stopped_even_while_the_other_converges(tmp_path):
    """Only y is set wrong. The first move centres x and doubles y's offset: the whole offset is
    smaller, but y's is larger, so the next measured move waits (flash-lite made two such moves
    before this check looked at each direction)."""
    core = instrument({"y": "down"}, x=-585.0, y=158.0)
    _, _, tools = guarded(core, tmp_path)
    first = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert "error" not in call(tools, "move_relative", deltas=first)
    second = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert refused(call(tools, "move_relative", deltas=second))


@pytest.mark.parametrize("scale, axis_swap", [(1.5, False), (1.0, True)])
def test_a_move_off_the_measurement_waits_for_run(tmp_path, scale, axis_swap):
    core = instrument(x=-400.0, y=250.0)
    _, _, tools = guarded(core, tmp_path)
    move = call(tools, "look", question="?")["kept"]["centre_move_um"]
    deltas = {"x": move["y"], "y": move["x"]} if axis_swap else {k: v * scale for k, v in move.items()}
    assert refused(call(tools, "move_relative", deltas=deltas))


def test_measured_moves_are_capped_per_request(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEASURED_MOVES_MAX", 1)
    core = instrument(x=-400.0, y=250.0)
    _, _, tools = guarded(core, tmp_path)
    move = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert "error" not in call(tools, "move_relative", deltas=move)
    again = call(tools, "look", question="?")["kept"]["centre_move_um"]
    assert refused(call(tools, "move_relative", deltas=again))


def test_focus_moves_go_to_the_best_focus_or_search_in_small_steps(tmp_path):
    core = instrument(f=5130.0)
    _, store, tools = guarded(core, tmp_path)
    call(tools, "look", question="?")
    assert refused(call(tools, "move_relative", deltas={"f": 150}))           # too large a search step
    for _ in range(2):
        assert "error" not in call(tools, "move_relative", deltas={"f": 100})  # search steps
        call(tools, "look", question="?")
    best = fh.sample_map(store.frames)["groups"][0]["best_focus"]             # frames at 5000, 5100, 5200
    assert "edge" not in best
    assert "error" not in call(tools, "move_absolute", targets={"f": best["f"]})
    assert abs(core.truth()["focus_error_um"]) <= 25
    call(tools, "look", question="?")
    assert refused(call(tools, "move_absolute", targets={"f": 5400}))         # neither the best nor a small step


def test_light_changes_within_a_factor_of_two_after_a_fresh_look(tmp_path):
    core = instrument()
    _, _, tools = guarded(core, tmp_path)
    call(tools, "look", question="?")
    assert "error" not in call(tools, "set_intensity", intensity=20)          # 10 -> 20
    assert refused(call(tools, "set_intensity", intensity=30))                # no fresh look since
    call(tools, "look", question="?")
    assert refused(call(tools, "set_intensity", intensity=80))                # 20 -> 80 is four times
    assert "error" not in call(tools, "set_camera", camera_exposure_time=0.04)
    assert json.loads(json.dumps(core.state["camera_exposure_time"])) == 0.04


def test_a_move_past_one_field_waits_for_run_even_when_measured(tmp_path):
    """A calibration that came out ten times too small asks for a move ten times too large: past
    one field of view it waits for Run, whatever the frame says."""
    field = 2048.0
    fh.Calibration(tmp_path / "calibration.json").store("1x", [[0.1 / field, 0.0], [0.0, 0.1 / field]], "09:00:00", 205)
    core = instrument(x=-400.0, y=0.0)
    _, _, tools = guarded(core, tmp_path)
    kept = call(tools, "look", question="?")["kept"]
    assert kept["scale"] == "calibrated" and kept["centre_move_um"]["x"] == pytest.approx(-4000, abs=400)
    assert refused(call(tools, "move_relative", deltas=kept["centre_move_um"]))
