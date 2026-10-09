"""AiAssistantGUI logic, from source, using the QtWidgets stub in conftest.

The real QThread / worker hand-off is a real-PyQt concern (the smoke layer); here we test the
tab wiring, the transport-busy refusal, and the single-flight input lock in isolation.
"""
import os
import types

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


def test_tab_inserts_after_remote_control():
    gui = AiAssistantGUI(_FakeParent(_FakeCore(acceptor=object())))
    tabs = gui.main_window.TabWidget
    assert tabs.indexOf(gui) == tabs.indexOf(gui.main_window.remote_control) + 1


def test_connect_refused_when_transport_busy():
    core = _FakeCore(acceptor=None)                     # start_ai_assistant leaves _assistant_acceptor None
    gui = AiAssistantGUI(_FakeParent(core))
    gui.on_connect()
    assert core.calls == ["start_ai_assistant"]         # Core was asked, on its own thread
    assert gui._state == "idle" and not gui.chat_window.isVisible()
    assert "Stop the Remote Control transport" in gui.status_label.text()
    assert gui.connect_button.isEnabled()               # the operator stops the transport and tries again


def test_a_failed_self_test_is_reported_with_its_reason():
    core = _FakeCore(acceptor=None)
    core._assistant_refusal = "AI Assistant self-test failed: no effective motion limit for axis/axes: x"
    gui = AiAssistantGUI(_FakeParent(core))
    gui.on_connect()
    assert "no effective motion limit" in gui.status_label.text()
    assert "Stop the Remote Control transport" not in gui.status_label.text()


def test_submit_single_flight_disables_input(monkeypatch):
    gui = AiAssistantGUI(_FakeParent(_FakeCore(acceptor=object())))
    monkeypatch.setattr(gui, "_ensure_worker", lambda: True)   # pretend ready; no real thread
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    gui._worker = type("_Worker", (), {"configure": lambda self, endpoint, vision=None: None})()
    gui.on_connect()
    sent = _collect(gui.sig_run_turn)
    gui.chat_window.input.setText("hello")
    gui.on_submit()
    assert sent == ["hello"]
    assert gui.chat_window.input.isEnabled() is False   # single-flight: locked until the turn ends
    assert not hasattr(gui.chat_window, "send_button")  # Enter sends; the button there is Stop microscope
    assert gui.chat_window.input.text() == ""           # the submitted text was cleared


def test_the_tab_is_setup_only_and_the_window_opens_with_connect(monkeypatch):
    """The tab has the setup, a status line and Connect / Disconnect, like the Remote Control
    tab; the chat is a window of its own, there while connected."""
    gui = _gui()
    for name in ("output", "input", "stop_button", "interrupt", "clear_button", "show_tool_calls"):
        assert not hasattr(gui, name) and hasattr(gui.chat_window, name)
    assert gui.status_label.text() == "disconnected"
    assert gui.connect_button.isEnabled() and not gui.disconnect_button.isEnabled()
    assert not gui.chat_window.isVisible()
    gui._worker = type("_Worker", (), {"configure": lambda self, endpoint, vision=None: None})()
    monkeypatch.setattr(gui, "_ensure_worker", lambda: True)
    gui.language.key.setText("g-key")
    gui.on_connect()
    assert gui.chat_window.isVisible() and gui.chat_window.windowTitle() == "mesoSPIM AI Assistant — Gemini, gemini-3.5-flash-lite"
    assert gui.status_label.text() == "connected: Gemini, gemini-3.5-flash-lite"
    assert not gui.connect_button.isEnabled() and gui.disconnect_button.isEnabled()
    assert not gui.language.isEnabled() and not gui.frame_bin.isEnabled()   # applied by Connect: Disconnect to change
    gui.on_connect()                                                             # a second press does nothing
    assert gui._state == "ready"


# --- the setup row ---

def _gui():
    return AiAssistantGUI(_FakeParent(_FakeCore(acceptor=object())))


def test_setup_row_prefills_the_default_provider():
    gui = _gui()
    assert gui.language.provider.currentText() == "Gemini"
    assert gui.language.model.text() == "gemini-3.5-flash-lite"
    assert gui.language.key.isVisible() and not gui.language.base_url.isVisible()
    assert gui.connect_button.text() == "Connect" and gui.disconnect_button.text() == "Disconnect"


def test_an_openai_style_server_adds_a_base_url_and_keeps_an_optional_key():
    gui = _gui()
    gui.language.provider.setCurrentText("OpenAI-style")
    gui.language.provider.currentTextChanged.emit("OpenAI-style")
    assert gui.language.model.text() == "gemma4:31b"
    assert gui.language.base_url.text() == "http://localhost:11434/v1"
    assert gui.language.base_url.isVisible() and gui.language.key.isVisible()
    assert gui.language.key.placeholderText() == "optional"


def test_connect_without_a_key_explains_and_does_not_start(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    gui = _gui()
    configured = []
    gui._worker = type("_Worker", (), {"configure": lambda self, endpoint, vision=None: configured.append(endpoint)})()
    monkeypatch.setattr(gui, "_ensure_worker", lambda: True)
    gui.on_connect()
    assert configured == [] and gui._state == "idle"          # nothing configured
    assert "Enter an API key for Gemini" in gui.status_label.text()
    assert "GEMINI_API_KEY" in gui.status_label.text() and not gui.chat_window.isVisible()


def test_connect_with_a_key_configures_the_worker(monkeypatch):
    gui = _gui()
    configured = []

    class _Worker:
        def configure(self, endpoint, vision=None):
            configured.append(endpoint)

    gui._worker = _Worker()
    monkeypatch.setattr(gui, "_ensure_worker", lambda: True)
    gui.language.provider.setCurrentText("Anthropic")
    gui.language.provider.currentTextChanged.emit("Anthropic")
    gui.language.key.setText("sk-test")
    gui.on_connect()
    (endpoint,) = configured
    assert (endpoint.provider, endpoint.kind, endpoint.api_key) == ("Anthropic", "anthropic", "sk-test")
    assert gui._state == "ready" and gui.chat_window.isVisible()


def test_openai_style_connects_without_a_key_and_passes_one_through(monkeypatch):
    for key in ("", "gw-token"):                            # an Ollama-like server; a gateway
        gui = _gui()
        configured = []

        class _Worker:
            def configure(self, endpoint, vision=None):
                configured.append(endpoint)

        gui._worker = _Worker()
        monkeypatch.setattr(gui, "_ensure_worker", lambda: True)
        gui.language.provider.setCurrentText("OpenAI-style")
        gui.language.provider.currentTextChanged.emit("OpenAI-style")
        gui.language.base_url.setText("http://box:8000/v1")
        gui.language.key.setText(key)
        gui.on_connect()
        (endpoint,) = configured
        assert (endpoint.kind, endpoint.base_url, endpoint.api_key) == ("openai-compatible", "http://box:8000/v1", key)
        assert gui._state == "ready" and gui.chat_window.isVisible()


def test_a_message_sends_only_while_connected():
    """The window is only open while connected, but its input could still be reached: nothing
    is sent from any other state."""
    gui = _gui()
    sent = _collect(gui.sig_run_turn)
    gui.chat_window.input.setText("hello")
    gui.on_submit()
    assert sent == [] and gui.chat_window.input.text() == "hello"


def _choose_provider(picker, name):
    picker.provider.setCurrentText(name)
    picker.provider.currentTextChanged.emit(name)


# --- the window ---

def test_closing_the_window_disconnects(monkeypatch):
    """The window's × is Disconnect: the session goes back, the setup unlocks, and the next
    Connect starts with an empty transcript, as the new worker starts with an empty memory."""
    gui, core, worker, thread = _connected_gui(monkeypatch)
    gui._blocks.append("<p>earlier turn</p>")
    assert gui.chat_window.close()
    assert not gui.chat_window.isVisible() and gui._state == "idle"
    assert core.calls == ["start_ai_assistant", "stop_ai_assistant"] and gui._worker is None
    assert gui.connect_button.isEnabled() and gui.language.isEnabled()
    assert gui._blocks == []
    gui.on_connect()
    assert gui.chat_window.isVisible() and gui._blocks == [] and "earlier turn" not in gui.chat_window.output.toPlainText()


def test_the_chat_shows_no_images():
    """The frame a look reads goes to the vision model only, not into the chat."""
    from mesoSPIM.src.ai_assistant import assistant as assistant
    assert not hasattr(assistant.AssistantWorker, "sig_frame")
    gui = _gui()
    gui._active = {"tools": [("look", "{}")], "reply": "Diagonal stripes.", "error": None}
    gui._on_done()
    assert "<img" not in gui.chat_window.output.toPlainText() and "Diagonal stripes." in gui.chat_window.output.toPlainText()


def test_tool_calls_show_only_when_switched_on_in_configure():
    """Off by default; the switch shows or hides them for every turn, the earlier ones too."""
    gui = _gui()
    assert gui.chat_window.show_tool_calls.isChecked() is False
    gui._active = {"tools": [("move_absolute", '{"targets": {"x": 5}}')], "reply": "Moved.", "error": None}
    gui._on_done()
    assert "Moved." in gui.chat_window.output.toPlainText() and "move_absolute" not in gui.chat_window.output.toPlainText()

    gui.chat_window.show_tool_calls.setChecked(True)
    assert "move_absolute" in gui.chat_window.output.toPlainText()

    gui.chat_window.show_tool_calls.setChecked(False)
    assert "move_absolute" not in gui.chat_window.output.toPlainText()


def test_no_command_was_sent_shows_only_with_the_tool_calls():
    """It says what the turn sent, so it goes with the tool calls: hidden by default (the operator
    did not want it in the chat), there when Show tool calls is on."""
    gui = _gui()
    gui._active = {"tools": [], "reply": "I have closed the shutters.", "error": None}
    gui._on_done()
    assert gui_module.NO_COMMANDS_SENT not in gui.chat_window.output.toPlainText()
    gui.chat_window.show_tool_calls.setChecked(True)
    assert gui.chat_window.output.toPlainText().count(gui_module.NO_COMMANDS_SENT) == 1


def test_a_stand_in_model_is_shown_in_the_turn():
    gui = _gui()
    gui._active = {"tools": [], "reply": None, "error": None}
    gui._on_served("gemini-3.1-flash-lite answered this turn, standing in for gemini-3.5-flash-lite")
    gui._on_reply("Moved.")
    assert "standing in for gemini-3.5-flash-lite" in gui.chat_window.output.toPlainText()
    gui._on_done()
    assert "standing in for" in gui.chat_window.output.toPlainText() and "Moved." in gui.chat_window.output.toPlainText()


def test_a_reply_with_no_command_behind_it_says_so():
    """A small model writes "I have closed the shutters" having called nothing. The tab cannot
    judge the sentence; it can say what it sent."""
    gui = _gui()
    gui.chat_window.show_tool_calls.setChecked(True)
    gui._active = {"tools": [], "reply": None, "error": None}
    gui._on_reply("I have closed the shutters.")
    assert gui_module.NO_COMMANDS_SENT in gui.chat_window.output.toPlainText()
    gui._on_done()
    assert gui_module.NO_COMMANDS_SENT in gui.chat_window.output.toPlainText()               # kept in the finished block
    gui._active = {"tools": [("close_shutters", "{}")], "reply": None, "error": None}
    gui._on_reply("I have closed the shutters.")
    assert gui.chat_window.output.toPlainText().count(gui_module.NO_COMMANDS_SENT) == 1      # not under a turn that did send one


# --- the Run / Cancel bar ---

def test_clear_all_clears_the_transcript_and_the_worker_between_turns():
    gui = _gui()
    resets = []
    gui._worker = type("_W", (), {"reset": lambda self: resets.append(True)})()
    gui._blocks.append(gui._user_block("old question"))
    gui._render()
    assert "old question" in gui.chat_window.output.toPlainText()
    gui.on_clear_all()
    assert resets == [True] and gui._blocks == [] and "old question" not in gui.chat_window.output.toPlainText()
    gui._set_running(True)
    gui.on_clear_all()                                       # ignored while a turn runs
    assert resets == [True] and not gui.chat_window.clear_button.isEnabled()


def test_vision_box_defers_to_the_language_model_by_default(monkeypatch):
    gui = _gui()
    configured = []
    gui._worker = type("_W", (), {"configure": lambda self, endpoint, vision=None: configured.append(vision)})()
    monkeypatch.setattr(gui, "_ensure_worker", lambda: True)
    assert gui.vision.mode.currentText() == "Same as language model" and gui.vision.same
    assert not gui.vision.provider.isVisible()
    gui.language.key.setText("k")
    gui.on_connect()
    assert configured == [None]


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


# --- ending the session ---

class _StoppableWorker:
    """What Disconnect touches on the worker and its thread, recorded."""

    def __init__(self):
        self.interrupted = 0
        self.configured = []

    def configure(self, endpoint, vision=None):
        self.configured.append(endpoint)

    def interrupt(self):
        self.interrupted += 1

    def run_turn(self, _text):
        pass


class _StoppableThread:
    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1

    def wait(self, _msec):
        return True


def _connected_gui(monkeypatch):
    """A tab that holds the session: Core handed it the acceptor and a model is configured."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    core = _FakeCore(acceptor=object())
    gui = AiAssistantGUI(_FakeParent(core))
    worker, thread = _StoppableWorker(), _StoppableThread()

    def ensure():
        if gui._worker is None:
            core.start_ai_assistant()
            gui._worker, gui._thread = worker, thread
        return True

    monkeypatch.setattr(gui, "_ensure_worker", ensure)
    gui.on_connect()
    return gui, core, worker, thread


def test_the_coordinate_system_box_reaches_the_worker_and_takes_the_config_start(monkeypatch):
    from mesoSPIM.src.ai_assistant import config as config
    gui = _gui()
    assert gui.axes() == config.DEFAULT_AXES
    assert gui.axis_boxes["z"].items() == ["toward the camera", "away from the camera"]
    gui._worker = type("_W", (), {"look_image_bin": 1})()
    gui.axis_boxes["y"].setCurrentText("down")
    gui.axis_boxes["y"].currentTextChanged.emit("down")
    assert gui._worker.axes == {"x": "right", "y": "down", "z": "toward the camera"}
    core = _FakeCore(acceptor=object())
    core.cfg = types.SimpleNamespace(ai_assistant_axes={"x": "left", "z": "nonsense"})
    started = AiAssistantGUI(_FakeParent(core))
    assert started.axes() == {"x": "left", "y": "up", "z": "toward the camera"}   # a bad value takes the default
    gui2, core2, worker, thread = _connected_gui(monkeypatch)
    assert not gui2.axis_boxes["x"].isEnabled()                                  # locked while connected, like the rest


def test_an_openai_style_model_sees_only_when_the_box_says_so(monkeypatch):
    """An OpenAI-style server says nothing about its model: unticked, the endpoint cannot see and
    the status line says so; ticked, it can."""
    for ticked in (False, True):
        gui = _gui()
        configured = []

        class _Worker:
            def configure(self, endpoint, vision=None):
                configured.append(endpoint)

        gui._worker = _Worker()
        monkeypatch.setattr(gui, "_ensure_worker", lambda: True)
        gui.language.provider.setCurrentText("OpenAI-style")
        gui.language.provider.currentTextChanged.emit("OpenAI-style")
        assert gui.language.sees.isVisible() or not gui.language.isVisible()
        gui.language.sees.setChecked(ticked)
        gui.on_connect()
        (endpoint,) = configured
        assert endpoint.vision is ticked
        assert ("no vision" in gui.status_label.text()) is not ticked
    gui.language.provider.setCurrentText("Gemini")
    gui.language.provider.currentTextChanged.emit("Gemini")
    assert not gui.language.sees.isVisible() and gui.language.cloud_endpoint().vision   # the preset decides


def test_a_request_interval_from_the_config_reaches_the_endpoints(monkeypatch):
    gui, core, worker, thread = _connected_gui(monkeypatch)
    assert worker.configured[-1].request_interval_s == 0.0
    gui.on_disconnect()
    core.cfg = types.SimpleNamespace(ai_assistant_request_interval_s=20)
    gui.on_connect()
    assert worker.configured[-1].request_interval_s == 20.0 and "20 s between requests" in gui.status_label.text()


def test_disconnect_releases_the_session_so_a_transport_can_start(monkeypatch):
    """Without it the assistant holds the session until mesoSPIM exits, and the Remote Control
    tab refuses to start with nothing on screen to release it."""
    gui, core, worker, thread = _connected_gui(monkeypatch)
    gui._blocks.append("<p>earlier turn</p>")

    gui.on_disconnect()

    assert core.calls == ["start_ai_assistant", "stop_ai_assistant"]
    assert core._assistant_acceptor is None                  # what start_for_core checks
    assert worker.interrupted == 1 and thread.quit_calls == 1
    assert gui._worker is None and gui._endpoints == {}
    assert gui._state == "idle" and not gui.chat_window.isVisible()
    assert gui.connect_button.isEnabled() and not gui.disconnect_button.isEnabled()
    assert gui._blocks == []                                  # a fresh transcript for a fresh worker


def test_connect_after_disconnect_takes_the_session_again(monkeypatch):
    gui, core, worker, thread = _connected_gui(monkeypatch)
    gui.on_disconnect()
    gui.on_connect()
    assert core.calls == ["start_ai_assistant", "stop_ai_assistant", "start_ai_assistant"]
    assert gui._state == "ready" and gui.chat_window.isVisible()


def test_disconnect_during_a_turn_cancels_it(monkeypatch):
    """Disconnect and the window's × work while the assistant is busy: the turn is cancelled,
    the window closes with an empty transcript, and what the assistant already started at the
    microscope carries on."""
    gui, core, worker, thread = _connected_gui(monkeypatch)
    gui.chat_window.input.setText("move x")
    gui.on_submit()                                           # a turn is running
    assert gui.disconnect_button.isEnabled()

    gui.chat_window.close()

    assert worker.interrupted == 1 and gui._worker is None and gui._state == "idle"
    assert not gui._running and gui.chat_window.input.isEnabled()
    assert gui._blocks == [] and gui.chat_window.output.toPlainText() == ""


def test_a_path_in_a_reply_is_as_large_as_the_text_around_it():
    """Qt's Markdown gives an inline code span, which is how a model writes a file path, a fixed
    8 pt: smaller than the chat. The size goes; the fixed-width face stays."""
    qt = ('<p>I have saved a snap to <span style=" font-family:\'Courier New\'; font-size:8pt;">'
          'D:/tmp/remote_20260924-175526.tif</span> while the microscope remained idle.</p>')
    html = gui_module._chat_sized(qt)
    assert "font-size" not in html
    assert "font-family:'Courier New';" in html and "D:/tmp/remote_20260924-175526.tif" in html


def test_the_meter_shows_the_session_size_warns_and_stops_at_the_model_s_ceiling(monkeypatch):
    """The history is append-only until Clear context, so the tab shows what the last request cost
    and refuses a new turn past the model's ceiling rather than sending one it would refuse."""
    from mesoSPIM.src.ai_assistant import config as config
    gui, core, worker, thread = _connected_gui(monkeypatch)            # Gemini
    sent = []
    gui.sig_run_turn.connect(sent.append)
    warn, ceiling = config.PROVIDERS["Gemini"]["context_warn_tokens"], config.PROVIDERS["Gemini"]["context_max_tokens"]
    gui._on_usage(12_000)
    assert gui.chat_window.meter.text() == "12,000 tokens per request"
    gui._on_usage(0)                                                    # a provider that counted nothing
    assert gui.chat_window.meter.text() == "12,000 tokens per request"
    gui._on_usage(warn)
    assert config.CONTEXT_LARGE in gui.chat_window.meter.text()
    gui._on_usage(ceiling)
    gui.chat_window.input.setText("move x by 100")
    gui.on_submit()
    assert sent == [] and config.CONTEXT_FULL in gui.chat_window.output.toPlainText()
    gui._worker = type("_W", (), {"reset": lambda self: None})()
    gui.on_clear_all()
    gui._worker = worker
    gui.chat_window.input.setText("move x by 100")
    gui.on_submit()
    assert sent == ["move x by 100"] and gui.chat_window.meter.text() == ""


def test_the_done_notice_is_one_grey_line_and_no_turn(monkeypatch):
    gui, core, worker, thread = _connected_gui(monkeypatch)
    sent = []
    gui.sig_run_turn.connect(sent.append)
    gui._active = {"tools": [], "reply": None, "error": None}
    gui._on_tool("run_acquisition_list", "{}")
    assert gui._done_check.active                                       # the worker is asked every 2 s
    gui._on_run_ended("Acquisition list finished 14:02:00")
    assert not gui._done_check.active and sent == []
    assert "Acquisition list finished 14:02:00" in gui.chat_window.output.toPlainText()
