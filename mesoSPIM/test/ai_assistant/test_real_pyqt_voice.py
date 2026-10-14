"""Real-PyQt test for speaking to the AI Assistant: builds the tab and its window offscreen with a
stand-in microphone and a stand-in speech engine, never a model, a sound card or a server.

Checks the window as designed (the input with Speak to its right; Stop microscope third in the
row below; no token line until the session nears the model's limit), that Speak and F2 start and
stop listening, that the words appear in the input as they are spoken (settled ones plain, the
phrase still being worked out in grey italics), that stopping leaves the text editable and unsent,
that Esc throws a recording away, and that with speech switched off there is no Speak button."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

try:
    import numpy as np
    from PyQt5 import QtCore, QtTest, QtWidgets
except ModuleNotFoundError as error:
    raise SystemExit("test_real_pyqt_voice.py requires PyQt5 and numpy") from error

from mesoSPIM.src.ai_assistant import voice
from mesoSPIM.src.ai_assistant.gui import AiAssistantGUI
from mesoSPIM.src.remote_control.gui import RemoteControlGUI


class Core(QtCore.QObject):
    sig_remote_control_started = QtCore.pyqtSignal(bool, str)
    sig_warning = QtCore.pyqtSignal(str)
    cfg = types.SimpleNamespace()
    _remote_control = None
    _assistant_acceptor = None

    @QtCore.pyqtSlot(str, str, int, str)
    def start_remote_control(self, *_args):
        pass

    @QtCore.pyqtSlot()
    def stop_remote_control(self):
        pass


class Window(QtWidgets.QMainWindow):
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

    def display_warning(self, text):
        pass


class Microphone:
    """Hands over whatever the test pushes, as the sound card would, while it is on."""

    def __init__(self):
        self.on_block = None

    def start(self, on_block):
        self.on_block = on_block

    def stop(self):
        self.on_block = None

    def say(self, samples):
        if self.on_block is not None:
            self.on_block(np.asarray(samples, dtype=np.float32))


class Session:
    """A live session that hears one word per block: each block's first value picks the word.
    A block of zeros is a pause, which settles the phrase so far."""

    WORDS = {1: "Move", 2: "up", 3: "by", 4: "50", 5: "micrometres."}

    def __init__(self, engine, on_partial, on_final):
        self.on_partial, self.on_final, self.words = on_partial, on_final, []

    def feed(self, block):
        if block[0] == 0:
            self.flush()
        else:
            self.words.append(self.WORDS[int(block[0])])
            self.on_partial(" ".join(self.words))

    def flush(self):
        if self.words:
            self.on_final(" ".join(self.words))
            self.words = []

    def finish(self):
        self.flush()


def wait_until(condition, timeout_ms=3000):
    deadline = QtCore.QDeadlineTimer(timeout_ms)
    while not condition():
        if deadline.hasExpired():
            raise AssertionError("timed out")
        QtTest.QTest.qWait(10)


def connected_tab(window, speech):
    """The tab, as Connect leaves it, with speech on or off; no worker, no model."""
    tab = AiAssistantGUI(window)
    tab.speech.setChecked(speech)
    microphone = Microphone()
    if speech:
        tab.start_voice(voice.Voice(load_engine=lambda: object(), make_session=Session, microphone=microphone))
    tab._set_connect_state("ready", "a stand-in")
    return tab, microphone


def top(widget, window):
    return widget.mapTo(window, widget.rect().topLeft())


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()

    # The window as designed: the input and Speak on one line, Speak to the right; the controls
    # below in this order, Stop microscope third; no token line while the session is small.
    tab, microphone = connected_tab(window, speech=True)
    chat = tab.chat_window
    app.processEvents()
    wait_until(lambda: chat.speak_button.isEnabled())
    entry, speak = chat.input, chat.speak_button
    assert speak.isVisible() and speak.text() == "Speak"
    assert top(speak, chat).y() == top(entry, chat).y()
    assert top(speak, chat).x() > entry.mapTo(chat, entry.rect().topRight()).x()
    assert speak.height() == entry.height()
    row = [chat.interrupt, chat.clear_button, chat.stop_button, chat.show_tool_calls]
    lefts = [top(widget, chat).x() for widget in row]
    assert lefts == sorted(lefts), lefts
    assert top(chat.stop_button, chat).y() > top(entry, chat).y()
    assert chat.stop_button.text() == "Stop microscope"
    assert chat.input.placeholderText() == "Ask the microscope, or press F2 to speak"
    tab._show_tokens(3210)
    assert not chat.meter.isVisible(), "a small session shows no token count"
    warn, _ceiling = tab._ceiling()
    if warn != float("inf"):
        tab._show_tokens(int(warn))
        assert chat.meter.isVisible() and "Clear context" in chat.meter.text()
    tab._show_tokens(0)

    # Speak starts listening: the button says so, the input is read-only, the status line counts.
    chat.input.setText("Then ")
    QtTest.QTest.mouseClick(speak, QtCore.Qt.LeftButton)
    assert speak.text() == "Listening…" and chat.input.isReadOnly()
    assert chat.status.isVisible() and "0:00" in chat.status.text()

    # The words appear as they are spoken: the settled phrase plain, the forming one grey italics.
    for word in (1, 2):
        microphone.say([word])
    microphone.say([0])                                       # a pause settles "Move up"
    for word in (3, 4):
        microphone.say([word])
    wait_until(lambda: chat.input.text() == "Then Move up by 50")
    settled, forming = chat.input.dictation_formats()
    assert settled == "Then Move up " and forming == "by 50", (settled, forming)

    # F2 stops: the last phrase settles, the text is plain and editable, and nothing is sent.
    sent = []
    chat.input.returnPressed.disconnect(tab.on_submit)
    chat.input.returnPressed.connect(lambda: sent.append(chat.input.text()))
    QtTest.QTest.keyClick(chat, QtCore.Qt.Key_F2)
    wait_until(lambda: not chat.input.isReadOnly())
    assert chat.input.text() == "Then Move up by 50" and speak.text() == "Speak"
    assert chat.input.dictation_formats() == ("Then Move up by 50", "")
    assert sent == [] and not chat.status.isVisible()

    # Esc throws a recording away: the input is back to what it held before.
    QtTest.QTest.keyClick(chat, QtCore.Qt.Key_F2)
    microphone.say([5])
    wait_until(lambda: chat.input.text().endswith("micrometres."))
    QtTest.QTest.keyClick(chat, QtCore.Qt.Key_Escape)
    wait_until(lambda: not chat.input.isReadOnly())
    assert chat.input.text() == "Then Move up by 50" and speak.text() == "Speak"

    tab.on_disconnect()
    assert tab.voice is None

    # Speech switched off: no Speak button, F2 does nothing, the plain placeholder.
    quiet, _microphone = connected_tab(window, speech=False)
    app.processEvents()
    assert not quiet.chat_window.speak_button.isVisible()
    assert quiet.chat_window.input.placeholderText() == "Ask the microscope…"
    QtTest.QTest.keyClick(quiet.chat_window, QtCore.Qt.Key_F2)
    assert not quiet.chat_window.input.isReadOnly()
    quiet.on_disconnect()
    print("ok: speaking to the assistant")


if __name__ == "__main__":
    main()
