"""The 'AI Assistant' tab and the chat window.

The tab is the setup. It asks for three things only: the provider, the model and your API key.
Under them are two lists of skills, the microscope's own (system skills) and yours (personal
skills), each with a tick box. Start Chat Window checks the key with the provider and then opens
the chat with exactly the skills that are ticked. Everything else the assistant needs (the
system prompt, the folders, whether conversations are saved, whether camera images may be sent)
is in the microscope's config file, where the facility sets it once (settings.py).

The chat is a window of its own: your question bold on its own panel, the answer plain under the
commands it ran. Enter sends. While the assistant works the input is locked, so there is one
request at a time. Under the input are Cancel prompt (stops the assistant), Clear context (starts
the conversation afresh; the button shows how full the model's memory is), Create skill (asks
the assistant to write down the procedure you just did together) and Show tool calls. Stop
microscope, beside the input, stops the instrument itself, exactly as the main window's Stop
does.

There is one chat at a time. Closing the chat window ends it and hands the microscope back, so
that the Remote Control tab can start a server; the two cannot run together.

Maintainer (2026):
    Thom de Hoog
    Center for Microscopy and Image Analysis
    thom.dehoog@zmb.uzh.ch
    thomdehoog@gmail.com
"""

import dataclasses
import html as _htmllib
import json
import re
import os
import threading

from PyQt5 import QtCore, QtGui, QtWidgets

from . import config
from . import conversations
from . import keys
from . import settings as assistant_settings
from . import skills as skill_files
from .assistant import AssistantWorker, Desk, Endpoint, offered_commands

NO_COMMANDS_SENT = "no command was sent to the microscope in this turn"
PAIR_GAP = 14                   # px before an inner label, more than the 8 between it and its field
SKILL_LIST_HEIGHT = 112         # px: each skill list shows about four skills and scrolls for more
_ORPHANED_THREADS = []          # worker threads still in a model call at exit; kept so Qt never destroys a running one

_BUBBLE = "#2b3b47"      # the operator's own turns only — the answers stay on the tab background
_DIM = "#9aa7b0"         # tool-call and note text
_GREEN, _RED, _GREY, _ORANGE = "#5fd38d", "#ff6b6b", "#7d8a93", "#e0a040"

# --- the words the operator reads ---
KEY_NOTICE = ("Use your own API key. Your messages, and the camera images the assistant looks at, are sent to "
              "the provider you choose, and the use is billed to the owner of the key.")
REMEMBER_LABEL = "Remember this key on this computer"
REMEMBER_TIP = ("The key is kept in the password store of your Windows account, encrypted, so you do not have to "
                "type it again. Only tick this if you are the only person using this Windows account: anyone "
                "else on it would be spending on your key.")
CANNOT_REMEMBER_TIP = ("Keys cannot be remembered on this computer: the 'keyring' library is not installed. "
                       "Everything else works; the key is asked for each time mesoSPIM starts.")
NOT_SAVED_NOTICE = ("Conversations are not saved. The calls the assistant makes are written to the mesoSPIM log.")
SAVED_NOTICE = ("Conversations are being saved in {folder}. The calls the assistant makes are written to the "
                "mesoSPIM log.")
NO_SKILLS = "(none)"
CREATE_SKILL_SHOWN = "Create a skill from this conversation."
LOSE_ON_CLEAR = ("This conversation is not saved. If you clear the context it is gone for good.\n\n"
                 "Clear it?")
LOSE_ON_CLOSE = ("This conversation is not saved. If you close the chat it is gone for good.\n\n"
                 "Close it?")


def _md_to_html(markdown):
    """Render Markdown to an HTML body fragment (the model's bold/lists/etc.) via Qt's own parser."""
    doc = QtGui.QTextDocument()
    doc.setMarkdown(markdown)
    html = doc.toHtml()
    lower = html.lower()
    body, close = lower.find("<body"), lower.rfind("</body>")
    if body == -1 or close == -1:
        return _htmllib.escape(markdown)
    return _chat_sized(html[html.find(">", body) + 1:close].strip())


def _chat_sized(html):
    """Qt's Markdown sets inline code, which is how a model writes a file path, in a fixed point
    size smaller than the chat; drop the size and keep the fixed-width face."""
    return re.sub(r"\s*font-size:\s*[\d.]+pt;", "", html)


class _Input(QtWidgets.QPlainTextEdit):
    """A two-line message box. Enter sends; Shift+Enter starts a new line. Offers the QLineEdit
    names the tab uses (text, setText, returnPressed) so the rest of the tab does not care."""

    returnPressed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor("white"))   # "Ask the microscope…" reads
        self.setPalette(palette)

    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(text)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not event.modifiers() & QtCore.Qt.ShiftModifier:
            self.returnPressed.emit()
            return
        super().keyPressEvent(event)


def _field_label(text, parent, font, gap=PAIR_GAP):
    """A field's label: right against its field, with room before it so each label-and-field
    pair reads as one."""
    widget = QtWidgets.QLabel(parent)
    widget.setText(text)
    widget.setFont(font)
    widget.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    widget.setContentsMargins(gap, 0, 0, 0)
    return widget


def _describe(endpoint):
    return f"{endpoint.provider}, {endpoint.model}"


def skill_text(kind, details):
    """What the operator is shown before a skill is saved: its name, its description, the tools it
    uses and its steps, in full. For a changed skill, the steps as they are now follow, so the two
    can be compared."""
    lines = [f"Name: {details.get('name', '')}", f"Description: {details.get('description', '')}"]
    if details.get("tools"):
        lines.append("Tools it uses: " + ", ".join(details["tools"]))
    lines += ["", "Steps:", str(details.get("steps", "")).strip()]
    if kind == "edit_skill" and details.get("was"):
        lines += ["", "", "The steps as they are now:", str(details["was"]).strip()]
    return "\n".join(lines)


class SkillDialog(QtWidgets.QDialog):
    """The question the assistant puts to the operator before it saves a skill: the full text, and
    two buttons. Nothing is written unless Save skill is pressed; closing the dialog is a no."""

    def __init__(self, parent, kind, details, font):
        super().__init__(parent)
        new = kind == "save_skill"
        self.setWindowTitle("Save this new skill?" if new else "Save this change to your skill?")
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            ("The assistant would like to save this as one of your personal skills. Read it through, the "
             "description above all: it is what the assistant goes by when it decides whether to use the skill."
             if new else
             "The assistant would like to change one of your personal skills to the text below."), self)
        intro.setWordWrap(True)
        intro.setFont(font)
        layout.addWidget(intro)
        self.text = QtWidgets.QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(font)
        self.text.setPlainText(skill_text(kind, details))
        layout.addWidget(self.text, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QtWidgets.QPushButton("Save skill", self)
        self.decline_button = QtWidgets.QPushButton("Don't save", self)
        for button in (self.save_button, self.decline_button):
            button.setFont(font)
            buttons.addWidget(button)
        self.save_button.clicked.connect(self.accept)
        self.decline_button.clicked.connect(self.reject)
        self.decline_button.setDefault(True)             # Enter by accident does not save
        layout.addLayout(buttons)
        self.resize(640, 560)


class ConversationsDialog(QtWidgets.QDialog):
    """The operator's saved conversations, newest first, each with a Remove button. Removing one
    erases its file for good, after a question, and it no longer turns up in a search."""

    def __init__(self, parent, folder, font):
        super().__init__(parent)
        self.folder = folder
        self._font = font
        self.setWindowTitle("Saved conversations")
        layout = QtWidgets.QVBoxLayout(self)
        where = QtWidgets.QLabel(f"These are the conversations saved in {folder}. Only you can see them. The "
                                 "assistant can search them when you ask what was done before.", self)
        where.setWordWrap(True)
        where.setFont(font)
        layout.addWidget(where)
        self.area = QtWidgets.QScrollArea(self)
        self.area.setWidgetResizable(True)
        layout.addWidget(self.area, 1)
        close = QtWidgets.QPushButton("Close", self)
        close.setFont(font)
        close.clicked.connect(self.accept)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        self.remove_buttons = {}
        self.fill()
        self.resize(720, 480)

    def fill(self):
        """List the conversations as they are on disk now."""
        inner = QtWidgets.QWidget(self.area)
        rows = QtWidgets.QVBoxLayout(inner)
        rows.setAlignment(QtCore.Qt.AlignTop)
        self.remove_buttons = {}
        found = conversations.listing(self.folder)
        if not found:
            empty = QtWidgets.QLabel("No conversation is saved yet.", inner)
            empty.setFont(self._font)
            rows.addWidget(empty)
        for item in found:
            line = QtWidgets.QHBoxLayout()
            label = QtWidgets.QLabel(f"{item['started']}   {item['title']}", inner)
            label.setFont(self._font)
            label.setToolTip(f"{item['turns']} message(s). File: {item['path']}")
            button = QtWidgets.QPushButton("Remove", inner)
            button.setFont(self._font)
            button.clicked.connect(lambda _checked=False, path=item["path"]: self.on_remove(path))
            self.remove_buttons[item["path"]] = button
            line.addWidget(label, 1)
            line.addWidget(button)
            rows.addLayout(line)
        self.area.setWidget(inner)

    def on_remove(self, path):
        answer = QtWidgets.QMessageBox.question(
            self, "Remove this conversation?",
            "The conversation is erased for good and can no longer be searched.\n\nRemove it?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if answer == QtWidgets.QMessageBox.Yes:
            if not conversations.remove(path):
                QtWidgets.QMessageBox.warning(self, "Not removed", f"The file {path} could not be erased.")
            self.fill()


class AssistantWindow(QtWidgets.QWidget):
    """The chat, in a window of its own: the transcript, the input line with Stop microscope
    beside it, and under them Cancel prompt, Clear context, Create skill and Show tool calls. It
    exists while a chat is open: Start Chat Window in the tab opens it, and closing it ends the
    chat. The tab owns the session and the turn; this window is what it shows."""

    def __init__(self, tab, font):
        super().__init__(tab.main_window, QtCore.Qt.Window)
        self.tab = tab
        self.setObjectName("AiAssistantWindow")
        self.setWindowTitle("mesoSPIM AI Assistant")
        self.setStyleSheet("QPushButton { padding: 3px 9px; }"
                           "QPushButton#AiAssistantStopButton { color: #ff4d4d; font-weight: bold; }")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.output = QtWidgets.QTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setObjectName("AiAssistantOutput")
        self.output.setFont(font)
        self.output.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)          # wrap; no horizontal bar
        self.output.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)   # scrollbar from the start
        layout.addWidget(self.output, 1)

        self.status = QtWidgets.QLabel("", self)
        self.status.setObjectName("AiAssistantStatus")
        self.status.setFont(font)
        self.status.setVisible(False)                             # shown only while a turn runs
        layout.addWidget(self.status)

        self.input = _Input(self)
        self.input.setPlaceholderText("Ask the microscope…")
        self.input.setObjectName("AiAssistantInput")
        self.input.setFont(font)
        self.input.returnPressed.connect(tab.on_submit)
        self.interrupt = QtWidgets.QPushButton("Cancel prompt", self)
        self.interrupt.setFont(font)
        self.interrupt.setToolTip("Stops the assistant. Whatever the microscope is doing carries on.")
        self.interrupt.clicked.connect(tab.on_interrupt)      # always clickable; a no-op between turns
        self.stop_button = QtWidgets.QPushButton("Stop microscope", self)
        self.stop_button.setObjectName("AiAssistantStopButton")
        self.stop_button.setFont(font)
        self.stop_button.setToolTip("Stops the stage and whatever is running, as the main window's Stop does, "
                                    "and stops the assistant.")
        self.stop_button.clicked.connect(tab.on_stop_microscope)   # always enabled: the emergency stop
        # Clear context carries the size of the conversation: the tokens of the last request, out
        # of what the model takes. The history only grows, so this number does too, until cleared.
        self.clear_button = QtWidgets.QPushButton("Clear context", self)
        self.clear_button.setObjectName("AiAssistantClearButton")
        self.clear_button.setFont(font)
        self.clear_button.setToolTip("Empties the conversation and starts afresh, with the same model and skills. "
                                     "The numbers show how full the model's memory is.")
        self.clear_button.clicked.connect(tab.on_clear_all)
        self.create_skill_button = QtWidgets.QPushButton("Create skill", self)
        self.create_skill_button.setFont(font)
        self.create_skill_button.setToolTip("Asks the assistant to write down the procedure you just did together "
                                            "as a personal skill. You see the text and decide before it is saved.")
        self.create_skill_button.clicked.connect(tab.on_create_skill)
        self.show_tool_calls = QtWidgets.QCheckBox("Show tool calls", self)
        self.show_tool_calls.setFont(font)
        self.show_tool_calls.setChecked(config.SHOW_TOOL_CALLS)
        self.show_tool_calls.toggled.connect(lambda _checked: tab._render())
        # The two-line input, which Enter sends, with Stop microscope to its right, as tall as it;
        # under them the other controls on one line.
        two_rows = 2 * self.interrupt.sizeHint().height() + 6
        self.input.setFixedHeight(two_rows)
        self.stop_button.setFixedHeight(two_rows)
        entry = QtWidgets.QHBoxLayout()
        entry.addWidget(self.input, 1)
        entry.addWidget(self.stop_button)
        layout.addLayout(entry)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(6)
        for widget in (self.interrupt, self.clear_button, self.create_skill_button, self.show_tool_calls):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.resize(760, 880)                     # room for an answer of some lines above the input
        self.hide()

    def closeEvent(self, event):
        """The window's own close button: the operator is done with this chat. A conversation that
        is not saved would be lost, so the tab asks first."""
        if self.tab.may_close():
            event.accept()
            self.tab.on_window_closed()
        else:
            event.ignore()


class AiAssistantGUI(QtWidgets.QWidget):
    """The tab: the setup and the session. Provider, model and API key, the two skill lists, a
    status line and Start Chat Window; the chat is in the AssistantWindow, which that button
    opens."""

    sig_run_turn = QtCore.pyqtSignal(str)
    sig_check_run = QtCore.pyqtSignal()      # is the run the assistant started still under way?
    sig_key_checked = QtCore.pyqtSignal(bool, str)   # the provider's answer to the key check, from its thread

    # The key check talks to the provider over the network, which can take seconds, so it runs
    # on a thread of its own and the window stays responsive. The tests switch this off.
    checks_in_background = True
    # The tools the assistant has that are not Remote Control calls: a skill may name them.
    ASSISTANT_TOOLS = ("look", "ask_eyes", "calibrate", "load_skill", "get_status", "search", "read_passage",
                       "save_skill", "edit_skill")

    def __init__(self, parent):
        super().__init__(parent.TabWidget)
        self.main_window = parent
        self.core = parent.core
        self.setObjectName("AiAssistantTabWidget")
        self._worker = None
        self._thread = None
        self._state = "idle"                    # idle or ready; the status line shows it
        self._endpoints = {}                    # "language": the model that answers
        self._running = False                   # a turn is in flight
        self._run_turn_slot = None
        self._blocks = []                       # oldest first: HTML, or a finished answer's turn dict
        self._active = None                     # the running turn: {"tools", "reply", "error"}
        self._tokens = 0                        # the input tokens of the session's last request
        self._pending = None                    # the endpoint whose key is being checked
        self._pending_vision = None             # the second model that looks at images, checked with it
        self._vision_note = None                # why a vision model the config names is not in use
        self._entries = []                      # the skill files of both folders (skills.library)
        self.skill_boxes = {}                   # skill name -> its tick box
        self.remove_buttons = {}                # personal skill name -> its Remove button
        self._skill_rows = {skill_files.SYSTEM: [], skill_files.PERSONAL: []}
        self.check_key = keys.check
        # What the facility set in the config file, and what this person chose last time.
        self.settings = assistant_settings.read(getattr(self.core, "cfg", None))
        self.choices = assistant_settings.read_choices(self.settings)
        # The done notice: while a run the assistant started is under way, the worker is asked every
        # DONE_CHECK_MS whether it has ended. It never runs a turn.
        self._done_check = QtCore.QTimer(self)
        self._done_check.setInterval(config.DONE_CHECK_MS)
        self._done_check.timeout.connect(self.sig_check_run.emit)
        self.sig_key_checked.connect(self._on_key_checked)
        self._build_ui()
        index = parent.TabWidget.indexOf(parent.remote_control)   # RemoteControlGUI instance
        if index >= 0:
            parent.TabWidget.insertTab(index + 1, self, "AI Assistant")
        else:
            parent.TabWidget.addTab(self, "AI Assistant")

    def _call_on_core(self, method):
        """Invoke a Core slot on the Core thread (affinity matters — the Acceptor must be
        built there). Blocks until it returns."""
        try:
            same = self.core.thread() is self.thread()
        except AttributeError:                                     # Qt-free test doubles
            same = True
        conn = QtCore.Qt.DirectConnection if same else QtCore.Qt.BlockingQueuedConnection
        QtCore.QMetaObject.invokeMethod(self.core, method, conn)

    def _ensure_worker(self):
        """Acquire the Acceptor (built by Core, on the Core thread) and start the worker, on
        first use. Returns False if a TCP/MCP transport is active (mutually exclusive)."""
        if self._worker is not None:
            return True
        self._call_on_core("start_ai_assistant")
        acceptor = getattr(self.core, "_assistant_acceptor", None)
        if acceptor is None:
            return False
        self._thread = QtCore.QThread(self)
        self._worker = AssistantWorker(acceptor)
        self._worker.moveToThread(self._thread)
        self._run_turn_slot = self._worker.run_turn
        self.sig_run_turn.connect(self._run_turn_slot, QtCore.Qt.QueuedConnection)
        self.sig_check_run.connect(self._worker.check_run, QtCore.Qt.QueuedConnection)
        self._worker.sig_run_ended.connect(self._on_run_ended)
        self._worker.sig_usage.connect(self._on_usage)
        self._worker.sig_reply.connect(self._on_reply)
        self._worker.sig_tool.connect(self._on_tool)
        self._worker.sig_served.connect(self._on_served)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_done.connect(self._on_done)
        self._worker.sig_confirm.connect(self._on_confirm)
        self._worker.sig_skill_saved.connect(self._on_skill_saved)
        self._worker.sig_note.connect(self._on_note)
        self._apply_options()
        self._thread.start()
        return True

    def _apply_options(self, *_):
        """Hand the worker the settings that used to be boxes in this tab and now come from the
        config file: how much a camera image is shrunk, how sharpness is measured, and what a
        positive move on each axis does to the sample in the image."""
        if self._worker is not None:
            self._worker.look_image_bin = self.settings.look_bin
            self._worker.focus_metric = self.settings.focus_metric
            self._worker.axes = self.axes()

    def axes(self):
        """What a positive move on x, y and z does to the sample in the image, from the config file."""
        return dict(self.settings.axes)

    # --- building the tab ---
    def _build_ui(self):
        # Only the padding: qdarkstyle's buttons hug their text, its labels carry 7 px a side that
        # the setup grids do not want, and every other property cascades.
        self.setStyleSheet("QPushButton { padding: 3px 9px; }"
                           "QGroupBox QLabel { padding: 0px; }")
        font = self.font()
        font.setPointSize(12)                                     # match Remote Control
        self._font = font
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        group = QtWidgets.QGroupBox("Setup AI assistant", self)
        group.setFont(font)
        column = QtWidgets.QVBoxLayout(group)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(8)

        # Provider, model and key: the three things an operator decides.
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        self.provider = QtWidgets.QComboBox(group)
        self.provider.addItems(list(config.PROVIDERS))
        self.provider.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.model = QtWidgets.QLineEdit("", group)
        self.model.setMinimumWidth(120)
        self.key = QtWidgets.QLineEdit("", group)
        self.key.setEchoMode(QtWidgets.QLineEdit.Password)      # dots, never the characters
        self.key.setMinimumWidth(160)
        # The light: grey until the key has been checked, green when the provider accepted it,
        # red when it did not. The sentence beside it says which.
        self.light = QtWidgets.QLabel("●", group)
        self.light.setObjectName("AiAssistantLight")
        self.light_text = QtWidgets.QLabel("", group)
        self.remember = QtWidgets.QCheckBox(REMEMBER_LABEL, group)
        self.forget_button = QtWidgets.QPushButton("Forget key", group)
        self.forget_button.setToolTip("Removes the key that was remembered for this provider.")
        self.forget_button.clicked.connect(self.on_forget_key)
        for widget in (self.provider, self.model, self.key, self.light, self.light_text, self.remember,
                       self.forget_button):
            widget.setFont(font)
        grid.addWidget(_field_label("Provider", group, font, gap=0), 0, 0)
        grid.addWidget(self.provider, 0, 1)
        grid.addWidget(_field_label("Model", group, font), 0, 2)
        grid.addWidget(self.model, 0, 3, 1, 3)
        grid.addWidget(_field_label("API key", group, font, gap=0), 1, 0)
        grid.addWidget(self.key, 1, 1, 1, 3)
        grid.addWidget(self.light, 1, 4)
        grid.addWidget(self.light_text, 1, 5)
        grid.addWidget(self.remember, 2, 1, 1, 3)
        grid.addWidget(self.forget_button, 2, 4, 1, 2)
        grid.setColumnStretch(3, 1)
        column.addLayout(grid)
        self.key_notice = QtWidgets.QLabel(KEY_NOTICE, group)
        self.key_notice.setWordWrap(True)
        self.key_notice.setFont(font)
        column.addWidget(self.key_notice)

        # The two skill lists. Each has a fixed height and scrolls, so the tab keeps its size
        # however many skills there are.
        self._skill_layouts = {}
        for kind, title, tip in (
                (skill_files.SYSTEM, "System skills",
                 "Procedures that belong to this microscope. Everyone who uses it sees them."),
                (skill_files.PERSONAL, "Personal skills",
                 "Your own procedures. Only you see them. The assistant can write one for you from a "
                 "conversation (Create skill, in the chat window).")):
            label = QtWidgets.QLabel(title, group)
            label.setFont(font)
            label.setToolTip(tip)
            column.addWidget(label)
            area = QtWidgets.QScrollArea(group)
            area.setWidgetResizable(True)
            area.setFixedHeight(SKILL_LIST_HEIGHT)
            inner = QtWidgets.QWidget(area)
            rows = QtWidgets.QVBoxLayout(inner)
            rows.setContentsMargins(6, 4, 6, 4)
            rows.setSpacing(2)
            rows.setAlignment(QtCore.Qt.AlignTop)
            area.setWidget(inner)
            column.addWidget(area)
            self._skill_layouts[kind] = (inner, rows)

        # The status line: what the session is doing, and what a failed start needs.
        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(_field_label("Status", group, font, gap=0))
        self.status_label = QtWidgets.QLabel(group)
        self.status_label.setObjectName("AiAssistantStatusLabel")
        self.status_label.setFont(font)
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        column.addLayout(status_row)

        self.start_button = QtWidgets.QPushButton("Start Chat Window", group)
        self.start_button.setFont(font)
        self.start_button.setToolTip("Checks your key with the provider and opens a new chat with the skills "
                                     "that are ticked. While a chat is open, this brings it to the front.")
        self.start_button.clicked.connect(self.on_start_chat)
        self.conversations_button = QtWidgets.QPushButton("Saved conversations…", group)
        self.conversations_button.setFont(font)
        self.conversations_button.clicked.connect(self.on_saved_conversations)
        self.conversations_button.setVisible(self.settings.save_conversations)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.conversations_button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        # Whether conversations are kept is the facility's decision (the config file); the tab
        # always says which it is, and where the calls are recorded.
        self.saving_notice = QtWidgets.QLabel(self.saving_text(), group)
        self.saving_notice.setWordWrap(True)
        self.saving_notice.setFont(font)
        column.addWidget(self.saving_notice)
        layout.addWidget(group)
        layout.addStretch(1)
        self.chat_window = AssistantWindow(self, font)

        self.provider.setCurrentText(self.choices.get("provider")
                                     if self.choices.get("provider") in config.PROVIDERS else config.DEFAULT_PROVIDER)
        self.provider.currentTextChanged.connect(self._on_provider_changed)
        self.key.textChanged.connect(self._on_key_typed)
        self._on_provider_changed(self.provider.currentText())
        self.refresh_skills()
        self._set_state("idle")
        if self.settings.problems:                                # a line of the config file that could not be used
            self.status_label.setText(" ".join(self.settings.problems))

    def saving_text(self):
        """The sentence that says whether conversations are saved, and where."""
        if self.settings.save_conversations:
            return SAVED_NOTICE.format(folder=self.settings.conversations)
        return NOT_SAVED_NOTICE

    def showEvent(self, event):
        """Each time the tab is shown the skill folders are read again, so a file added or changed
        by hand appears without restarting mesoSPIM."""
        if self._state == "idle":
            self.refresh_skills()
        super().showEvent(event)

    # --- provider, model and key ---
    def _on_provider_changed(self, name):
        """Prefill the model for this provider (the one used last time, else the usual one), and
        say in the key field where the key will come from when nothing is typed."""
        preset = config.PROVIDERS[name]
        self.model.setText((self.choices.get("models") or {}).get(name) or preset["model"])
        self.key.setText("")
        key_env = preset.get("key_env")
        own_address = preset["kind"] == "openai-compatible"
        kept = bool(keys.remembered(name))
        if kept:
            placeholder = "your remembered key is used"
        elif own_address:
            placeholder = "optional"
        elif key_env and os.environ.get(key_env):
            placeholder = f"using {key_env} from the environment"
        else:
            placeholder = f"{name} API key"
        self.key.setPlaceholderText(placeholder)
        can = keys.can_remember()
        self.remember.setEnabled(can and self._state == "idle")
        self.remember.setChecked(kept)
        self.remember.setToolTip(REMEMBER_TIP if can else CANNOT_REMEMBER_TIP)
        self.forget_button.setEnabled(kept and self._state == "idle")
        self._set_light(None, "not checked yet")

    def _on_key_typed(self, *_):
        if self._pending is None and self._state == "idle":
            self._set_light(None, "not checked yet")           # a new key has not been checked

    def _set_light(self, accepted, text):
        """Green for a key the provider accepted, red for one it did not, grey for not yet known."""
        colour = _GREY if accepted is None else (_GREEN if accepted else _RED)
        self.light.setStyleSheet(f"color: {colour};")
        self.light_text.setText(text)
        self.light_state = accepted

    def on_forget_key(self):
        """Remove the key remembered for the chosen provider."""
        name = self.provider.currentText()
        if keys.forget(name):
            self.status_label.setText(f"The remembered key for {name} was removed.")
        else:
            self.status_label.setText(f"The remembered key for {name} could not be removed.")
        self._on_provider_changed(name)

    def _chosen_endpoint(self):
        """The model the setup names. The key is the one typed, else the remembered one, else the
        one in the environment. The address of a model that the facility hosts itself, and whether
        camera images may be sent, come from the config file."""
        name = self.provider.currentText()
        preset = config.PROVIDERS[name]
        own_address = preset["kind"] == "openai-compatible"
        key = self.key.text().strip() or keys.remembered(name)
        if own_address:
            sees = self.settings.vision is True          # mesoSPIM cannot know; the config file says
        else:
            sees = False if self.settings.vision is False else None      # None: as the model allows
        endpoint = Endpoint.from_preset(name, self.model.text(), key,
                                        self.settings.address if own_address else "", vision=sees)
        if self.settings.request_interval_s > 0:        # the config file spaces the requests, for a tight limit
            endpoint = dataclasses.replace(endpoint, request_interval_s=self.settings.request_interval_s)
        return endpoint

    def _chosen_vision_endpoint(self, answering):
        """The second model that looks at the camera images, when the config file names one
        (its `vision_model` block), else None. Returns (endpoint, note): the note is a sentence
        for the chat when the config asks for a vision model and it cannot be used.

        Its key is found without a second field in the tab. When it is at the same provider as
        the model that answers, the key in use for that one serves both. Otherwise it is the key
        this person had remembered for that provider, else the provider's environment variable."""
        wanted = self.settings.vision_provider
        if not wanted or self.settings.vision is False:        # none named, or images are forbidden altogether
            return None, None
        own_address = config.PROVIDERS[wanted]["kind"] == "openai-compatible"
        key = answering.api_key if wanted == answering.provider else keys.remembered(wanted)
        endpoint = Endpoint.from_preset(wanted, self.settings.vision_model, key,
                                        self.settings.vision_address if own_address else "", vision=True)
        if endpoint.needs_key and not endpoint.api_key:
            key_env = config.PROVIDERS[wanted].get("key_env")
            return None, (f"The vision model ({wanted}) has no key. To give it one, choose {wanted} in the tab once, "
                          f"type your key and tick '{REMEMBER_LABEL}', or set {key_env} before starting mesoSPIM. "
                          "Until then the model that answers looks at the images itself, if it can.")
        if self.settings.request_interval_s > 0:
            endpoint = dataclasses.replace(endpoint, request_interval_s=self.settings.request_interval_s)
        return endpoint, None

    # --- the skill lists ---
    def refresh_skills(self):
        """Read both skill folders and rebuild the two lists. A file that cannot be used is shown
        greyed out with the reason, and a skill that names a call mesoSPIM no longer has is marked
        as out of date."""
        offered = [cmd.name for cmd in offered_commands()] + list(self.ASSISTANT_TOOLS)
        self._entries = skill_files.library(self.settings.system_skills, self.settings.personal_skills, offered)
        switched_off = set(self.choices.get("skills_off") or [])
        self.skill_boxes, self.remove_buttons = {}, {}
        editable = self._state == "idle"
        for kind, (inner, rows) in self._skill_layouts.items():
            for row in self._skill_rows[kind]:
                rows.removeWidget(row)
                row.hide()                      # gone from the list at once; Qt frees it a moment later
                row.setParent(None)
                row.deleteLater()
            self._skill_rows[kind] = []
            entries = [entry for entry in self._entries if entry.kind == kind]
            entries.sort(key=lambda entry: not entry.usable)      # the files that cannot be used go last
            if not entries:
                empty = QtWidgets.QLabel(NO_SKILLS, inner)
                empty.setFont(self._font)
                rows.addWidget(empty)
                self._skill_rows[kind].append(empty)
            for entry in entries:
                row = QtWidgets.QWidget(inner)
                line = QtWidgets.QHBoxLayout(row)
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(6)
                if entry.usable:
                    text, tip = entry.key, entry.description
                    if entry.missing_tools:
                        text += f"   (out of date: no tool {', '.join(entry.missing_tools)})"
                        tip += ("\n\nThis skill uses a call that this version of mesoSPIM does not have, so it "
                                "would fail half way. Change the skill, or untick it.")
                else:
                    text, tip = f"{entry.path.name}   (cannot be used)", entry.problem
                box = QtWidgets.QCheckBox(text, row)
                box.setFont(self._font)
                box.setToolTip(tip)
                box.setChecked(entry.usable and entry.key not in switched_off)
                box.setEnabled(entry.usable and editable)
                if entry.usable:
                    box.toggled.connect(lambda checked, key=entry.key: self._on_tick(key, checked))
                    self.skill_boxes[entry.key] = box
                line.addWidget(box, 1)
                if kind == skill_files.PERSONAL:
                    button = QtWidgets.QPushButton("Remove", row)
                    button.setFont(self._font)
                    button.setEnabled(editable)
                    button.setToolTip("Takes this skill out of your list. The file is moved to a 'deleted' "
                                      "folder next to your skills, so it can be put back.")
                    button.clicked.connect(lambda _checked=False, chosen=entry: self.on_remove_skill(chosen))
                    self.remove_buttons[entry.key] = button
                    line.addWidget(button)
                # Each row keeps its full height, so a long list scrolls and is never squeezed.
                row.setMinimumHeight(max(box.sizeHint().height(), 22) + 2)
                rows.addWidget(row)
                self._skill_rows[kind].append(row)
            # Tell the scrolling area how tall the whole list is: the rows, the gaps between them
            # and the margins. Without this it squeezes the rows into the height it has.
            heights = [row.minimumHeight() for row in self._skill_rows[kind]]
            inner.setMinimumHeight(sum(heights) + 2 * max(len(heights) - 1, 0) + 8)

    def _on_tick(self, key, checked):
        """A skill was ticked or unticked: remember it for this person. The next chat follows it;
        a chat that is already open keeps what it started with."""
        switched_off = set(self.choices.get("skills_off") or [])
        if checked:
            switched_off.discard(key)
        else:
            switched_off.add(key)
        self.choices["skills_off"] = sorted(switched_off)
        assistant_settings.write_choices(self.settings, self.choices)

    def on_remove_skill(self, entry):
        """Remove a personal skill, after a question. The file is moved, not erased."""
        answer = QtWidgets.QMessageBox.question(
            self, "Remove this skill?",
            f"Remove your skill '{entry.key}' from the list?\n\nThe file is moved to the 'deleted' folder next to "
            "your skills, so you can put it back if this was a mistake.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            moved = skill_files.remove_personal(entry.path)
            self.status_label.setText(f"The skill '{entry.key}' was moved to {moved.parent}.")
        except OSError as error:
            self.status_label.setText(f"The skill '{entry.key}' could not be removed: {error}")
        self.refresh_skills()

    # --- the session ---
    def _set_state(self, state, detail=""):
        """idle: the setup can be changed and no chat is open. ready: a chat is open, the window
        titled with the model that answers, and the setup is locked until that chat ends."""
        self._state = state
        if state == "idle":
            self.status_label.setText("no chat is open")
        else:
            self.status_label.setText(f"a chat is open: {detail}")
        for widget in (self.provider, self.model, self.key):
            widget.setEnabled(state == "idle")
        self.remember.setEnabled(state == "idle" and keys.can_remember())
        self.forget_button.setEnabled(state == "idle" and bool(keys.remembered(self.provider.currentText())))
        for widget in list(self.skill_boxes.values()) + list(self.remove_buttons.values()):
            widget.setEnabled(state == "idle")
        if state == "ready":
            self.chat_window.setWindowTitle(f"mesoSPIM AI Assistant — {detail}")
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
        else:
            self._done_check.stop()
            self.chat_window.hide()

    # --- starting a chat ---
    def on_start_chat(self):
        """Start Chat Window. With a chat already open, bring it to the front: there is one chat
        at a time. Otherwise check the key with the provider first, and open the chat when the
        provider has accepted it (_on_key_checked)."""
        if self._state == "ready":
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
            return
        if self._pending is not None:
            return                                          # the check is already under way
        endpoint = self._chosen_endpoint()
        vision, self._vision_note = self._chosen_vision_endpoint(endpoint)
        self._pending, self._pending_vision = endpoint, vision
        self.start_button.setEnabled(False)
        self._set_light(None, "checking…")
        self.status_label.setText(f"Checking the key with {endpoint.provider}…")

        def work():
            try:
                accepted, sentence = self.check_key(endpoint)
            except Exception as error:                      # the check itself must never crash the tab
                accepted, sentence = False, f"The key could not be checked: {error}"
            # The vision model is checked too. If its provider refuses, the chat still opens:
            # the model that answers then looks at the images itself, and the chat says why.
            if accepted and vision is not None:
                try:
                    seeing, why = self.check_key(vision)
                except Exception as error:
                    seeing, why = False, f"its key could not be checked: {error}"
                if not seeing:
                    self._pending_vision = None
                    self._vision_note = (f"The vision model ({vision.provider}, {vision.model}) cannot be used: {why} "
                                         "The model that answers looks at the images itself, if it can.")
            self.sig_key_checked.emit(accepted, sentence)
        if self.checks_in_background:
            threading.Thread(target=work, name="ai-assistant-key-check", daemon=True).start()
        else:
            work()

    def _on_key_checked(self, accepted, sentence):
        """The provider's answer. A key that was not accepted stops here, with the reason on the
        status line; an accepted one is remembered if the box is ticked, and the chat opens."""
        endpoint, self._pending = self._pending, None
        vision, self._pending_vision = self._pending_vision, None
        self.start_button.setEnabled(True)
        if endpoint is None:
            return
        self._set_light(accepted, "connected" if accepted else "not connected")
        if not accepted:
            self.status_label.setText(sentence)
            return
        typed = self.key.text().strip()
        if typed and self.remember.isChecked() and keys.can_remember():
            keys.remember(endpoint.provider, typed)
        self.choices["provider"] = endpoint.provider
        self.choices.setdefault("models", {})[endpoint.provider] = endpoint.model
        assistant_settings.write_choices(self.settings, self.choices)
        self._open_chat(endpoint, vision)

    def _open_chat(self, endpoint, vision=None):
        """Take the session, hand the worker the model and what this chat is given, and open the
        window. `vision` is the second model that looks at the camera images, when the config
        file names one. Returns False, after a note that says what to do, when the microscope is
        taken by the Remote Control."""
        if not self._ensure_worker():
            self._note(getattr(self.core, "_assistant_refusal", None)
                       or "Stop the Remote Control transport to use the AI Assistant.")
            return False
        self._apply_options()
        self._endpoints = {"language": endpoint}
        if vision is not None:
            self._endpoints["vision"] = vision
        self._blocks, self._active = [], None
        self._worker.configure(endpoint, vision)
        self.refresh_skills()                                   # the files as they are now
        switched_off = set(self.choices.get("skills_off") or [])
        ticked = skill_files.chosen(self._entries, switched_off)
        desk = Desk(skills=ticked,
                    switched_off=tuple(entry.key for entry in self._entries if entry.usable and entry.key in switched_off),
                    personal_skills=self.settings.personal_skills,
                    documentation=self.settings.documentation,
                    conversations=self.settings.conversations,
                    saving=self.settings.save_conversations)
        recorder_factory = None
        if self.settings.save_conversations:
            folder, model = self.settings.conversations, endpoint.model
            recorder_factory = lambda: conversations.Recorder(folder, model)        # noqa: E731
        ceiling = self._ceiling()[1]
        self._worker.open_desk(desk, system_prompt_file=self.settings.system_prompt,
                               recorder_factory=recorder_factory,
                               context_max_tokens=None if ceiling == float("inf") else ceiling)
        # The chat opens with what it was given, so the operator sees it and can scroll back to it.
        opening = ["Skills in this chat: " + ", ".join(ticked) if ticked else "No skill is ticked for this chat."]
        opening += [f"The skill '{entry.key}' is out of date: it uses {', '.join(entry.missing_tools)}, which "
                    "mesoSPIM no longer has." for entry in self._entries if entry.key in ticked and entry.missing_tools]
        opening += list(self.settings.problems)
        if self._vision_note:
            opening.append(self._vision_note)
        if vision is not None:
            opening.append(f"Camera images are looked at by a second model ({_describe(vision)}). The model that "
                           "answers is told what that one sees and receives no image itself.")
        elif not endpoint.vision:
            opening.append("Camera images are not sent: the assistant works from the numbers that describe an image.")
        opening.append(self.saving_text())
        self._blocks = [self._note_block(line) for line in opening]
        self._show_tokens(0)
        self._render()
        detail = _describe(endpoint)
        if vision is not None:
            detail += f"; vision: {_describe(vision)}"
        if endpoint.request_interval_s > 0:
            detail += f"; {endpoint.request_interval_s:g} s between requests"
        self._set_state("ready", detail)
        return True

    # --- ending a chat ---
    def has_conversation(self):
        """True once the operator has said something in this chat."""
        return self._active is not None or any(isinstance(block, dict) for block in self._blocks)

    def may_close(self):
        """May the chat window close? Always when the conversation is saved or empty; otherwise
        the operator is asked, because closing would lose it."""
        if self._state != "ready" or self.settings.save_conversations or not self.has_conversation():
            return True
        return self._ask_yes_no("Close this chat?", LOSE_ON_CLOSE)

    def _ask_yes_no(self, title, text):
        answer = QtWidgets.QMessageBox.question(self.chat_window, title, text,
                                                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                                                QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes

    def on_end_chat(self):
        """End the chat and hand the session back so the Remote Control tab can start a
        transport, without restarting mesoSPIM: a running turn is cancelled, the window closes,
        and the next chat starts fresh. A run the assistant started carries on, and the main
        window's STOP ends it."""
        if self._state == "idle":
            return
        self._release_session()
        if self._running:
            self._set_running(False)
        self._blocks, self._active = [], None
        self._show_tokens(0)
        self._render()
        self._set_state("idle")
        self.refresh_skills()                   # a skill made in that chat can now be ticked

    def on_window_closed(self):
        """The chat window's ×: the chat is over."""
        self.on_end_chat()

    def _release_session(self):
        """Stop the worker, joining with a bound so the GUI never hangs on an in-flight model call,
        and release the Core-owned Acceptor."""
        self._endpoints = {}
        self._done_check.stop()
        if self._worker is None:
            return
        self._worker.interrupt()
        self._worker.answer(False)              # a question still open is answered no
        if self._run_turn_slot is not None:
            self.sig_run_turn.disconnect(self._run_turn_slot)
            self.sig_check_run.disconnect(self._worker.check_run)
            self._run_turn_slot = None
        self._thread.quit()
        if not self._thread.wait(3000):         # still inside a model call: let it be, never qFatal
            self._thread.setParent(None)
            _ORPHANED_THREADS.append(self._thread)
        self._call_on_core("stop_ai_assistant")
        self._worker, self._thread = None, None

    def _note(self, text):
        """What a start needs: on the status line, where the setup is, and in the transcript,
        where it stays readable once the chat is open."""
        self.status_label.setText(text)
        self._blocks.append(self._note_block(text))
        self._render()

    # --- questions from the assistant, and what it saved ---
    def _on_confirm(self, kind, details_json):
        """The assistant asks to save or change a skill: show the full text, and pass the
        operator's answer back. The tool that asked is waiting for it."""
        try:
            details = json.loads(details_json)
        except ValueError:
            details = {}
        agreed = self._confirm_skill(kind, details)
        if self._worker is not None:
            self._worker.answer(agreed)

    def _confirm_skill(self, kind, details):
        """True when the operator pressed Save skill."""
        dialog = SkillDialog(self.chat_window, kind, details, self._font)
        return dialog.exec_() == QtWidgets.QDialog.Accepted

    def _on_skill_saved(self, name):
        """A personal skill was saved or changed: say so in the chat and show it in the list."""
        self._blocks.append(self._note_block(f"The skill '{name}' was saved to your personal skills."))
        self._render()
        self.refresh_skills()

    def _on_note(self, text):
        """A grey line from the worker that belongs to no turn (a conversation that could not be saved)."""
        self._blocks.append(self._note_block(text))
        self._render()

    def on_saved_conversations(self):
        """Show this person's saved conversations, where one can be removed."""
        ConversationsDialog(self, self.settings.conversations, self._font).exec_()

    # --- transcript rendering ---
    def _user_block(self, text):
        """The question, bold on its own lighter panel. Only the operator's turns are panelled, so
        the transcript reads as the microscope answering into your log rather than as two
        symmetrical speakers — which is also what makes the 'You' label unnecessary."""
        return ('<table width="100%" cellspacing="0" cellpadding="8" style="margin:14px 0 2px 0;">'
                f'<tr><td style="background-color:{_BUBBLE};">'
                f'<b>{_htmllib.escape(text)}</b></td></tr></table>')

    def _assistant_block(self, active):
        """The answer, unbolded and unpanelled, under the commands it ran. The left margin lines it
        up with the question text inside the panel above rather than with the panel's edge. Nothing
        is emitted until the first tool call or the reply arrives — the status line already says the
        turn is running."""
        parts = []
        for name, args in active["tools"] if self.chat_window.show_tool_calls.isChecked() else ():
            parts.append(f'<div style="color:{_DIM};">&#8250; {_htmllib.escape(name)}'
                         f'({_htmllib.escape(args)})</div>')
        if active.get("served"):
            parts.append(f'<div style="color:#e0c080;"><b>&#9888;</b> {_htmllib.escape(active["served"])}</div>')
        if active["error"] is not None:
            parts.append(f'<div style="color:#e08a8a;"><b>&#9888; error</b> — '
                         f'{_htmllib.escape(active["error"])}</div>')
        elif active["reply"] is not None:
            if not active["tools"] and self.chat_window.show_tool_calls.isChecked():
                # A small model will write "I have closed the shutters" having called nothing. The
                # tab cannot judge the sentence, but it knows what it sent: said with the tool calls.
                parts.append(f'<div style="color:{_DIM};">&#8250; {_htmllib.escape(NO_COMMANDS_SENT)}</div>')
            parts.append(_md_to_html(active["reply"]))
        # Qt drops a bottom margin before the next question's table: an empty line keeps the air.
        return f'<div style="margin:12px 0 0 8px;">{"".join(parts)}</div><p style="margin:0;">&nbsp;</p>'

    def _note_block(self, text):
        return f'<div style="color:{_DIM};margin:3px 0;"><i>{_htmllib.escape(text)}</i></div>'

    def _render(self):
        output = self.chat_window.output
        blocks = [self._assistant_block(b) if isinstance(b, dict) else b for b in self._blocks]
        if self._active is not None:
            blocks.append(self._assistant_block(self._active))
        output.setHtml("".join(blocks))
        cursor = output.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)               # collapse to the end: nothing selected
        output.setTextCursor(cursor)
        output.ensureCursorVisible()                             # scroll to the newest line

    # --- input / turn lifecycle ---
    def on_submit(self):
        text = self.chat_window.input.text().strip()
        if not text or not self.chat_window.input.isEnabled() or self._state != "ready":
            return
        if self._context_full():
            return
        self.chat_window.input.clear()
        self._submit(text)

    def _context_full(self):
        """True, after a grey line that says so, when the conversation is as large as the model
        takes. No turn starts then until Clear context: nothing is trimmed behind the operator's
        back."""
        if self._tokens < self._ceiling()[1]:
            return False
        self._blocks.append(self._note_block(config.CONTEXT_FULL))
        self._render()
        return True

    def _submit(self, text, shown=None):
        """One turn: the operator's message. `shown` is what the transcript shows in its place,
        for a button that sends a longer request than its label."""
        self._blocks.append(self._user_block(shown or text))
        self._active = {"tools": [], "reply": None, "error": None}
        self._set_running(True)
        self._render()
        self.sig_run_turn.emit(text)

    def on_create_skill(self):
        """Create skill: ask the assistant to look back over the conversation, choose the
        procedure worth keeping and propose it as a personal skill. It then calls save_skill,
        which shows the operator the text and waits for a yes."""
        if self._running or self._state != "ready":
            return
        if not self.has_conversation():
            self._blocks.append(self._note_block("There is no conversation yet to make a skill from. Do the "
                                                 "procedure with the assistant first, then press Create skill."))
            self._render()
            return
        if self._context_full():
            return
        self._submit(config.CREATE_SKILL_REQUEST, shown=CREATE_SKILL_SHOWN)

    # --- the size of the conversation, on the Clear context button ---
    def _ceiling(self):
        """The language model's (warning, ceiling) in input tokens; none for an unknown server."""
        language = self._endpoints.get("language")
        preset = config.PROVIDERS.get(language.provider, {}) if language is not None else {}
        return preset.get("context_warn_tokens", float("inf")), preset.get("context_max_tokens", float("inf"))

    def _on_usage(self, tokens):
        if tokens:                              # a provider that counted nothing keeps the last count
            self._show_tokens(tokens)

    def _show_tokens(self, tokens):
        """Clear context (12,400 of 900,000 tokens): the size of the whole conversation as the
        provider last counted it, out of what this model takes. The number turns orange where a
        request starts to cost more, and red at the ceiling, where no turn starts until cleared."""
        self._tokens = tokens
        warn, ceiling = self._ceiling()
        size = f"{tokens:,} tokens" if ceiling == float("inf") else f"{tokens:,} of {ceiling:,} tokens"
        button = self.chat_window.clear_button
        button.setText(f"Clear context ({size})")
        if tokens >= ceiling:
            button.setStyleSheet(f"color: {_RED}; font-weight: bold;")
            button.setToolTip(config.CONTEXT_FULL)
        elif tokens >= warn:
            button.setStyleSheet(f"color: {_ORANGE};")
            button.setToolTip(config.CONTEXT_LARGE)
        else:
            button.setStyleSheet("")
            button.setToolTip("Empties the conversation and starts afresh, with the same model and skills. "
                              "The numbers show how full the model's memory is.")

    # --- the done notice ---
    def _on_run_ended(self, text):
        """The run the assistant started has ended: one grey line, no model turn."""
        self._done_check.stop()
        self._blocks.append(self._note_block(text))
        self._render()

    def on_interrupt(self):
        if not self._running:
            return                                  # nothing is running
        if self._worker is not None:
            self._worker.interrupt()
        self._blocks.append(self._note_block("[cancelled]"))
        self._render()

    def on_stop_microscope(self):
        """The emergency stop, exactly as the main window's Stop button does it and just as fast:
        from the GUI thread, the same queued signals to Core (state idle aborts the running mode,
        the time lapse is cancelled) plus the stage stop, with no assistant thread or dispatcher
        in between. The assistant is cancelled too."""
        if self._worker is not None:
            self._worker.interrupt()
        self.main_window.stop_acquisition_and_timelapse()
        self.main_window.sig_stop_movement.emit()
        self._blocks.append(self._note_block("[stop microscope]"))
        self._render()

    def on_clear_all(self):
        """Clear context: empty the transcript and the model's memory of it. The model and the
        skills stay. A conversation that is not saved would be lost, so the operator is asked
        first; a saved one stays on disk and can still be searched."""
        if self._running:
            return                                  # not while a turn runs
        if not self.settings.save_conversations and self.has_conversation() \
                and not self._ask_yes_no("Clear the context?", LOSE_ON_CLEAR):
            return
        self._blocks = []
        self._active = None
        if self._worker is not None:
            self._worker.reset()
        self._show_tokens(0)
        self._render()

    def _set_running(self, running):
        self._running = running
        window = self.chat_window
        window.input.setEnabled(not running)
        window.clear_button.setEnabled(not running)   # the memory is cleared between turns
        window.create_skill_button.setEnabled(not running)
        window.status.setText("mesoSPIM is working…" if running else "")
        window.status.setVisible(running)
        if not running:
            window.input.setFocus()

    def _on_reply(self, text):
        if self._active is not None:
            self._active["reply"] = text
            self._render()

    def _on_tool(self, name, args):
        if self._active is not None:
            self._active["tools"].append((name, args))
            self._render()
        if name in config.RUNS_ON_ITS_OWN:
            self._done_check.start()            # the worker says when it is under way, and when it ends

    def _on_served(self, text):
        if self._active is not None:
            self._active["served"] = text
            self._render()

    def _on_error(self, message):
        if self._active is not None:
            self._active["error"] = message
            self._render()

    def _on_done(self):
        if self._active is not None:
            self._blocks.append(self._active)   # kept as the turn, so Show tool calls reaches it
            self._active = None
        self._set_running(False)
        self._render()

    def shutdown(self):
        """Called by MainWindow on app exit: release the session as ending a chat does. The
        instrument is the main window's to stop."""
        self._release_session()
        self.chat_window.hide()
