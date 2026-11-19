"""System and personal skills: what the setup window lists, and saving, changing and removing."""
import types

import pytest

from mesoSPIM.src.ai_assistant import settings as assistant_settings
from mesoSPIM.src.ai_assistant import skills

GOOD = "---\nname: {name}\ndescription: {description}\ntools: look, move_relative\n---\nStep one.\n"


def _folders(tmp_path):
    system, personal = tmp_path / "skills", tmp_path / "home" / "mesoSPIM" / "skills"
    system.mkdir(parents=True)
    personal.mkdir(parents=True)
    return system, personal


def test_the_library_lists_both_kinds_and_marks_what_cannot_be_used(tmp_path):
    system, personal = _folders(tmp_path)
    (system / "centring.md").write_text(GOOD.format(name="centring", description="Centre the sample."))
    (system / "broken.md").write_text("no head here")
    (personal / "mine.md").write_text(GOOD.format(name="overview", description="A quick overview scan."))
    (personal / "centring.md").write_text(GOOD.format(name="centring", description="My own centring."))
    entries = skills.library(system, personal, offered=["look"])
    by_key = {entry.key: entry for entry in entries}
    assert [entry.kind for entry in entries] == ["system", "system", "personal", "personal"]
    assert not by_key["broken"].usable and "head" in by_key["broken"].problem
    assert by_key["centring"].kind == "system" and by_key["centring (personal)"].kind == "personal"
    assert by_key["overview"].missing_tools == ("move_relative",)
    ticked = skills.chosen(entries, switched_off={"overview"})
    assert sorted(ticked) == ["centring", "centring (personal)"]


def test_a_personal_skill_is_saved_changed_and_removed(tmp_path):
    _, personal = _folders(tmp_path)
    saved = skills.save_personal(personal, "Quick overview", "A quick overview scan.", "Step one.\nStep two.")
    assert saved.kind == "personal" and saved.path.name == "quick-overview.md"
    assert skills.load(personal, "personal")[0]["Quick overview"].body == "Step one.\nStep two."
    with pytest.raises(ValueError, match="exists already"):
        skills.save_personal(personal, "Quick overview", "Again.", "Other steps.")
    changed = skills.change_personal(saved, body="Only one step now.")
    assert changed.description == "A quick overview scan." and changed.body == "Only one step now."
    moved = skills.remove_personal(changed.path)
    assert moved.parent.name == "deleted" and moved.exists() and not changed.path.exists()
    assert skills.load(personal, "personal")[0] == {}


def test_a_skill_without_steps_or_description_is_refused(tmp_path):
    _, personal = _folders(tmp_path)
    with pytest.raises(ValueError):
        skills.save_personal(personal, "Empty", "Has no steps.", "   ")
    with pytest.raises(ValueError):
        skills.save_personal(personal, "No description", "", "Step one.")
    assert list(personal.glob("*.md")) == []


def test_the_assistant_may_not_change_a_system_skill(tmp_path):
    system, _ = _folders(tmp_path)
    (system / "centring.md").write_text(GOOD.format(name="centring", description="Centre the sample."))
    skill = skills.load(system)[0]["centring"]
    with pytest.raises(ValueError, match="system skill"):
        skills.change_personal(skill, body="Something else.")


def test_the_config_block_gives_each_person_their_own_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    cfg = types.SimpleNamespace(__file__=str(tmp_path / "config" / "demo_config.py"),
                                ai_assistant={"save_conversations": True, "vision": False, "look_bin": 3,
                                              "axes": {"x": "left"}, "system_prompt": "missing.md"})
    found = assistant_settings.read(cfg)
    assert found.system_skills == tmp_path / "config" / "skills"
    assert found.personal_skills == tmp_path / "home" / "mesoSPIM" / "skills"
    assert found.conversations == tmp_path / "home" / "mesoSPIM" / "conversations"
    assert found.save_conversations and not found.vision
    assert found.axes == {"x": "left", "y": "up", "z": "toward the camera"}
    assert found.look_bin == 2 and len(found.problems) == 2      # the bin and the missing prompt file
    assert assistant_settings.read(types.SimpleNamespace()).save_conversations is False


def test_ticks_are_remembered(tmp_path):
    found = assistant_settings.Settings(personal_skills=tmp_path / "me" / "skills")
    assert assistant_settings.read_choices(found) == {}
    assert assistant_settings.write_choices(found, {"skills_off": ["overview"]})
    assert assistant_settings.read_choices(found) == {"skills_off": ["overview"]}


def test_the_vision_model_block_names_a_second_model(tmp_path):
    def read(**block):
        return assistant_settings.read(types.SimpleNamespace(__file__=str(tmp_path / "c.py"), ai_assistant=block))
    none = read()
    assert (none.vision_provider, none.vision_model, none.problems) == ("", "", ())
    found = read(vision_model={"provider": "Gemini", "model": " gemini-3.5-flash-lite "})
    assert (found.vision_provider, found.vision_model, found.problems) == ("Gemini", "gemini-3.5-flash-lite", ())
    own = read(vision_model={"provider": "OpenAI-style", "address": "http://eyes.example.org/v1"})
    assert (own.vision_provider, own.vision_model, own.vision_address) == ("OpenAI-style", "", "http://eyes.example.org/v1")
    for wrong in ({"provider": "Nobody"}, {"model": "x"}, "Gemini"):
        bad = read(vision_model=wrong)
        assert bad.vision_provider == "" and len(bad.problems) == 1 and "vision_model" in bad.problems[0]
