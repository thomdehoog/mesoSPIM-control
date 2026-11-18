"""Saved conversations, and the search over them and the documentation."""
import time
from pathlib import Path

from mesoSPIM.src.ai_assistant import conversations, search

TURNS = [{"time": "14:02:11", "prompt": "centre my cleared brain please", "readout": {"big": "x" * 500},
          "tools": [{"tool": "move_relative", "args": {"deltas": {"x": 120}}, "result": '{"status": "completed"}'}],
          "reply": "The sample is centred now."},
         {"time": "14:05:00", "prompt": "set the 488 laser to ten percent", "tools": [], "reply": "Done."}]


def _docs(tmp_path):
    docs = tmp_path / "docs"
    (docs / "_static").mkdir(parents=True, exist_ok=True)
    (docs / "_static" / "ignored.md").write_text("# Hidden\n\nlight-sheet alignment secrets\n")
    (docs / "guide.md").write_text("Intro text.\n\n# Light-sheet alignment\n\nTurn the galvo amplitude to zero, then "
                                   "align the sheet.\n\n## ETL calibration\n\nThe ETL offset moves the waist.\n")
    (docs / "install.rst").write_text("Installation\n============\n\nInstall with pip.\n\nDrivers\n-------\n\n"
                                      "Install the camera driver first.\n")
    return docs


def test_a_chat_is_saved_listed_and_removed(tmp_path):
    recorder = conversations.Recorder(tmp_path / "chats", model="some-model", clock=lambda: 1_800_000_000)
    assert recorder.save([]) and not recorder.path.exists()            # nothing said, nothing written
    assert recorder.save(TURNS)
    document = conversations.read(recorder.path)
    assert document["model"] == "some-model" and len(document["turns"]) == 2
    assert "readout" not in document["turns"][0]                        # the readout is not kept
    listed = conversations.listing(tmp_path / "chats")
    assert listed[0]["title"] == "centre my cleared brain please" and listed[0]["turns"] == 2
    assert conversations.remove(recorder.path) and conversations.listing(tmp_path / "chats") == []


def test_a_folder_that_cannot_be_written_does_not_stop_the_chat(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("in the way")
    recorder = conversations.Recorder(blocker / "chats")
    assert recorder.save(TURNS) is False and "could not be saved" in recorder.problem


def test_documentation_is_cut_at_its_headings(tmp_path):
    places = {place: title for place, title, _ in search.documentation_passages(_docs(tmp_path))}
    assert places == {"guide.md#1": "guide.md", "guide.md#2": "guide.md: Light-sheet alignment",
                      "guide.md#3": "guide.md: ETL calibration", "install.rst#1": "install.rst: Installation",
                      "install.rst#2": "install.rst: Drivers"}


def test_search_finds_the_passage_and_reads_it_in_full(tmp_path):
    docs = _docs(tmp_path)
    conversations.Recorder(tmp_path / "chats", clock=lambda: 1_800_000_000).save(TURNS)
    found = search.search("how do I do the light-sheet alignment?", documentation=docs,
                          conversation_folder=tmp_path / "chats")
    assert found["results"][0]["place"] == "guide.md#2" and found["results"][0]["source"] == "documentation"
    earlier = search.search("cleared brain", where="conversations", conversation_folder=tmp_path / "chats")
    place = earlier["results"][0]["place"]
    assert place.endswith(".json#1")
    full = search.read_passage(place, conversation_folder=tmp_path / "chats")
    assert "move_relative" in full["text"] and "The sample is centred now." in full["text"]
    assert "Turn the galvo" in search.read_passage("guide.md#2", documentation=docs)["text"]
    assert "error" in search.read_passage("guide.md#99", documentation=docs)
    assert "error" in search.read_passage("nonsense", documentation=docs)


def test_search_says_what_it_could_not_search(tmp_path):
    nothing = search.search("alignment", documentation=None, conversation_folder=tmp_path / "none", saving=False)
    assert nothing["results"] == [] and len(nothing["notes"]) == 2
    assert "not saved" in nothing["notes"][1]
    assert "error" in search.search("?!", documentation=_docs(tmp_path))
    assert "error" in search.search("alignment", where="everywhere")
    unmatched = search.search("zebrafish", where="documentation", documentation=_docs(tmp_path))
    assert unmatched["results"] == [] and "Nothing matched" in unmatched["notes"][0]


def test_punctuation_in_a_question_is_never_a_command(tmp_path):
    for question in ('"unbalanced', "NEAR(a b)", "a AND OR NOT", "col:umn *", "(((", "it's 50% - ok?"):
        found = search.search(question, where="documentation", documentation=_docs(tmp_path))
        assert "results" in found or found["error"]["code"] == "validation"     # "(((" has no word in it


def test_the_real_documentation_is_searched_quickly():
    docs = Path(__file__).resolve().parents[3] / "docs" / "source"
    started = time.perf_counter()
    found = search.search("remote control token", where="documentation", documentation=docs)
    assert found["results"] and time.perf_counter() - started < 2.0
