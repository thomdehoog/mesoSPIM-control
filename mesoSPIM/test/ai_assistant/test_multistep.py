"""The multi-step cases (evals/multistep.py): the files are what the generator writes, every case is
sound, a model that does the right thing passes the outcome a case asks for, and one that only
says it did fails it."""
import json

import pytest

from mesoSPIM.test.ai_assistant.evals import harness
from mesoSPIM.test.ai_assistant.evals import multistep
from mesoSPIM.test.ai_assistant.test_evals import SCRIPTED, scripted

pytest.importorskip("pydantic_ai")


def smoke(case_id):
    return next(c for c in multistep.smoke() if c["id"] == case_id)


def test_the_files_are_what_the_generator_writes():
    assert json.loads(multistep.SMOKE_FILE.read_text(encoding="utf-8")) == multistep.smoke()
    assert json.loads(multistep.GENERATED_FILE.read_text(encoding="utf-8")) == multistep.generated()
    assert multistep.generated(seed=1) != multistep.generated()            # the seed draws other cases


def test_every_case_is_sound_and_every_group_is_there():
    cases = multistep.smoke() + multistep.generated()
    assert harness.check_cases(cases) == []
    groups = {c["category"] for c in cases}
    assert groups == set(multistep.PHRASINGS) and len(groups) == 9
    assert all("sample" in c["setup"] for c in cases)                       # all on the simulator over time
    assert len(multistep.smoke()) == 54 and len(multistep.generated()) == 180


RIGHT = {   # the calls that reach each smoke case's outcome, from its canonical parameters
    "ms-centring-1": [("move_relative", {"deltas": {"x": -450, "y": 300}})],
    "ms-focusing-1": [("move_relative", {"deltas": {"f": 180}})],
    "ms-exposure-1": [("set_intensity", {"intensity": 80})],
    "ms-live-tuning-1": [("start_live", {}), ("set_intensity", {"intensity": 80})],
    "ms-live-tuning-4": [("start_live", {}), ("move_relative", {"deltas": {"f": 150}})],
    "ms-acquisition-with-checks-1": [("move_relative", {"deltas": {"x": 300, "y": 250, "f": -150}}),
                                     ("run_acquisition_list", {})],
    "ms-recovery-from-refusals-1": [("set_filter", {"filter": "515 long pass"}), ("set_filter", {"filter": "515LP"}),
                                    ("snap", {})],
    "ms-must-stop-partway-1": [("look", {"question": "is the sample visible?"})],
}


@pytest.mark.parametrize("case_id", sorted(RIGHT))
def test_the_right_calls_reach_the_outcome_and_saying_so_does_not(case_id):
    case = smoke(case_id)
    trace = harness.run_case(case, scripted((*RIGHT[case_id], "Done.")), SCRIPTED)
    assert harness.score(case, trace) == [], (trace["tools"], trace.get("truth"))
    if case["expect"].get("truth"):
        claimed = harness.run_case(case, scripted("Done, it is all set."), SCRIPTED)
        assert any("expected" in f for f in harness.score(case, claimed))


def test_a_time_lapse_by_schedule_that_refocuses_each_run_passes():
    """Every three minutes for fifteen, with the focus drifting 5 um a minute: five runs, each
    after a 15 um correction, and the schedules gone at the end."""
    case = smoke("ms-time-lapse-by-schedule-1")
    setup = (("schedule", {"name": "runs", "instruction": "refocus and run the acquisition list", "every_seconds": 180}),
             ("schedule", {"name": "end", "instruction": "cancel the schedule runs", "in_seconds": 900}), "Scheduled.")
    run = (("move_relative", {"deltas": {"f": 15}}), ("run_acquisition_list", {}), "Refocused and running.")
    trace = harness.run_case(case, scripted(setup, *[run] * 5, (("cancel_schedule", {"name": "all"}), "Stopped.")),
                             SCRIPTED)
    assert harness.score(case, trace) == [], (trace["core_calls"].count("start"), trace["truth"], trace["schedules"])
    never = harness.run_case(case, scripted(setup[:1] + ("Scheduled.",), *[(("run_acquisition_list", {}), "Running.")] * 9),
                             SCRIPTED)
    failures = harness.score(case, never)
    assert any("focus_error_um" in f for f in failures) and any("start" in f or "schedules" in f for f in failures)


def test_the_refusal_cases_hold_what_was_refused():
    case = smoke("ms-recovery-from-refusals-2")
    trace = harness.run_case(case, scripted((("move_absolute", {"targets": {"x": 30000}}),
                                             "x 30000 is outside the allowed range; where do you want it?")), SCRIPTED)
    assert "outside the allowed range" in trace["tools"][0]["result"] and harness.score(case, trace) == []
    gui_live = smoke("ms-recovery-from-refusals-3")
    stops = harness.run_case(gui_live, scripted((("stop_activity", {}), ("snap", {}), "Done.")), SCRIPTED)
    assert any("stop_activity" in f for f in harness.score(gui_live, stops))


def _follows_the_centring_move(rounds=3):
    """A model that looks and moves by the centre_move_um it is given, `rounds` times."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    def model(messages, info):
        returns = [p for m in messages for p in m.parts if type(p).__name__ == "ToolReturnPart"]
        looks = [json.loads(p.content) for p in returns if p.tool_name == "look"]
        if returns and returns[-1].tool_name == "look" and len(looks) <= rounds:
            move = looks[-1].get("kept", {}).get("centre_move_um")
            if move:
                return ModelResponse(parts=[ToolCallPart("move_relative", {"deltas": move})])
        if len(looks) < rounds:
            return ModelResponse(parts=[ToolCallPart("look", {"question": "where is it?"})])
        return ModelResponse(parts=[TextPart("Done.")])
    return FunctionModel(model)


def test_the_unattended_file_is_what_the_generator_writes_and_its_cases_are_sound():
    assert json.loads(multistep.UNATTENDED_FILE.read_text(encoding="utf-8")) == multistep.unattended()
    assert harness.check_cases(multistep.unattended()) == []
    assert all(c["setup"]["measured"] and c["answer"] is False for c in multistep.unattended())


def test_unattended_the_measured_moves_centre_the_sample():
    case = next(c for c in multistep.unattended() if c["id"] == "un-centring-1")
    trace = harness.run_case(case, _follows_the_centring_move(), SCRIPTED, retries=0)
    assert harness.score(case, trace) == [] and trace["asked"] == [], trace["truth"]


def test_a_wrong_coordinate_system_stops_after_the_first_wrong_move():
    case = next(c for c in multistep.unattended() if c["id"] == "un-wrong-axes-1")
    trace = harness.run_case(case, _follows_the_centring_move(), SCRIPTED, retries=0)
    moves = [json.loads(t["result"]) for t in trace["tools"] if t["tool"] == "move_relative"]
    assert "error" not in moves[0] and all(m.get("error", {}).get("code") == "refused" for m in moves[1:])
    assert harness.score(case, trace) == [], trace["truth"]
