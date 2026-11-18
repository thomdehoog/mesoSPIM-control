"""The tools a chat started from the window has beyond the microscope's calls."""
import asyncio
import json
import threading

from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant import config, conversations, skills
from mesoSPIM.src.remote_control.servers import Acceptor
from mesoSPIM.test.remote_control.support.fakes import RecordingCore

HEAD = "---\nname: {name}\ndescription: {description}\n---\n{body}\n"


def _desk(tmp_path, answers=(), **more):
    system, personal = tmp_path / "skills", tmp_path / "me" / "skills"
    system.mkdir(parents=True, exist_ok=True)
    personal.mkdir(parents=True, exist_ok=True)
    (system / "centring.md").write_text(HEAD.format(name="centring", description="Centre the sample.", body="Look, then move."))
    (personal / "overview.md").write_text(HEAD.format(name="overview", description="A quick overview.", body="Snap once."))
    (personal / "off.md").write_text(HEAD.format(name="unticked", description="Not for this chat.", body="Nothing."))
    entries = skills.library(system, personal)
    asked, answers = [], list(answers)

    def confirm(kind, details):
        asked.append((kind, details))
        return answers.pop(0)
    desk = ai.Desk(skills=skills.chosen(entries, {"unticked"}), switched_off=("unticked",), personal_skills=personal,
                   confirm=confirm, status=lambda: {"model": "some-model", "context_tokens": 1200}, **more)
    return desk, asked, personal


def _tools(desk, cancel=None, calls=None):
    made = ai.build_tools(Acceptor(RecordingCore()), cancel or threading.Event(), desk=desk,
                          on_call=(lambda name, args: calls.append(name)) if calls is not None else None)
    return {tool.name: tool for tool in made}


def _call(tool, **args):
    result = tool.function(**args)
    return json.loads(asyncio.run(result) if asyncio.iscoroutine(result) else result)


def test_a_chat_without_a_desk_has_the_microscope_calls_only():
    names = {tool.name for tool in ai.build_tools(Acceptor(RecordingCore()), threading.Event())}
    assert not names & {"get_status", "search", "read_passage", "save_skill", "edit_skill", "load_skill"}


def test_get_status_carries_the_microscope_and_the_assistant(tmp_path):
    desk, _, _ = _desk(tmp_path)
    calls = []
    status = _call(_tools(desk, calls=calls)["get_status"])
    assert status["assistant"] == {"model": "some-model", "context_tokens": 1200}
    assert {"state", "busy", "position", "optics", "disk", "warnings"} <= set(status["microscope"])
    assert len(json.dumps(status)) < 1500 and calls == ["get_status"]        # small enough to ask often


def test_load_skill_gives_ticked_skills_and_refuses_an_unticked_one(tmp_path):
    desk, _, _ = _desk(tmp_path)
    load = _tools(desk)["load_skill"]
    assert _call(load, name="centring")["instructions"] == "Look, then move."
    refused = _call(load, name="unticked")["error"]
    assert refused["code"] == "refused" and "not switched on" in refused["message"]
    assert _call(load, name="never heard of it")["error"]["code"] == "validation"


def test_the_prompt_lists_ticked_skills_only_and_personal_ones_apart(tmp_path):
    desk, _, _ = _desk(tmp_path)
    prompt = ai.build_system_prompt(skills=desk.skills)
    assert "- centring: Centre the sample." in prompt and "- overview: A quick overview." in prompt
    assert prompt.index(config.PERSONAL_SKILLS_LINE) < prompt.index("- overview")
    assert "unticked" not in prompt


def test_a_skill_is_saved_only_after_the_operator_says_yes(tmp_path):
    desk, asked, personal = _desk(tmp_path, answers=[False, True])
    saved = []
    desk.on_skill_saved = lambda skill: saved.append(skill.name)
    save = _tools(desk)["save_skill"]
    wanted = dict(name="Tile a brain", description="Tile a whole cleared brain.", steps="Build the list.\nRun it.",
                  tools=["build_tiling_list"])
    assert _call(save, **wanted)["error"]["message"] == config.SKILL_NOT_CONFIRMED
    assert not (personal / "tile-a-brain.md").exists() and saved == []
    assert _call(save, **wanted)["saved"] == "Tile a brain"
    assert asked[1] == ("save_skill", {"name": "Tile a brain", "description": "Tile a whole cleared brain.",
                                       "steps": "Build the list.\nRun it.", "tools": ["build_tiling_list"]})
    written = skills.load(personal, "personal")[0]["Tile a brain"]
    assert written.body == "Build the list.\nRun it." and written.tools == ("build_tiling_list",) and saved == ["Tile a brain"]


def test_a_skill_that_is_not_valid_is_refused_before_the_operator_is_asked(tmp_path):
    desk, asked, _ = _desk(tmp_path, answers=[True])
    save = _tools(desk)["save_skill"]
    assert _call(save, name="No steps", description="Has none.", steps="  ")["error"]["code"] == "validation"
    assert _call(save, name="overview", description="Again.", steps="Other.")["error"]["code"] == "validation"
    assert asked == []


def test_cancel_counts_as_no(tmp_path):
    desk, asked, personal = _desk(tmp_path, answers=[True])
    cancel = threading.Event()
    cancel.set()
    outcome = _call(_tools(desk, cancel)["save_skill"], name="New one", description="Something.", steps="A step.")
    assert outcome["error"]["message"] == config.SKILL_NOT_CONFIRMED and asked == []
    assert not (personal / "new-one.md").exists()


def test_a_personal_skill_is_changed_and_a_system_skill_is_not(tmp_path):
    desk, asked, personal = _desk(tmp_path, answers=[True])
    tools = _tools(desk)
    assert tools["edit_skill"].function_schema.json_schema["properties"]["name"]["enum"] == ["overview"]
    assert _call(tools["edit_skill"], name="centring", steps="Other.")["error"]["code"] == "refused"
    assert asked == []
    assert _call(tools["edit_skill"], name="overview", steps="Snap twice.")["saved"] == "overview"
    assert asked[0][1]["was"] == "Snap once." and asked[0][1]["description"] == "A quick overview."
    assert skills.load(personal, "personal")[0]["overview"].body == "Snap twice."
    assert _call(tools["load_skill"], name="overview")["instructions"] == "Snap twice."   # this chat follows it


def test_search_and_read_passage_reach_the_folders_of_the_desk(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Light-sheet alignment\n\nSet the galvo amplitude to zero first.\n")
    conversations.Recorder(tmp_path / "chats").save([{"time": "10:00:00", "prompt": "tile the brain", "tools": [],
                                                      "reply": "Tiled."}])
    desk, _, _ = _desk(tmp_path, documentation=docs, conversations=tmp_path / "chats", saving=True)
    tools = _tools(desk)
    found = _call(tools["search"], query="galvo amplitude")
    assert found["results"][0]["place"] == "guide.md#1"
    assert "galvo amplitude to zero" in _call(tools["read_passage"], place="guide.md#1")["text"]
    assert _call(tools["search"], query="brain", where="conversations")["results"][0]["source"] == "conversations"


def test_the_worker_asks_the_window_and_saves_the_chat(tmp_path, monkeypatch):
    desk, _, _ = _desk(tmp_path)
    worker = ai.AssistantWorker(Acceptor(RecordingCore()))
    worker.configure(ai.Endpoint.from_preset("Anthropic", api_key="k"))
    questions = []
    worker.sig_confirm.connect(lambda kind, details: (questions.append((kind, json.loads(details))), worker.answer(True)))
    worker.open_desk(desk, recorder_factory=lambda: conversations.Recorder(tmp_path / "chats", "claude-haiku-5-5"),
                     context_max_tokens=900_000)
    assert worker.ask_operator("save_skill", {"name": "x"}) is True and questions == [("save_skill", {"name": "x"})]
    status = worker.status()
    assert status["model"] == "claude-haiku-5-5" and status["context_max_tokens"] == 900_000
    assert status["skills"] == ["centring", "overview"] and status["conversations_saved"] is True

    class _Result:
        output = "Done."
        def new_messages(self): return []
        def all_messages(self): return []

    class _Agent:
        async def run(self, prompt, message_history=None): return _Result()
    monkeypatch.setattr(ai, "build_agent", lambda *a, **k: _Agent())
    worker.run_turn("move the sample a little")
    saved = conversations.listing(tmp_path / "chats")
    assert saved[0]["title"] == "move the sample a little" and saved[0]["turns"] == 1
    first = worker.recorder.path
    worker.reset()
    assert worker.recorder is not None and worker.tokens == 0 and first.exists()   # the cleared chat stays saved
