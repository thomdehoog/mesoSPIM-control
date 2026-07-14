"""The Remote Control tab: owns its widgets, its settings, and the MCP child process.

Follows the project's convention for optional features (mesoSPIM_Optimizer,
ProcessorChainWindow): take the MainWindow as parent and reach back through it for the
Core, the package directory and the tab bar. MainWindow keeps only a handle and a
teardown call.
"""
import secrets

from PyQt5 import QtCore, QtWidgets

TCP_PORT = 42000
MCP_PORT = 42100


class RemoteControlTab(QtWidgets.QWidget):
    """Self-contained Remote Control GUI.

    The two signals are emitted to Core through a queued connection so the server's socket
    is created on the Core's own thread, which is where it must live. MainWindow keeps only
    a handle to this object and calls shutdown() on close.
    """

    sig_start_remote_control = QtCore.pyqtSignal(str, int, str)
    sig_stop_remote_control = QtCore.pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent.TabWidget)
        self.main_window = parent  # modal parent for the dialogs; self would re-centre them
        self.core = parent.core
        self.package_directory = parent.package_directory
        self.running = False
        self.mode = 'TCP'
        self.host = '127.0.0.1'
        self.port = TCP_PORT
        self.token = 'smart_mesospim'
        self._pending_mcp = None
        self._mcp_process = None
        self.setObjectName('RemoteControlTabWidget')
        self._build_ui()
        self.sig_start_remote_control.connect(
            self.core.start_remote_control, type=QtCore.Qt.QueuedConnection)
        self.sig_stop_remote_control.connect(
            self.core.stop_remote_control, type=QtCore.Qt.QueuedConnection)
        self.core.sig_remote_control_started.connect(self.on_started)
        index = parent.TabWidget.indexOf(parent.TimelapseTabWidget)
        if index >= 0:
            parent.TabWidget.insertTab(index + 1, self, 'Remote Control')
        else:
            parent.TabWidget.addTab(self, 'Remote Control')
        self.update_mode_note()
        self.refresh()

    def _build_ui(self):
        """Build the tab exactly as MainWindow built it: same object names, labels, fonts,
        margins, spacing and defaults. This is a move, not a redesign."""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        group = QtWidgets.QGroupBox('Setup remote control', self)
        group.setObjectName('RemoteControlSetupGroupBox')
        font = group.font()
        font.setPointSize(12)
        group.setFont(font)
        form = QtWidgets.QFormLayout(group)
        form.setContentsMargins(10, 30, 10, 10)
        form.setSpacing(8)

        self.RemoteControlModeComboBox = QtWidgets.QComboBox(group)
        self.RemoteControlModeComboBox.addItems(['TCP', 'MCP'])
        self.RemoteControlModeComboBox.setCurrentText(self.mode)
        self.RemoteControlHostLineEdit = QtWidgets.QLineEdit(self.host, group)
        self.RemoteControlPortLineEdit = QtWidgets.QLineEdit(str(self.port), group)
        self.RemoteControlTokenLineEdit = QtWidgets.QLineEdit(self.token, group)
        self.RemoteControlStatusLabel = QtWidgets.QLabel(group)
        for widget in self._inputs():
            widget.setFont(font)
        self.RemoteControlStatusLabel.setFont(font)

        def form_label(text):
            label = QtWidgets.QLabel(text, group)
            label.setFont(font)
            return label

        form.addRow(form_label('Protocol'), self.RemoteControlModeComboBox)
        form.addRow(form_label('Host'), self.RemoteControlHostLineEdit)
        form.addRow(form_label('Port'), self.RemoteControlPortLineEdit)
        form.addRow(form_label('Password'), self.RemoteControlTokenLineEdit)
        form.addRow(form_label('Status'), self.RemoteControlStatusLabel)

        self.RemoteControlStartButton = QtWidgets.QPushButton('Start', group)
        self.RemoteControlStopButton = QtWidgets.QPushButton('Stop', group)
        self.RemoteControlStartButton.setFont(font)
        self.RemoteControlStopButton.setFont(font)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.RemoteControlStartButton)
        buttons.addWidget(self.RemoteControlStopButton)
        form.addRow(buttons)
        layout.addWidget(group)
        layout.addStretch(1)

        self.RemoteControlStartButton.clicked.connect(self.start)
        self.RemoteControlStopButton.clicked.connect(self.stop)
        self.RemoteControlModeComboBox.currentTextChanged.connect(self.on_mode_changed)

    def _inputs(self):
        return (self.RemoteControlModeComboBox, self.RemoteControlHostLineEdit,
                self.RemoteControlPortLineEdit, self.RemoteControlTokenLineEdit)

    def start(self):
        """Start the server the operator asked for.

        In MCP mode the bridge is fronted by an internal TCP server on an ephemeral port
        (hence port 0) with its own generated token; the operator's token guards the MCP
        endpoint itself. That internal port only becomes known when Core reports back, so
        the MCP child cannot be launched until on_started() runs.
        """
        try:
            port = int(self.RemoteControlPortLineEdit.text())
        except ValueError:
            self._warn('Port must be a number.')
            return
        host = self.RemoteControlHostLineEdit.text().strip() or '127.0.0.1'
        token = self.RemoteControlTokenLineEdit.text().strip()
        if not token:
            self._warn('Password is required.')
            return
        self.host = host
        self.port = port
        self.token = token
        self.mode = self.RemoteControlModeComboBox.currentText()
        if self.mode == 'MCP':
            internal_token = secrets.token_urlsafe(32)
            self._pending_mcp = (host, port, token, internal_token)
            self.sig_start_remote_control.emit('127.0.0.1', 0, internal_token)
        else:
            self._pending_mcp = None
            self.sig_start_remote_control.emit(host, port, token)

    def stop(self):
        self._stop_mcp()
        self.sig_stop_remote_control.emit()
        self.running = False
        self.refresh()

    def shutdown(self):
        """Tear down on application exit, doing all three things close_app() used to do.

        Killing the MCP child without also emitting the queued stop to Core would leave the
        TCP server listening; dropping either one leaves something running after exit.
        """
        if self.running:
            self._stop_mcp()
            self.sig_stop_remote_control.emit()
            self.running = False

    def on_started(self, ok, message):
        """Handle Core's queued report of a start attempt.

        On failure the server did NOT start, so the tab must not show "running" -- warn with
        the reason instead. In MCP mode the internal TCP port is parsed out of the success
        message and the child is launched; if the child fails, the TCP server that was just
        started is torn down again so nothing is left half-up.
        """
        pending, self._pending_mcp = self._pending_mcp, None
        if ok and pending is not None:
            try:
                tcp_port = int(str(message).rsplit(':', 1)[1])
            except (IndexError, ValueError):
                ok, message = False, f'Could not read internal TCP port from: {message}'
            else:
                ok, message = self._start_mcp(*pending, tcp_port)
            if not ok:
                self.sig_stop_remote_control.emit()
        self.running = ok
        if not ok:
            self._warn(f'Could not start the server: {message}')
        self.refresh()

    def _start_mcp(self, host, port, token, tcp_token, tcp_port):
        """Launch the MCP bridge child. It is parented to this tab, so tab teardown reaps it."""
        from .mesoSPIM_RemoteControl_Servers import start_mcp_server_process
        ok, message, proc = start_mcp_server_process(
            self, self.package_directory, host, port, token, tcp_token, tcp_port)
        if ok:
            self._mcp_process = proc
        return ok, message

    def _stop_mcp(self):
        from .mesoSPIM_RemoteControl_Servers import stop_mcp_server_process
        if self._mcp_process is not None:
            stop_mcp_server_process(self._mcp_process)
            self._mcp_process = None

    def on_mode_changed(self, _mode):
        self.update_mode_note()
        self.refresh()

    def update_mode_note(self):
        """Swap in the other protocol's default port, but never override a port the operator
        typed themselves."""
        text = self.RemoteControlPortLineEdit.text()
        if self.RemoteControlModeComboBox.currentText() == 'MCP':
            if text == str(TCP_PORT):
                self.RemoteControlPortLineEdit.setText(str(MCP_PORT))
        elif text == str(MCP_PORT):
            self.RemoteControlPortLineEdit.setText(str(TCP_PORT))

    def refresh(self):
        if self.running:
            self.RemoteControlStatusLabel.setText(
                f'{self.mode} running on {self.host}:{self.port}')
        else:
            self.RemoteControlStatusLabel.setText('stopped')
        self.RemoteControlStartButton.setEnabled(not self.running)
        self.RemoteControlStopButton.setEnabled(self.running)
        for widget in self._inputs():
            widget.setEnabled(not self.running)

    def _warn(self, message):
        QtWidgets.QMessageBox.warning(self.main_window, 'Remote Control', message)
