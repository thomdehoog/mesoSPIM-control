"""Real-PyQt smoke test for the AI Assistant tab and window: builds them offscreen, never a
worker, a model or a server. Checks that the setup shows provider, model and key with the light
beside the key, that the two skill lists keep their height and scroll when they hold many skills,
that the tab stays within the main window's width; that the chat window lays its controls out as
designed and appears only while a chat is open; and that Enter sends while Shift+Enter starts a
new line."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
# A home and a config folder of this run's own, so the skill lists show what this script wrote
# and nothing of the machine it runs on.
import tempfile                                             # noqa: E402
_ROOT = Path(tempfile.mkdtemp(prefix="mesospim-assistant-smoke-"))
os.environ["HOME"] = os.environ["USERPROFILE"] = str(_ROOT / "home")

try:
    from PyQt5 import QtCore, QtGui, QtTest, QtWidgets
except ModuleNotFoundError as error:
    raise SystemExit("test_real_pyqt_assistant_smoke.py requires PyQt5") from error

from mesoSPIM.src.ai_assistant import gui as assistant_gui
from mesoSPIM.src.ai_assistant.gui import AiAssistantGUI
from mesoSPIM.src.remote_control.gui import RemoteControlGUI

MAIN_WINDOW_WIDTH = 1000  # the designed width of mesoSPIM_MainWindow.ui
FRAME = 56                # the window's border, scroll bar and tab margins around the tab


class Core(QtCore.QObject):
    sig_remote_control_started = QtCore.pyqtSignal(bool, str)
    sig_warning = QtCore.pyqtSignal(str)
    cfg = types.SimpleNamespace(__file__=str(_ROOT / "config" / "demo_config.py"))
    _remote_control = None                                # Core's controller handles
    _assistant_acceptor = None

    @QtCore.pyqtSlot(str, str, int, str)
    def start_remote_control(self, *_args):
        pass

    @QtCore.pyqtSlot()
    def stop_remote_control(self):
        pass


class Window(QtWidgets.QMainWindow):
    # MainWindow's two stop signals, which the Remote Control tab listens to.
    sig_state_request = QtCore.pyqtSignal(dict)
    sig_stop_time_lapse = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.core = Core()
        self.TabWidget = QtWidgets.QTabWidget(self)
        self.TimelapseTabWidget = QtWidgets.QWidget()
        self.TabWidget.addTab(self.TimelapseTabWidget, "Timelapse")
        self.setCentralWidget(self.TabWidget)
        self.remote_control = RemoteControlGUI(self)
        self.core.sig_warning.connect(self.display_warning)   # MainWindow.py:184 runs after the tab

    def display_warning(self, text):
        pass


def write_skills(count=8):
    """Eight system skills, more than a list shows at once, and two personal ones."""
    head = "---\nname: {name}\ndescription: {name}, for the smoke test.\ntools: look\n---\nStep one.\n"
    for folder, names in ((_ROOT / "config" / "skills", [f"system skill {n}" for n in range(1, count + 1)]),
                          (_ROOT / "home" / "mesoSPIM" / "skills", ["my routine", "my overview"])):
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            (folder / (name.replace(" ", "-") + ".md")).write_text(head.format(name=name), encoding="utf-8")


def top(widget, within):
    return widget.mapTo(within, widget.rect().topLeft()).y()


class _Acceptor:
    """What the worker needs of the Acceptor here: a place for its name, and reads that answer."""

    client = None

    def dispatch(self, _name, _args):
        return {}


def question_crosses_the_threads(app, window):
    """The assistant asks "save this skill?" from a tool, which runs on a thread of its own while
    the turn runs on the worker's thread. The question must reach the window's thread, where a
    dialog may be shown, and the answer must get back to the tool that waits for it. A real
    worker in a real thread runs one turn whose only step is that question; no model is called."""
    import asyncio
    import threading

    from mesoSPIM.src.ai_assistant import assistant as ai

    core = window.core
    core.start_ai_assistant = lambda: setattr(core, "_assistant_acceptor", _Acceptor())
    core.stop_ai_assistant = lambda: setattr(core, "_assistant_acceptor", None)
    tab = AiAssistantGUI(window)
    tab._call_on_core = lambda method: getattr(core, method)()        # this Core has no slots to invoke
    tab.checks_in_background = False
    tab.check_key = lambda endpoint: (True, "accepted")
    asked_on, got = [], []

    def confirm(kind, details):
        asked_on.append((threading.current_thread() is threading.main_thread(), kind, details["name"]))
        return True                                                   # the operator presses Save skill
    tab._confirm_skill = confirm

    class _Result:
        output = "Saved."

        def new_messages(self):
            return []

        def all_messages(self):
            return []

    class _Agent:
        def __init__(self, worker):
            self.worker = worker

        async def run(self, _prompt, message_history=None):
            got.append(await asyncio.to_thread(self.worker.ask_operator, "save_skill", {"name": "overview"}))
            return _Result()

    built = ai.build_agent
    ai.build_agent = lambda *_a, **_k: _Agent(tab._worker)
    try:
        tab.provider.setCurrentText("Anthropic")
        tab.key.setText("not-a-real-key")
        tab.on_start_chat()
        assert tab._state == "ready", tab.status_label.text()
        tab.chat_window.input.setText("make a skill of that")
        tab.on_submit()
        deadline = QtCore.QDeadlineTimer(10_000)
        while tab._running and not deadline.hasExpired():
            app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        assert not tab._running, "the turn did not end: the question or its answer was lost between threads"
        assert asked_on == [(True, "save_skill", "overview")], asked_on      # asked on the window's thread
        assert got == [True]                                                 # and the tool got the yes
        assert "Saved." in tab.chat_window.output.toPlainText()
    finally:
        ai.build_agent = built
        tab.on_end_chat()
    assert tab._state == "idle" and tab._worker is None


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    write_skills()
    window = Window()
    tab = AiAssistantGUI(window)
    window.TabWidget.setCurrentWidget(tab)
    window.show()
    app.processEvents()

    # The setup: provider and model on one line, the key under them with the light to its right,
    # then the box to remember the key. The key shows dots.
    tab.provider.setCurrentText("Anthropic")
    app.processEvents()
    assert tab.model.text() == "claude-haiku-5-5" and tab.key.echoMode() == QtWidgets.QLineEdit.Password
    assert top(tab.provider, tab) == top(tab.model, tab) < top(tab.key, tab) < top(tab.remember, tab)
    assert tab.light.mapTo(tab, tab.light.rect().topLeft()).x() > tab.key.mapTo(tab, tab.key.rect().topRight()).x()
    width_cloud = tab.minimumSizeHint().width()
    tab.provider.setCurrentText("OpenAI-style")
    app.processEvents()
    assert tab.key.placeholderText() == "optional"
    width_server = tab.minimumSizeHint().width()
    tab.provider.setCurrentText("Anthropic")
    app.processEvents()

    # The skill lists: eight system skills in a list that shows about four. The list keeps its
    # height, every row keeps its own, and the rest is reached by scrolling.
    assert len(tab.skill_boxes) == 10 and len(tab.remove_buttons) == 2
    inner, _rows = tab._skill_layouts["system"]
    area = inner.parent().parent()                           # the widget sits in the area's viewport
    assert isinstance(area, QtWidgets.QScrollArea) and area.height() == assistant_gui.SKILL_LIST_HEIGHT
    rows = tab._skill_rows["system"]
    assert len(rows) == 8 and all(row.height() >= row.minimumHeight() >= 22 for row in rows)
    assert inner.height() > area.viewport().height() and area.verticalScrollBar().maximum() > 0
    assert top(rows[1], inner) >= top(rows[0], inner) + rows[0].height()         # rows do not overlap
    height_with_eight = tab.minimumSizeHint().height()
    write_skills(count=20)
    tab.refresh_skills()
    app.processEvents()
    assert len(tab._skill_rows["system"]) == 20 and tab.minimumSizeHint().height() == height_with_eight

    # The tab: Start Chat Window under the setup; the chat a separate top-level window, closed
    # until a chat is open. While one is open the setup is locked.
    chat = tab.chat_window
    assert tab.start_button.isEnabled() and tab.start_button.text() == "Start Chat Window"
    assert top(tab.start_button, tab) < top(tab.saving_notice, tab)
    assert chat.isWindow() and not chat.isVisible() and chat.parent() is window
    tab._set_state("ready", "Anthropic, claude-haiku-5-5")
    app.processEvents()
    assert chat.isVisible() and chat.windowTitle() == "mesoSPIM AI Assistant — Anthropic, claude-haiku-5-5"
    assert not tab.provider.isEnabled() and not tab.key.isEnabled()
    assert not any(box.isEnabled() for box in tab.skill_boxes.values())
    # In the window: the transcript on top, Stop microscope right of the input, on its line (Enter
    # sends); under them Cancel prompt, Clear context, Create skill and Show tool calls on one
    # line, in this order.
    stop, entry = chat.stop_button, chat.input
    assert chat.output.mapTo(chat, chat.output.rect().bottomLeft()).y() < entry.mapTo(chat, entry.rect().topLeft()).y()
    assert stop.mapTo(chat, stop.rect().topLeft()).y() == entry.mapTo(chat, entry.rect().topLeft()).y()
    assert stop.mapTo(chat, stop.rect().topLeft()).x() > entry.mapTo(chat, entry.rect().topRight()).x()
    row = (chat.interrupt, chat.clear_button, chat.create_skill_button, chat.show_tool_calls)
    middles = {b.mapTo(chat, b.rect().center()).y() for b in row}
    assert max(middles) - min(middles) <= 2 and min(middles) > entry.mapTo(chat, entry.rect().bottomLeft()).y()
    lefts = [b.mapTo(chat, b.rect().topLeft()).x() for b in row]
    assert lefts == sorted(lefts)
    tab._show_tokens(12_400)
    assert chat.clear_button.text() == "Clear context (12,400 tokens)"          # no model chosen here: no ceiling
    tab._set_state("idle")
    app.processEvents()
    assert not chat.isVisible() and tab.provider.isEnabled()

    # The question before a skill is saved shows the whole text, and Enter does not save.
    dialog = assistant_gui.SkillDialog(chat, "save_skill", {"name": "overview", "description": "A quick overview.",
                                                            "steps": "Snap once.", "tools": ["snap"]}, tab.font())
    assert "Snap once." in dialog.text.toPlainText() and dialog.text.isReadOnly()
    assert dialog.decline_button.isDefault() and not dialog.save_button.isDefault()
    dialog.deleteLater()

    question_crosses_the_threads(app, window)

    # Widths are only real with real fonts: offscreen Qt on Windows has none, and draws every
    # letter as a wide box.
    fonts = bool(QtGui.QFontDatabase().families())
    if fonts:
        assert width_cloud <= MAIN_WINDOW_WIDTH - FRAME, f"cloud setup needs {width_cloud} px"
        assert width_server <= MAIN_WINDOW_WIDTH - FRAME, f"own-address setup needs {width_server} px"

    # The input box: Enter sends, Shift+Enter starts a new line, as editors do. Only the key
    # handling is under test, so the tab's own submit slot is detached first.
    sent = []
    entry.returnPressed.disconnect(tab.on_submit)
    entry.returnPressed.connect(lambda: sent.append(entry.text()))
    chat.show()
    entry.setFocus()
    QtTest.QTest.keyClicks(entry, "centre the sample")
    QtTest.QTest.keyClick(entry, QtCore.Qt.Key_Return, QtCore.Qt.ShiftModifier)
    QtTest.QTest.keyClicks(entry, "then snap")
    assert sent == [] and entry.text() == "centre the sample\nthen snap"
    QtTest.QTest.keyClick(entry, QtCore.Qt.Key_Return)
    assert sent == ["centre the sample\nthen snap"]
    entry.setText("")

    tab.shutdown()
    print(f"REAL PYQT ASSISTANT SMOKE PASS: Qt {QtCore.QT_VERSION_STR}, "
          + (f"setup widths cloud={width_cloud} own-address={width_server}" if fonts
             else "setup widths not measured: no fonts on this platform, run with QT_QPA_PLATFORM=windows"))


if __name__ == "__main__":
    main()
