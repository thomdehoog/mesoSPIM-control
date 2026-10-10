"""Skills: the loader, the prompt's list, load_skill, and the harness checks that score them."""
import json
import threading
from pathlib import Path

import pytest

from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant import config, skills
from mesoSPIM.test.ai_assistant.evals import harness
from mesoSPIM.test.ai_assistant.test_evals import SCRIPTED, scripted

BENCHMARK = Path(__file__).parent / "evals" / "skills"
SKILL = "---\nname: centring\ndescription: Bring the sample to the middle.\ntools: look, move_relative\n---\n1. look.\n"


def test_a_skill_is_its_head_and_its_steps():
    skill = skills.parse(SKILL)
    assert (skill.name, skill.description, skill.tools, skill.body) == (
        "centring", "Bring the sample to the middle.", ("look", "move_relative"), "1. look.")


@pytest.mark.parametrize("text, problem", [
    ("name: a\n", "starts with a head"),
    ("---\nname: a\n---\nsteps", "needs a name and a description"),
    ("---\nname: a\ndescription: b\n---\n", "has no steps"),
    ("---\nname: a\ndescription: b\n---\n" + "x" * config.SKILL_MAX_CHARS, "characters"),
])
def test_a_file_that_is_not_a_skill_says_why(text, problem):
    with pytest.raises(ValueError, match=problem):
        skills.parse(text)


def test_a_folder_loads_by_name_and_reports_what_it_left_out(tmp_path):
    (tmp_path / "centring.md").write_text(SKILL, encoding="utf-8")
    (tmp_path / "again.md").write_text(SKILL, encoding="utf-8")
    (tmp_path / "broken.md").write_text("no head", encoding="utf-8")
    found, problems = skills.load(tmp_path)
    assert list(found) == ["centring"] and len(problems) == 2
    assert skills.load(tmp_path / "missing") == ({}, [])            # no folder is no skills


def test_the_folder_is_next_to_the_microscope_config(tmp_path):
    cfg = type("Cfg", (), {"__file__": str(tmp_path / "demo_config.py")})
    assert skills.folder_of(cfg) == tmp_path / "skills" and skills.folder_of(None) is None


def test_a_skill_naming_a_tool_that_is_not_offered_is_reported():
    found = {"s": skills.parse(SKILL.replace("move_relative", "teleport"))}
    assert skills.unknown_tools(found, ["look", "move_relative"]) == ["skill centring: no tool teleport"]


def test_the_benchmark_skills_load_and_name_only_offered_tools():
    found, problems = skills.load(BENCHMARK)
    assert problems == [] and len(found) == 5
    offered = [c.name for c in ai.offered_commands()] + ["look", "ask_eyes", "calibrate"]
    assert skills.unknown_tools(found, offered) == []


def test_without_skills_the_prompt_and_the_tools_are_unchanged():
    assert ai.build_system_prompt() == ai.build_system_prompt(skills={})
    pytest.importorskip("pydantic_ai")
    assert "load_skill" not in {t.name for t in ai.build_tools(None, threading.Event())}


def test_with_skills_the_prompt_lists_each_by_description_only_and_load_skill_returns_the_steps():
    pytest.importorskip("pydantic_ai")
    found, _ = skills.load(BENCHMARK)
    prompt = ai.build_system_prompt(skills=found)
    assert "# Skills" in prompt and "- centring: Bring the sample to the middle of the camera image." in prompt
    assert found["centring"].body not in prompt                     # the steps come only on demand
    tool = next(t for t in ai.build_tools(None, threading.Event(), skills=found) if t.name == "load_skill")
    assert json.loads(tool.function(name="centring"))["instructions"] == found["centring"].body
    assert "error" in json.loads(tool.function(name="nothing"))


def _case(**expect):
    return {"id": "c", "category": "skills", "prompt": "Centre the sample.", "expect": expect}


def test_the_harness_scores_the_skill_loaded_first_and_the_order_of_calls():
    found, _ = skills.load(BENCHMARK)
    def run(*calls):
        return harness.run_case(_case(), scripted(calls + ("Done.",)), SCRIPTED, skills=found)
    good = run(("load_skill", {"name": "centring"}), ("look", {"question": "where?"}))
    assert harness.score(_case(skill="centring", calls_in_order=["load_skill", "look"]), good) == []
    look_first = run(("look", {"question": "where?"}), ("load_skill", {"name": "centring"}))
    assert harness.score(_case(skill="centring"), look_first) == []          # a look before it is fine
    late = run(("move_relative", {"deltas": {"x": 10}}), ("load_skill", {"name": "centring"}))
    assert harness.score(_case(skill="centring"), late) == ["load_skill came after move_relative"]
    assert harness.score(_case(calls_in_order=["load_skill", "look"]), look_first) == [
        "expected the calls in this order: ['load_skill', 'look']"]
    wrong = run(("load_skill", {"name": "focusing"}),)
    assert harness.score(_case(skill="centring"), wrong) == ["loaded skill focusing, expected centring"]
    assert harness.score(_case(skill=None), wrong) == ["loaded skill focusing for a request that needs none"]
    assert harness.score(_case(skill="centring"), run(("look", {"question": "q"}),)) == ["expected load_skill centring"]

