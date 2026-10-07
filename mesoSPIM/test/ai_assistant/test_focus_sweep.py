"""focus_sweep (roadmap H2, pulled forward): a measuring block that finds the best focus near the
current one; the model moves there. Scored against the simulator's truth."""
import json

import pytest

from mesoSPIM.test.ai_assistant.evals import harness, multistep
from mesoSPIM.test.ai_assistant.test_evals import SCRIPTED, scripted
from mesoSPIM.test.ai_assistant.test_frames import call, instrument, tools_on

pytest.importorskip("pydantic_ai")


@pytest.mark.parametrize("off", [-250, -80, 0, 130, 260])
def test_the_sweep_finds_the_best_focus_and_goes_back(tmp_path, off):
    core = instrument(f=5000.0 + off)
    _, store, tools = tools_on(core, tmp_path)
    found = call(tools, "focus_sweep")
    assert found["best_f"] == pytest.approx(5000 + off, abs=15) and "edge" not in found
    assert core.state["position"]["f_pos"] == 5000.0 and found["start_f"] == 5000.0   # back where it started
    assert len(found["frames"]) == 9 and store.frames.frames[-1]["source"] == "focus_sweep"
    call(tools, "move_absolute", targets={"f": found["best_f"]})
    assert abs(core.truth()["focus_error_um"]) <= 15


def test_a_focus_beyond_the_range_is_an_edge_and_an_empty_field_no_answer(tmp_path):
    core = instrument(f=5450.0)
    _, _, tools = tools_on(core, tmp_path)
    found = call(tools, "focus_sweep")
    assert found["edge"] == "search higher f" and found["best_f"] == 5300.0
    empty = instrument(x=9000.0)
    _, _, tools = tools_on(empty, tmp_path)
    assert "no frame of the sweep shows the sample" in call(tools, "focus_sweep")["error"]["message"]


def test_a_focusing_case_passes_with_the_sweep_and_the_move():
    case = next(c for c in multistep.smoke() if c["id"] == "ms-focusing-1")

    def sweep_then_move(messages, info):
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        returns = [p for m in messages for p in getattr(m, "parts", []) if type(p).__name__ == "ToolReturnPart"]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("focus_sweep", {})])
        if returns[-1].tool_name == "focus_sweep":
            best = json.loads(returns[-1].content)["best_f"]
            return ModelResponse(parts=[ToolCallPart("move_absolute", {"targets": {"f": best}})])
        return ModelResponse(parts=[TextPart("In focus.")])
    from pydantic_ai.models.function import FunctionModel
    trace = harness.run_case(case, FunctionModel(sweep_then_move), SCRIPTED, retries=0)
    assert harness.score(case, trace) == [], trace["truth"]


def test_a_time_lapse_that_sweeps_before_each_run_stays_in_focus():
    """Every three minutes for fifteen with the focus drifting 5 um a minute: a model that sweeps
    and moves to the best focus before each run ends in focus."""
    case = next(c for c in multistep.smoke() if c["id"] == "ms-time-lapse-by-schedule-1")
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    def model(messages, info):
        turn = messages[[i for i, m in enumerate(messages) if type(m.parts[0]).__name__ == "UserPromptPart"][-1]:]
        prompt = turn[0].parts[0].content
        returns = [p for m in turn for p in m.parts if type(p).__name__ == "ToolReturnPart"]
        done = [p.tool_name for p in returns]
        if "[scheduled" not in prompt:
            if not done:
                return ModelResponse(parts=[
                    ToolCallPart("schedule", {"name": "runs", "instruction": "focus and run the list", "every_seconds": 180}),
                    ToolCallPart("schedule", {"name": "end", "instruction": "cancel the schedule runs", "in_seconds": 900})])
            return ModelResponse(parts=[TextPart("Scheduled.")])
        if "'end'" in prompt:
            return ModelResponse(parts=[ToolCallPart("cancel_schedule", {"name": "all"})] if not done else [TextPart("Stopped.")])
        if not done:
            return ModelResponse(parts=[ToolCallPart("focus_sweep", {"range_um": 100, "step_um": 20})])
        if done[-1] == "focus_sweep":
            best = json.loads(returns[-1].content)["best_f"]
            return ModelResponse(parts=[ToolCallPart("move_absolute", {"targets": {"f": best}})])
        if done[-1] == "move_absolute":
            return ModelResponse(parts=[ToolCallPart("run_acquisition_list", {})])
        return ModelResponse(parts=[TextPart("Focused and running.")])
    trace = harness.run_case(case, FunctionModel(model), SCRIPTED, retries=0)
    assert harness.score(case, trace) == [], (trace["truth"], trace["core_calls"].count("start"), trace["error"])
