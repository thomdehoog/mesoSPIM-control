"""AiAssistantGUI logic, from source, using the QtWidgets stub in conftest.

The real QThread / worker hand-off is a real-PyQt concern (the smoke layer); here we test the
setup tab (provider, model, key, the two skill lists, Start Chat Window), the transport-busy
refusal, the questions the window asks, and the single-flight input lock, in isolation.
"""
import json
import types

import pytest

from PyQt5 import QtWidgets

from mesoSPIM.src.ai_assistant import gui as gui_module
from mesoSPIM.src.ai_assistant.gui import AiAssistantGUI


class _FakeTabWidget:
    def __init__(self):
        self._tabs = []

    def indexOf(self, widget):
        return self._tabs.index(widget) if widget in self._tabs else -1

    def addTab(self, widget, _label):
        self._tabs.append(widget)

    def insertTab(self, index, widget, _label):
        self._tabs.insert(index, widget)


class _FakeCore:
    """Records the assistant slots MainWindow would invoke and hands back an acceptor (or not)."""

    def __init__(self, acceptor):
        self._acceptor = acceptor
        self._assistant_acceptor = None
        self.calls = []

    def start_ai_assistant(self):
        self.calls.append("start_ai_assistant")
        self._assistant_acceptor = self._acceptor      # None simulates a busy transport

    def stop_ai_assistant(self):
        self.calls.append("stop_ai_assistant")
        self._assistant_acceptor = None


class _Signal:
    """A bound signal stand-in: connect stores slots, emit calls them."""

    def __init__(self):
        self._slots = []

    def connect(self, slot, *_a, **_k):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _FakeParent:
    """MainWindow as the tab sees it, including the Stop button's method and the stage stop."""

    def __init__(self, core):
        self.TabWidget = _FakeTabWidget()
        self.remote_control = object()
        self.TabWidget.addTab(self.remote_control, "Remote Control")
        self.core = core
        self.stops = 0
        self.sig_stop_movement = _Signal()

    def stop_acquisition_and_timelapse(self):
        self.stops += 1


def _collect(signal):
    got = []
    signal.connect(lambda *a: got.append(a[0] if len(a) == 1 else a))
    return got



HEAD = "---\nname: {name}\ndescription: {description}\ntools: {tools}\n---\nStep one.\n"


class _Worker:
    """What the tab touches on the worker and its thread, recorded."""

    def __init__(self):
        self.configured, self.desks, self.answers = [], [], []
        self.interrupted = self.resets = 0

    def configure(self, endpoint, vision=None):
        self.configured.append(endpoint)

    def open_desk(self, desk, system_prompt_file=None, recorder_factory=None, context_max_tokens=None):
        self.desks.append((desk, system_prompt_file, recorder_factory, context_max_tokens))

    def interrupt(self):
        self.interrupted += 1

    def answer(self, yes):
        self.answers.append(yes)

    def reset(self):
        self.resets += 1

    def run_turn(self, _text):
        pass

    def check_run(self):
        pass


class _Thread:
    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1

    def wait(self, _msec):
        return True


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """Every test gets a home folder and a config folder of its own, no password store, and no
    key in the environment, so nothing of the machine the tests run on leaks in."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    kept = {}
    monkeypatch.setattr(gui_module.keys, "can_remember", lambda: True)
    monkeypatch.setattr(gui_module.keys, "remembered", lambda provider: kept.get(provider, ""))
    monkeypatch.setattr(gui_module.keys, "remember", lambda provider, key: kept.__setitem__(provider, key) or True)
    monkeypatch.setattr(gui_module.keys, "forget", lambda provider: kept.pop(provider, None) is None or True)
    QtWidgets.QMessageBox.answers.clear()
    QtWidgets.QMessageBox.questions.clear()
    QtWidgets.QDialog.answers.clear()
    QtWidgets.QDialog.shown.clear()
    return kept


def _files(tmp_path):
    """A config folder with two system skills (one out of date) and a broken file, and a home
    folder with one personal skill."""
    system, personal = tmp_path / "config" / "skills", tmp_path / "home" / "mesoSPIM" / "skills"
    system.mkdir(parents=True)
    personal.mkdir(parents=True)
    (system / "centring.md").write_text(HEAD.format(name="centring", description="Centre the sample.", tools="look"))
    (system / "old.md").write_text(HEAD.format(name="old routine", description="Out of date.", tools="retired_call"))
    (system / "broken.md").write_text("no head here")
    (personal / "overview.md").write_text(HEAD.format(name="overview", description="A quick overview.", tools="look"))
    return system, personal


def _gui(tmp_path=None, acceptor=object(), **block):
    core = _FakeCore(acceptor=acceptor)
    if tmp_path is not None:
        core.cfg = types.SimpleNamespace(__file__=str(tmp_path / "config" / "demo_config.py"), ai_assistant=block)
    gui = AiAssistantGUI(_FakeParent(core))
    gui.checks_in_background = False                  # the key check answers at once, in the test's own thread
    gui.check_key = lambda endpoint: (bool(endpoint.api_key) or not endpoint.needs_key, "the provider's answer")
    return gui


def _with_worker(gui, monkeypatch):
    """Let the tab take the session without a real thread: Core hands over the acceptor and the
    recording worker stands in."""
    worker, thread = _Worker(), _Thread()

    def ensure():
        if gui._worker is None:
            gui.core.start_ai_assistant()
            if gui.core._assistant_acceptor is None:
                return False
            gui._worker, gui._thread = worker, thread
        return True

    monkeypatch.setattr(gui, "_ensure_worker", ensure)
    return worker, thread


def _started(monkeypatch, tmp_path=None, provider="Gemini", **block):
    """A tab with a chat open."""
    gui = _gui(tmp_path, **block)
    worker, thread = _with_worker(gui, monkeypatch)
    _choose(gui, provider)
    gui.key.setText("my-key")
    gui.on_start_chat()
    assert gui._state == "ready", gui.status_label.text()
    return gui, worker, thread


def _choose(gui, provider):
    gui.provider.setCurrentText(provider)
    gui.provider.currentTextChanged.emit(provider)


def _said(gui, text="move x"):
    """Put one finished turn in the transcript."""
    gui._blocks.append(gui._user_block(text))
    gui._active = {"tools": [], "reply": "Done.", "error": None}
    gui._on_done()


# --- the setup ---

def test_tab_inserts_after_remote_control():
    gui = _gui()
    tabs = gui.main_window.TabWidget
    assert tabs.indexOf(gui) == tabs.indexOf(gui.main_window.remote_control) + 1


def test_the_setup_asks_for_provider_model_and_key_only():
    """Everything that used to be a box here (vision model, bin, focus metric, coordinate system,
    system prompt) is in the config file; Connect and Disconnect are gone."""
    gui = _gui()
    for gone in ("language", "vision", "frame_bin", "focus_metric", "axis_boxes", "connect_button",
                 "disconnect_button"):
        assert not hasattr(gui, gone)
    assert gui.provider.currentText() == "Gemini" and gui.model.text() == "gemini-3.5-flash-lite"
    assert gui.key.echoMode() == QtWidgets.QLineEdit.Password            # dots, never the characters
    assert gui.start_button.text() == "Start Chat Window"
    assert gui.status_label.text() == "no chat is open" and not gui.chat_window.isVisible()
    assert gui.key_notice.text() == gui_module.KEY_NOTICE and "your own API key" in gui_module.KEY_NOTICE
    assert gui.saving_notice.text() == gui_module.NOT_SAVED_NOTICE and not gui.conversations_button.isVisible()
    for name in ("output", "input", "stop_button", "interrupt", "clear_button", "create_skill_button", "show_tool_calls"):
        assert not hasattr(gui, name) and hasattr(gui.chat_window, name)


def test_the_config_file_decides_what_left_the_tab(tmp_path, monkeypatch):
    _files(tmp_path)
    gui, worker, _ = _started(monkeypatch, tmp_path, look_bin=4, focus_metric="dct_shannon", axes={"y": "down"},
                              request_interval_s=20, save_conversations=True)
    assert gui.axes() == {"x": "right", "y": "down", "z": "toward the camera"}
    assert worker.look_image_bin == 4 and worker.focus_metric == "dct_shannon" and worker.axes["y"] == "down"
    assert worker.configured[-1].request_interval_s == 20.0 and "20 s between requests" in gui.status_label.text()
    assert "Conversations are being saved in" in gui.saving_notice.text() and gui.conversations_button.isVisible()
    desk, prompt_file, recorder_factory, ceiling = worker.desks[-1]
    assert desk.saving and recorder_factory is not None and ceiling == 900_000
    assert recorder_factory().folder == tmp_path / "home" / "mesoSPIM" / "conversations"
    assert prompt_file.name == "manual.md"


def test_a_line_of_the_config_file_that_cannot_be_used_is_said(tmp_path):
    gui = _gui(tmp_path, look_bin=7)
    assert "look_bin" in gui.status_label.text()


# --- the key ---

def test_start_checks_the_key_first_and_a_refused_key_opens_nothing(monkeypatch):
    gui = _gui()
    worker, _ = _with_worker(gui, monkeypatch)
    gui.check_key = lambda endpoint: (False, "Gemini did not accept this key. Check that it was copied in full.")
    gui.key.setText("wrong")
    gui.on_start_chat()
    assert gui._state == "idle" and not gui.chat_window.isVisible() and worker.configured == []
    assert gui.light_state is False and "did not accept this key" in gui.status_label.text()
    assert gui.start_button.isEnabled()                       # fix the key and press again
    gui.key.setText("right")
    assert gui.light_state is None                            # a new key has not been checked yet
    gui.check_key = lambda endpoint: (True, "accepted")
    gui.on_start_chat()
    assert gui._state == "ready" and gui.light_state is True and gui.chat_window.isVisible()
    assert worker.configured[-1].api_key == "right"


def test_a_check_that_crashes_is_a_refusal_and_not_a_crash(monkeypatch):
    gui = _gui()
    _with_worker(gui, monkeypatch)
    gui.check_key = lambda endpoint: 1 / 0
    gui.on_start_chat()
    assert gui._state == "idle" and "could not be checked" in gui.status_label.text() and gui.start_button.isEnabled()


def test_a_key_is_remembered_only_when_the_box_is_ticked(monkeypatch, _own_home):
    kept = _own_home
    gui, _, _ = _started(monkeypatch, provider="Anthropic")
    assert kept == {}                                         # the box was not ticked
    gui.on_end_chat()
    gui.remember.setChecked(True)
    gui.on_start_chat()
    assert kept == {"Anthropic": "my-key"}
    gui.on_end_chat()
    _choose(gui, "Anthropic")                                 # the field is empty again; the key is remembered
    assert gui.key.text() == "" and gui.key.placeholderText() == "your remembered key is used"
    assert gui.remember.isChecked() and gui.forget_button.isEnabled()
    assert gui._chosen_endpoint().api_key == "my-key"
    gui.on_forget_key()
    assert kept == {} and not gui.forget_button.isEnabled() and gui.key.placeholderText() == "Anthropic API key"


def test_without_a_password_store_the_box_says_why(monkeypatch):
    monkeypatch.setattr(gui_module.keys, "can_remember", lambda: False)
    gui = _gui()
    assert not gui.remember.isEnabled() and gui.remember.toolTip() == gui_module.CANNOT_REMEMBER_TIP


def test_the_address_and_vision_of_an_own_model_come_from_the_config_file(tmp_path, monkeypatch):
    for block, sees in (({"address": "http://box:8000/v1"}, False),
                        ({"address": "http://box:8000/v1", "vision": True}, True)):
        gui = _gui(tmp_path, **block)
        worker, _ = _with_worker(gui, monkeypatch)
        _choose(gui, "OpenAI-style")
        assert gui.key.placeholderText() == "optional"
        gui.on_start_chat()                                   # no key: an own server may need none
        endpoint = worker.configured[-1]
        assert (endpoint.kind, endpoint.base_url, endpoint.vision) == ("openai-compatible", "http://box:8000/v1", sees)
        assert ("Camera images are not sent" in gui.chat_window.output.toPlainText()) is not sees


def test_a_facility_can_forbid_sending_camera_images(tmp_path, monkeypatch):
    gui, worker, _ = _started(monkeypatch, tmp_path, vision=False)
    assert worker.configured[-1].vision is False
    assert "Camera images are not sent" in gui.chat_window.output.toPlainText()
    seeing, worker, _ = _started(monkeypatch, tmp_path)
    assert worker.configured[-1].vision is True               # left out: as the model allows


# --- the skill lists ---

def test_both_lists_show_their_skills_and_mark_what_is_wrong(tmp_path):
    _files(tmp_path)
    gui = _gui(tmp_path)
    assert sorted(gui.skill_boxes) == ["centring", "old routine", "overview"]
    assert all(box.isChecked() for box in gui.skill_boxes.values())
    assert gui.skill_boxes["centring"].toolTip() == "Centre the sample."
    assert "out of date: no tool retired_call" in gui.skill_boxes["old routine"].text()
    assert list(gui.remove_buttons) == ["overview"]            # only a personal skill can be removed
    kinds = {entry.path.name: entry for entry in gui._entries}
    assert not kinds["broken.md"].usable                       # listed, greyed out, with the reason


def test_ticks_are_remembered_and_decide_what_a_chat_gets(tmp_path, monkeypatch):
    _files(tmp_path)
    gui = _gui(tmp_path)
    gui.skill_boxes["old routine"].setChecked(False)
    again = _gui(tmp_path)                                     # the next time mesoSPIM starts
    assert not again.skill_boxes["old routine"].isChecked() and again.skill_boxes["centring"].isChecked()
    worker, _ = _with_worker(again, monkeypatch)
    again.key.setText("k")
    again.on_start_chat()
    desk = worker.desks[-1][0]
    assert sorted(desk.skills) == ["centring", "overview"] and desk.switched_off == ("old routine",)
    assert desk.skills["overview"].kind == "personal" and desk.personal_skills == tmp_path / "home" / "mesoSPIM" / "skills"
    shown = again.chat_window.output.toPlainText()
    assert "Skills in this chat: centring, overview" in shown
    assert not again.skill_boxes["centring"].isEnabled()       # fixed for this chat: end it to change the ticks


def test_a_ticked_skill_that_is_out_of_date_is_said_when_the_chat_opens(tmp_path, monkeypatch):
    _files(tmp_path)
    gui, _, _ = _started(monkeypatch, tmp_path)
    assert "is out of date: it uses retired_call" in gui.chat_window.output.toPlainText()


def test_removing_a_skill_asks_first_and_moves_the_file(tmp_path):
    _, personal = _files(tmp_path)
    gui = _gui(tmp_path)
    gui.remove_buttons["overview"].clicked.emit()              # the answer is No
    assert (personal / "overview.md").exists() and "overview" in gui.skill_boxes
    QtWidgets.QMessageBox.answers.append(QtWidgets.QMessageBox.Yes)
    gui.remove_buttons["overview"].clicked.emit()
    assert not (personal / "overview.md").exists() and (personal / "deleted" / "overview.md").exists()
    assert "overview" not in gui.skill_boxes and "deleted" in gui.status_label.text()


def test_a_file_added_by_hand_appears_when_the_tab_is_shown(tmp_path):
    system, _ = _files(tmp_path)
    gui = _gui(tmp_path)
    (system / "focus.md").write_text(HEAD.format(name="focusing", description="Find focus.", tools="look"))
    gui.showEvent(None)
    assert "focusing" in gui.skill_boxes


# --- starting and ending a chat ---

def test_start_refused_when_transport_busy(monkeypatch):
    gui = _gui(acceptor=None)                                  # start_ai_assistant leaves _assistant_acceptor None
    _with_worker(gui, monkeypatch)
    gui.key.setText("k")
    gui.on_start_chat()
    assert gui.core.calls == ["start_ai_assistant"]            # Core was asked, on its own thread
    assert gui._state == "idle" and not gui.chat_window.isVisible()
    assert "Stop the Remote Control transport" in gui.status_label.text() and gui.start_button.isEnabled()


def test_a_failed_self_test_is_reported_with_its_reason(monkeypatch):
    gui = _gui(acceptor=None)
    gui.core._assistant_refusal = "AI Assistant self-test failed: no effective motion limit for axis/axes: x"
    _with_worker(gui, monkeypatch)
    gui.key.setText("k")
    gui.on_start_chat()
    assert "no effective motion limit" in gui.status_label.text()
    assert "Stop the Remote Control transport" not in gui.status_label.text()


def test_there_is_one_chat_at_a_time(monkeypatch):
    gui, worker, _ = _started(monkeypatch)
    assert gui.chat_window.windowTitle() == "mesoSPIM AI Assistant — Gemini, gemini-3.5-flash-lite"
    assert gui.status_label.text() == "a chat is open: Gemini, gemini-3.5-flash-lite"
    assert not gui.provider.isEnabled() and not gui.key.isEnabled()      # the setup is locked meanwhile
    _said(gui)
    gui.on_start_chat()                                        # a second press brings the chat to the front
    assert len(worker.configured) == 1 and len(worker.desks) == 1 and gui.has_conversation()


def test_closing_the_window_ends_the_chat_and_releases_the_session(monkeypatch):
    """Without it the assistant holds the session until mesoSPIM exits, and the Remote Control
    tab refuses to start with nothing on screen to release it."""
    gui, worker, thread = _started(monkeypatch)
    assert gui.chat_window.close()                             # nothing was said: no question
    assert QtWidgets.QMessageBox.questions == []
    assert gui.core.calls == ["start_ai_assistant", "stop_ai_assistant"] and gui.core._assistant_acceptor is None
    assert worker.interrupted == 1 and worker.answers == [False] and thread.quit_calls == 1
    assert gui._worker is None and gui._endpoints == {} and gui._state == "idle"
    assert not gui.chat_window.isVisible() and gui.provider.isEnabled() and gui._blocks == []
    gui.on_start_chat()
    assert gui.core.calls[-1] == "start_ai_assistant" and gui._state == "ready"


def test_closing_a_chat_that_is_not_saved_asks_first(monkeypatch):
    gui, _, _ = _started(monkeypatch)
    _said(gui)
    assert not gui.chat_window.close()                         # the answer is No: the chat stays
    assert gui._state == "ready" and "gone for good" in QtWidgets.QMessageBox.questions[-1][1]
    QtWidgets.QMessageBox.answers.append(QtWidgets.QMessageBox.Yes)
    assert gui.chat_window.close() and gui._state == "idle"


def test_a_saved_chat_closes_and_clears_without_a_question(tmp_path, monkeypatch):
    gui, worker, _ = _started(monkeypatch, tmp_path, save_conversations=True)
    _said(gui)
    gui.on_clear_all()
    assert worker.resets == 1 and QtWidgets.QMessageBox.questions == []
    _said(gui)
    assert gui.chat_window.close() and QtWidgets.QMessageBox.questions == []


def test_ending_a_chat_during_a_turn_cancels_it(monkeypatch):
    gui, worker, _ = _started(monkeypatch)
    gui.chat_window.input.setText("move x")
    gui.on_submit()                                            # a turn is running
    QtWidgets.QMessageBox.answers.append(QtWidgets.QMessageBox.Yes)
    gui.chat_window.close()
    assert worker.interrupted == 1 and gui._worker is None and gui._state == "idle"
    assert not gui._running and gui.chat_window.input.isEnabled()
    assert gui._blocks == [] and gui.chat_window.output.toPlainText() == ""


# --- the chat window ---

def test_submit_single_flight_disables_input(monkeypatch):
    gui, _, _ = _started(monkeypatch)
    sent = _collect(gui.sig_run_turn)
    gui.chat_window.input.setText("hello")
    gui.on_submit()
    assert sent == ["hello"]
    assert gui.chat_window.input.isEnabled() is False          # single-flight: locked until the turn ends
    assert not gui.chat_window.create_skill_button.isEnabled() and not gui.chat_window.clear_button.isEnabled()
    assert gui.chat_window.input.text() == ""                  # the submitted text was cleared


def test_a_message_sends_only_while_a_chat_is_open():
    gui = _gui()
    sent = _collect(gui.sig_run_turn)
    gui.chat_window.input.setText("hello")
    gui.on_submit()
    assert sent == [] and gui.chat_window.input.text() == "hello"


def test_clear_context_shows_the_size_and_stops_at_the_ceiling(monkeypatch):
    """The history only grows until Clear context, so the button shows the size of the last
    request out of what the model takes, and no turn starts past the ceiling."""
    from mesoSPIM.src.ai_assistant import config
    gui, worker, _ = _started(monkeypatch)                     # Gemini
    sent = _collect(gui.sig_run_turn)
    button = gui.chat_window.clear_button
    assert not hasattr(gui.chat_window, "meter")               # the per-request line is gone
    assert button.text() == "Clear context (0 of 900,000 tokens)"
    gui._on_usage(12_400)
    assert button.text() == "Clear context (12,400 of 900,000 tokens)"
    gui._on_usage(0)                                           # a provider that counted nothing
    assert button.text() == "Clear context (12,400 of 900,000 tokens)"
    gui._on_usage(config.PROVIDERS["Gemini"]["context_warn_tokens"])
    assert button.toolTip() == config.CONTEXT_LARGE
    gui._on_usage(config.PROVIDERS["Gemini"]["context_max_tokens"])
    assert button.toolTip() == config.CONTEXT_FULL
    gui.chat_window.input.setText("move x by 100")
    gui.on_submit()
    assert sent == [] and config.CONTEXT_FULL in gui.chat_window.output.toPlainText()
    gui.on_create_skill()
    assert sent == []
    gui.on_clear_all()                                         # nothing of the operator's to lose: no question
    assert worker.resets == 1 and button.text() == "Clear context (0 of 900,000 tokens)"
    gui.chat_window.input.setText("move x by 100")
    gui.on_submit()
    assert sent == ["move x by 100"]


def test_a_model_with_no_known_ceiling_shows_the_tokens_alone(tmp_path, monkeypatch):
    gui, _, _ = _started(monkeypatch, tmp_path, provider="OpenAI-style", address="http://box/v1")
    gui._on_usage(5_000)
    assert gui.chat_window.clear_button.text() == "Clear context (5,000 tokens)"


def test_clearing_a_chat_that_is_not_saved_asks_first(monkeypatch):
    gui, worker, _ = _started(monkeypatch)
    _said(gui, "old question")
    gui.on_clear_all()                                         # the answer is No
    assert worker.resets == 0 and "old question" in gui.chat_window.output.toPlainText()
    QtWidgets.QMessageBox.answers.append(QtWidgets.QMessageBox.Yes)
    gui.on_clear_all()
    assert worker.resets == 1 and gui._blocks == [] and "old question" not in gui.chat_window.output.toPlainText()
    gui._set_running(True)
    gui.on_clear_all()                                         # ignored while a turn runs
    assert worker.resets == 1


def test_create_skill_asks_the_assistant_and_needs_a_conversation(monkeypatch):
    from mesoSPIM.src.ai_assistant import config
    gui, _, _ = _started(monkeypatch)
    sent = _collect(gui.sig_run_turn)
    gui.on_create_skill()
    assert sent == [] and "no conversation yet" in gui.chat_window.output.toPlainText()
    _said(gui)
    gui.on_create_skill()
    assert sent == [config.CREATE_SKILL_REQUEST]
    shown = gui.chat_window.output.toPlainText()
    assert gui_module.CREATE_SKILL_SHOWN in shown and "save_skill" not in shown      # the label, not the request


def test_the_assistant_s_question_shows_the_whole_skill_and_passes_the_answer_on(monkeypatch):
    gui, worker, _ = _started(monkeypatch)
    details = {"name": "overview", "description": "A quick overview.", "steps": "Snap once.", "tools": ["snap"]}
    gui._on_confirm("save_skill", json.dumps(details))         # the dialog is declined
    assert worker.answers == [False]
    QtWidgets.QDialog.answers.append(True)
    gui._on_confirm("edit_skill", json.dumps(dict(details, was="Snap twice.")))
    assert worker.answers == [False, True]
    text = gui_module.skill_text("edit_skill", dict(details, was="Snap twice."))
    assert "Name: overview" in text and "Description: A quick overview." in text and "Tools it uses: snap" in text
    assert text.index("Snap once.") < text.index("The steps as they are now:") < text.index("Snap twice.")
    assert QtWidgets.QDialog.shown[-1].text.toPlainText() == text


def test_a_saved_skill_is_said_in_the_chat_and_listed(tmp_path, monkeypatch):
    _, personal = _files(tmp_path)
    gui, _, _ = _started(monkeypatch, tmp_path)
    (personal / "tiling.md").write_text(HEAD.format(name="tiling", description="Tile a brain.", tools="look"))
    gui._on_skill_saved("tiling")
    assert "was saved to your personal skills" in gui.chat_window.output.toPlainText()
    assert gui.skill_boxes["tiling"].isChecked() and not gui.skill_boxes["tiling"].isEnabled()   # for the next chat
    gui.on_end_chat()
    assert gui.skill_boxes["tiling"].isEnabled()


def test_a_note_from_the_worker_is_a_grey_line(monkeypatch):
    gui, _, _ = _started(monkeypatch)
    gui._on_note("This conversation could not be saved in D:/full: no space left")
    assert "could not be saved" in gui.chat_window.output.toPlainText()


def test_the_chat_shows_no_images():
    """The frame a look reads goes to the vision model only, not into the chat."""
    from mesoSPIM.src.ai_assistant import assistant as assistant
    assert not hasattr(assistant.AssistantWorker, "sig_frame")
    gui = _gui()
    gui._active = {"tools": [("look", "{}")], "reply": "Diagonal stripes.", "error": None}
    gui._on_done()
    assert "<img" not in gui.chat_window.output.toPlainText() and "Diagonal stripes." in gui.chat_window.output.toPlainText()


def test_tool_calls_show_as_grey_lines_and_can_be_hidden():
    """On by default, so the operator sees what the assistant does to the microscope; the switch
    shows or hides them for every turn, the earlier ones too."""
    gui = _gui()
    assert gui.chat_window.show_tool_calls.isChecked() is True
    gui._active = {"tools": [("move_absolute", '{"targets": {"x": 5}}')], "reply": "Moved.", "error": None}
    gui._on_done()
    assert "Moved." in gui.chat_window.output.toPlainText() and "move_absolute" in gui.chat_window.output.toPlainText()
    gui.chat_window.show_tool_calls.setChecked(False)
    assert "Moved." in gui.chat_window.output.toPlainText() and "move_absolute" not in gui.chat_window.output.toPlainText()
    gui.chat_window.show_tool_calls.setChecked(True)
    assert "move_absolute" in gui.chat_window.output.toPlainText()


def test_a_reply_with_no_command_behind_it_says_so():
    """A small model writes "I have closed the shutters" having called nothing. The tab cannot
    judge the sentence; it can say what it sent, with the tool calls."""
    gui = _gui()
    gui._active = {"tools": [], "reply": None, "error": None}
    gui._on_reply("I have closed the shutters.")
    assert gui_module.NO_COMMANDS_SENT in gui.chat_window.output.toPlainText()
    gui._on_done()
    assert gui_module.NO_COMMANDS_SENT in gui.chat_window.output.toPlainText()               # kept in the finished block
    gui._active = {"tools": [("close_shutters", "{}")], "reply": None, "error": None}
    gui._on_reply("I have closed the shutters.")
    assert gui.chat_window.output.toPlainText().count(gui_module.NO_COMMANDS_SENT) == 1      # not under a turn that did send one
    gui.chat_window.show_tool_calls.setChecked(False)
    assert gui_module.NO_COMMANDS_SENT not in gui.chat_window.output.toPlainText()


def test_a_stand_in_model_is_shown_in_the_turn():
    gui = _gui()
    gui._active = {"tools": [], "reply": None, "error": None}
    gui._on_served("gemini-3.1-flash-lite answered this turn, standing in for gemini-3.5-flash-lite")
    gui._on_reply("Moved.")
    assert "standing in for gemini-3.5-flash-lite" in gui.chat_window.output.toPlainText()
    gui._on_done()
    assert "standing in for" in gui.chat_window.output.toPlainText() and "Moved." in gui.chat_window.output.toPlainText()


def test_stop_microscope_goes_the_main_windows_way_and_cancels_the_assistant():
    gui = _gui()
    window = gui.main_window
    halted = _collect(window.sig_stop_movement)
    assert gui.chat_window.stop_button.isEnabled()
    gui.on_stop_microscope()                                        # before any assistant: still stops
    assert window.stops == 1 and len(halted) == 1
    cancelled = []
    gui._worker = type("_W", (), {"interrupt": lambda self: cancelled.append(True)})()
    gui._set_running(True)
    assert gui.chat_window.stop_button.isEnabled()
    gui.on_stop_microscope()
    assert window.stops == 2 and len(halted) == 2 and cancelled == [True]
    assert "[stop microscope]" in gui.chat_window.output.toPlainText()


def test_cancel_is_always_clickable_and_idle_between_turns():
    gui = _gui()
    assert gui.chat_window.interrupt.isEnabled()
    gui.on_interrupt()                                              # idle: nothing happens
    assert "[cancelled]" not in gui.chat_window.output.toPlainText()
    interrupted = []
    gui._worker = type("_W", (), {"interrupt": lambda self: interrupted.append(True)})()
    gui._set_running(True)
    assert gui.chat_window.interrupt.isEnabled()
    gui.on_interrupt()
    assert interrupted == [True] and "[cancelled]" in gui.chat_window.output.toPlainText()


def test_a_path_in_a_reply_is_as_large_as_the_text_around_it():
    """Qt's Markdown gives an inline code span, which is how a model writes a file path, a fixed
    8 pt: smaller than the chat. The size goes; the fixed-width face stays."""
    qt = ('<p>I have saved a snap to <span style=" font-family:\'Courier New\'; font-size:8pt;">'
          'D:/tmp/remote_20260924-175526.tif</span> while the microscope remained idle.</p>')
    html = gui_module._chat_sized(qt)
    assert "font-size" not in html
    assert "font-family:'Courier New';" in html and "D:/tmp/remote_20260924-175526.tif" in html


def test_the_done_notice_is_one_grey_line_and_no_turn(monkeypatch):
    gui, _, _ = _started(monkeypatch)
    sent = _collect(gui.sig_run_turn)
    gui._active = {"tools": [], "reply": None, "error": None}
    gui._on_tool("run_acquisition_list", "{}")
    assert gui._done_check.active                                       # the worker is asked every 2 s
    gui._on_run_ended("Acquisition list finished 14:02:00")
    assert not gui._done_check.active and sent == []
    assert "Acquisition list finished 14:02:00" in gui.chat_window.output.toPlainText()


# --- saved conversations ---

def test_saved_conversations_are_listed_and_one_can_be_removed(tmp_path):
    from mesoSPIM.src.ai_assistant import conversations
    folder = tmp_path / "chats"
    conversations.Recorder(folder).save([{"time": "10:00:00", "prompt": "tile the brain", "tools": [], "reply": "Tiled."}])
    dialog = gui_module.ConversationsDialog(None, folder, None)
    (path,) = dialog.remove_buttons
    dialog.remove_buttons[path].clicked.emit()                 # the answer is No
    assert path.exists()
    QtWidgets.QMessageBox.answers.append(QtWidgets.QMessageBox.Yes)
    dialog.remove_buttons[path].clicked.emit()
    assert not path.exists() and dialog.remove_buttons == {}


# --- a second model that looks at the camera images ---

def test_without_a_vision_model_in_the_config_the_answering_model_looks(monkeypatch):
    gui, worker, _ = _started(monkeypatch)
    assert "vision" not in gui._endpoints and "second model" not in gui.chat_window.output.toPlainText()


def test_a_vision_model_at_the_same_provider_uses_the_key_in_use(tmp_path, monkeypatch):
    gui = _gui(tmp_path, vision_model={"provider": "Anthropic", "model": "claude-sonnet-5-5"})
    seen = []
    gui.check_key = lambda endpoint: (seen.append(endpoint.model), (True, "accepted"))[1]
    configured = []
    worker, _ = _with_worker(gui, monkeypatch)
    worker.configure = lambda endpoint, vision=None: configured.append((endpoint, vision))
    _choose(gui, "Anthropic")
    gui.key.setText("my-key")
    gui.on_start_chat()
    (answering, vision), = configured
    assert (vision.provider, vision.model, vision.api_key, vision.vision) == ("Anthropic", "claude-sonnet-5-5", "my-key", True)
    assert seen == ["claude-haiku-5-5", "claude-sonnet-5-5"]              # both were checked with the provider
    shown = gui.chat_window.output.toPlainText()
    assert "looked at by a second model (Anthropic, claude-sonnet-5-5)" in shown
    assert "vision: Anthropic, claude-sonnet-5-5" in gui.status_label.text()


def test_a_vision_model_at_another_provider_uses_that_person_s_remembered_key(tmp_path, monkeypatch, _own_home):
    _own_home["Gemini"] = "remembered-gemini-key"
    gui = _gui(tmp_path, vision_model={"provider": "Gemini"})
    configured = []
    worker, _ = _with_worker(gui, monkeypatch)
    worker.configure = lambda endpoint, vision=None: configured.append((endpoint, vision))
    _choose(gui, "Anthropic")
    gui.key.setText("my-key")
    gui.on_start_chat()
    answering, vision = configured[0]
    assert answering.api_key == "my-key"
    assert (vision.provider, vision.model, vision.api_key) == ("Gemini", "gemini-3.5-flash-lite", "remembered-gemini-key")


def test_a_vision_model_without_a_key_is_said_and_the_answering_model_looks(tmp_path, monkeypatch):
    gui = _gui(tmp_path, vision_model={"provider": "Gemini"})
    configured = []
    worker, _ = _with_worker(gui, monkeypatch)
    worker.configure = lambda endpoint, vision=None: configured.append(vision)
    _choose(gui, "Anthropic")
    gui.key.setText("my-key")
    gui.on_start_chat()
    assert configured == [None] and gui._state == "ready"                 # the chat still opens
    shown = gui.chat_window.output.toPlainText()
    assert "The vision model (Gemini) has no key" in shown and "GEMINI_API_KEY" in shown


def test_a_vision_model_the_provider_refuses_does_not_stop_the_chat(tmp_path, monkeypatch):
    gui = _gui(tmp_path, vision_model={"provider": "Anthropic", "model": "claude-sonnet-5-5"})
    gui.check_key = lambda endpoint: (endpoint.model != "claude-sonnet-5-5", "Anthropic did not accept this key.")
    configured = []
    worker, _ = _with_worker(gui, monkeypatch)
    worker.configure = lambda endpoint, vision=None: configured.append(vision)
    _choose(gui, "Anthropic")
    gui.key.setText("my-key")
    gui.on_start_chat()
    assert configured == [None] and gui._state == "ready" and gui.light_state is True
    assert "cannot be used" in gui.chat_window.output.toPlainText()


def test_forbidding_images_also_switches_the_vision_model_off(tmp_path, monkeypatch):
    gui = _gui(tmp_path, vision=False, vision_model={"provider": "Anthropic"})
    configured = []
    worker, _ = _with_worker(gui, monkeypatch)
    worker.configure = lambda endpoint, vision=None: configured.append((endpoint.vision, vision))
    _choose(gui, "Anthropic")
    gui.key.setText("my-key")
    gui.on_start_chat()
    assert configured == [(False, None)]
    assert "Camera images are not sent" in gui.chat_window.output.toPlainText()


def test_a_vision_model_with_an_unknown_provider_is_named_as_a_problem(tmp_path):
    gui = _gui(tmp_path, vision_model={"provider": "Nobody"})
    assert gui.settings.vision_provider == "" and "vision_model" in gui.status_label.text()
