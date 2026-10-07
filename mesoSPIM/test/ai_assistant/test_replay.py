"""The 158 cases, their 158 held-out twins and the 234 multi-step cases as a regression suite: each
is replayed from a recorded model run (evals/replay.py) through the real agent, tools, guard and dispatcher, and must
score as it did when recorded, with no more than GROWTH_ALLOWED more estimated tokens."""
import pytest

from mesoSPIM.test.ai_assistant.evals import harness
from mesoSPIM.test.ai_assistant.evals import replay
from mesoSPIM.test.ai_assistant.test_evals import SCRIPTED, scripted

pytest.importorskip("pydantic_ai")

FILES = tuple(harness.CASES_FILE.with_name(name) for name in
              ("cases.json", "cases_holdout.json", "cases_multistep.json", "cases_generated.json"))


def _recorded_cases():
    for path in FILES:
        recording = replay.load_recording(path)
        for case in harness.load_cases(path):
            yield pytest.param(case, recording.get(case["id"]), id=case["id"])


@pytest.mark.parametrize("case, recorded", list(_recorded_cases()))
def test_the_case_replays_as_recorded(case, recorded):
    assert recorded is not None, "not recorded: run evals/run.py --record"
    trace = replay.replay_case(case, recorded)
    assert trace["failures"] == recorded["failures"], trace["tools"]
    assert trace["tokens"] <= recorded["tokens"] * (1 + replay.GROWTH_ALLOWED), (
        f"{trace['tokens']} estimated tokens, {recorded['tokens']} recorded")


def test_a_recorded_run_replays_to_the_same_calls_and_size():
    case = next(c for c in harness.load_cases() if c["id"] == "move-relative-mm")
    trace = replay.record_case(case, lambda: scripted((("move_relative", {"deltas": {"x": -100}}), "Moved.")), SCRIPTED)
    recorded = trace["recording"]
    assert recorded["responses"] == [[{"tool": "move_relative", "args": {"deltas": {"x": -100}}}], [{"text": "Moved."}]]
    assert recorded["failures"] == [] and recorded["provider_tokens"]["requests"] == 2
    again = replay.replay_case(case, dict(recorded, tokens=None))
    assert [t["tool"] for t in again["tools"]] == ["move_relative"] and again["failures"] == []
    assert again["requests"] == 2 and again["tokens"] > 1000            # instructions and tools are counted


def test_a_replay_that_runs_out_of_answers_says_so():
    case = next(c for c in harness.load_cases() if c["id"] == "move-relative-mm")
    recorded = {"model": "m", "vision": False, "profile": None, "responses": [], "eyes": []}
    trace = replay.replay_case(case, recorded)
    assert trace["replies"] == [replay.REPLAY_ENDED] and trace["failures"]
