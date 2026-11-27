"""Split SSH terminal window with independent sessions and explicit lifetimes."""

import queue

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
)

from .config import RobotConfig
from .robot import HostKeyUnknown, friendly_error, trust_host
from .terminal_view import TerminalView
from .terminal_worker import TerminalWorker
from .widgets import PasswordEdit


class TerminalPane(QFrame):
    identity_requested = Signal(object, object)

    def __init__(self, config, number, parent=None):
        super().__init__(parent)
        self.setObjectName("terminalPane")
        self.config = config
        self.number = number
        self.worker = None
        self.closing = False
        self.retry_requested = False
        self.failed = False
        self.pending_config = None
        self.connect_timer = QTimer(self)
        self.connect_timer.setSingleShot(True)
        self.connect_timer.setInterval(180)
        self.connect_timer.timeout.connect(self.connect_session)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(5)
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        self.label.setObjectName("terminalTarget")
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.label, 1)
        self.connect_button = QPushButton("连接")
        self.connect_button.clicked.connect(self.connect_session)
        self.disconnect_button = QPushButton("断开")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect_session)
        self.settings_button = QPushButton("设置")
        self.settings_button.clicked.connect(self.edit_connection)
        self.close_button = QPushButton("关闭")
        self.close_button.setProperty("variant", "close")
        self.close_button.setToolTip("关闭此分屏及其 SSH 会话")
        for button in (self.connect_button, self.disconnect_button, self.settings_button, self.close_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.view = TerminalView()
        layout.addWidget(self.view, 1)
        self.view.input_bytes.connect(self.send_input)
        self.view.terminal_resized.connect(self.resize_terminal)
        self.status = QLabel("未连接")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setObjectName("terminalStatus")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.status)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(30)
        self.poll_timer.timeout.connect(self.poll_output)
        self.poll_timer.start()
        self.update_label()

    def update_label(self):
        self.label.setText(f"{self.number} · {self.config.host}")
        self.label.setToolTip(f"{self.config.username}@{self.config.host}:{self.config.port}")

    def connect_session(self):
        self.connect_timer.stop()
        if self.closing:
            return
        if self.worker is not None:
            if self.worker.stop_event.is_set():
                self.retry_requested = True
            return
        self.failed = False
        self.retry_requested = False
        self.view.reset_screen()
        self.view.update_dimensions()
        self.status.setText("正在连接…")
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        worker = TerminalWorker(self.config, self.view.buffer.columns, self.view.buffer.lines, self)
        self.worker = worker
        worker.connected.connect(self.on_connected)
        worker.error.connect(self.on_error)
        worker.finished.connect(self.on_finished)
        worker.start()

    def on_connected(self):
        if self.worker is not None and not self.worker.stop_event.is_set():
            self.view.enabled_input = True
            self.status.setText(f"已连接 · {self.config.username}@{self.config.host}:{self.config.port}")
            self.view.setFocus()

    def send_input(self, data):
        if self.worker is not None and self.view.enabled_input:
            self.worker.send_input(data)

    def resize_terminal(self, columns, lines):
        if self.worker is not None:
            self.worker.resize(columns, lines)

    def poll_output(self):
        if self.worker is None:
            return
        blocks = []
        for _ in range(4):
            try:
                blocks.append(self.worker.incoming.get_nowait())
            except queue.Empty:
                break
        if blocks:
            self.view.feed(b"".join(blocks))

    def disconnect_session(self):
        self.connect_timer.stop()
        self.retry_requested = False
        self.view.enabled_input = False
        if self.worker is not None:
            self.status.setText("正在断开…")
            self.disconnect_button.setEnabled(False)
            self.worker.stop()

    def on_error(self, error):
        if self.closing:
            return
        self.failed = True
        if isinstance(error, HostKeyUnknown):
            self.status.setText("等待核实 SSH 主机身份")
            self.identity_requested.emit(self, error)
        else:
            message = friendly_error(error)
            for secret in (self.config.password, self.config.sudo_password):
                if secret:
                    message = message.replace(secret, "***")
            self.status.setText(message)
            self.status.setToolTip(message)

    def retry_when_ready(self):
        if self.closing:
            return
        if self.worker is None:
            self.connect_session()
        else:
            self.retry_requested = True

    def on_finished(self):
        worker = self.worker
        while not worker.incoming.empty():
            self.poll_output()
        self.worker = None
        self.view.enabled_input = False
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        if not self.failed:
            self.status.setText("会话已断开")
        worker.deleteLater()
        if self.pending_config is not None:
            self.config = self.pending_config
            self.pending_config = None
            self.update_label()
        if self.retry_requested and not self.closing:
            QTimer.singleShot(0, self.connect_session)

    def edit_connection(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"终端 {self.number} 连接设置")
        dialog.setMinimumWidth(340)
        layout = QFormLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        host = QLineEdit(self.config.host)
        username = QLineEdit(self.config.username)
        password = PasswordEdit(self.config.password)
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(self.config.port)
        for label, widget in (
            ("机器人 IP", host),
            ("SSH 端口", port),
            ("用户名", username),
            ("密码", password),
        ):
            layout.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        config = RobotConfig(
            host.text().strip(),
            username.text().strip(),
            password.text(),
            port.value(),
            self.config.generation,
        )
        try:
            config.validate()
        except ValueError as error:
            QMessageBox.warning(self, "连接参数", str(error))
            return
        if self.worker is not None:
            self.pending_config = config
            self.disconnect_session()
            self.retry_requested = True
        else:
            self.config = config
            self.update_label()
            self.connect_session()


class TerminalWindow(QDialog):
    def __init__(self, config, parent=None, autoconnect=True):
        super().__init__(parent)
        self.setWindowTitle("SSH 远程终端")
        self.resize(1200, 800)
        self.setMinimumSize(760, 500)
        self.closing = False
        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.setInterval(50)
        self.close_timer.timeout.connect(self.close)
        self.config = config
        self.panes = []
        self.retired_panes = set()
        self.next_number = 1
        self.identity_queue = []
        self.identity_active = False
        self.approved_keys = set()
        self.autoconnect = autoconnect
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)
        row = QHBoxLayout()
        title = QLabel("SSH 远程终端")
        title.setObjectName("section")
        row.addWidget(title)
        row.addStretch()
        row.addWidget(QLabel("字号"))
        self.font_size = QSpinBox()
        self.font_size.setRange(9, 24)
        self.font_size.setValue(12)
        self.font_size.valueChanged.connect(self.change_font_size)
        row.addWidget(self.font_size)
        self.add_split_button = QPushButton("添加分屏")
        self.add_split_button.setProperty("variant", "accent")
        self.add_split_button.clicked.connect(self.add_pane)
        row.addWidget(self.add_split_button)
        layout.addLayout(row)
        self.container = QVBoxLayout()
        layout.addLayout(self.container, 1)
        self.root_splitter = None
        self.add_pane()

    @property
    def count(self):
        return len(self.panes)

    def add_pane(self):
        if self.count >= 4 or self.closing:
            return
        pane = TerminalPane(self.config, self.next_number, self)
        self.next_number += 1
        pane.view.set_font_size(self.font_size.value())
        pane.identity_requested.connect(self.request_identity)
        pane.close_button.clicked.connect(lambda: self.remove_pane(pane))
        self.panes.append(pane)
        self.layout_panes()
        if self.autoconnect:
            # Let splitter geometry and the viewport's resize settle before requesting a PTY.
            pane.connect_timer.start()

    def remove_pane(self, pane):
        if self.count <= 1 or self.closing:
            return
        index = self.panes.index(pane)
        self.panes.remove(pane)
        self.identity_queue = [(item, error) for item, error in self.identity_queue if item is not pane]
        pane.closing = True
        pane.setParent(self)
        pane.hide()
        if pane.worker is None:
            pane.disconnect_session()
            pane.deleteLater()
        else:
            self.retired_panes.add(pane)
            pane.worker.finished.connect(self.reap_closed_panes)
            pane.disconnect_session()
        self.layout_panes()
        self.panes[min(index, self.count - 1)].view.setFocus()

    def reap_closed_panes(self):
        for pane in self.retired_panes.copy():
            if pane.worker is None:
                self.retired_panes.remove(pane)
                pane.deleteLater()

    def layout_panes(self):
        count = self.count
        for pane in self.panes:
            pane.setParent(self)
        if self.root_splitter is not None:
            self.container.removeWidget(self.root_splitter)
            self.root_splitter.deleteLater()
        root = QSplitter(Qt.Horizontal if count in (1, 2, 3) else Qt.Vertical)
        root.setChildrenCollapsible(False)
        if count in (1, 2):
            for pane in self.panes:
                root.addWidget(pane)
        elif count == 3:
            root.addWidget(self.panes[0])
            right = QSplitter(Qt.Vertical)
            right.setChildrenCollapsible(False)
            right.addWidget(self.panes[1])
            right.addWidget(self.panes[2])
            root.addWidget(right)
            root.setSizes([600, 600])
        else:
            for start in (0, 2):
                split = QSplitter(Qt.Horizontal)
                split.setChildrenCollapsible(False)
                split.addWidget(self.panes[start])
                split.addWidget(self.panes[start + 1])
                split.setSizes([600, 600])
                root.addWidget(split)
            root.setSizes([400, 400])
        self.root_splitter = root
        self.container.addWidget(root)
        for pane in self.panes:
            pane.close_button.setVisible(count > 1)
            pane.show()
        self.add_split_button.setEnabled(count < 4)

    def stop_all(self):
        for pane in self.panes:
            pane.disconnect_session()

    def has_running_sessions(self):
        return bool(self.retired_panes) or any(pane.worker is not None for pane in self.panes)

    def change_font_size(self, size):
        for pane in self.panes:
            pane.view.set_font_size(size)

    def request_identity(self, pane, error):
        token = (error.host, error.key.asbytes())
        if self.closing or pane.closing:
            return
        if token in self.approved_keys:
            pane.retry_when_ready()
            return
        self.identity_queue.append((pane, error))
        if not self.identity_active:
            QTimer.singleShot(0, self.process_identities)

    def process_identities(self):
        if self.identity_active or self.closing or not self.identity_queue:
            return
        self.identity_active = True
        pane, error = self.identity_queue.pop(0)
        token = (error.host, error.key.asbytes())
        answer = QMessageBox.question(
            self,
            "首次连接机器人",
            "请核实机器人的 SSH 身份：\n\n" + str(error) + "\n\n是否信任并保存此指纹？",
        )
        matches = [pane]
        remaining = []
        for other, item in self.identity_queue:
            if (item.host, item.key.asbytes()) == token:
                matches.append(other)
            else:
                remaining.append((other, item))
        self.identity_queue = remaining
        if answer == QMessageBox.Yes and not self.closing:
            trust_host(error)
            self.approved_keys.add(token)
            for item in matches:
                if not item.closing:
                    item.retry_when_ready()
        else:
            for item in matches:
                if not item.closing:
                    item.status.setText("未信任 SSH 身份，连接已取消")
        self.identity_active = False
        if self.identity_queue:
            QTimer.singleShot(0, self.process_identities)

    def closeEvent(self, event):
        self.closing = True
        self.identity_queue.clear()
        for pane in self.panes:
            pane.closing = True
        self.stop_all()
        if self.has_running_sessions():
            self.close_timer.start()
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        # Esc is a remote terminal key, never an implicit close/disconnect shortcut.
        if self.closing and not self.has_running_sessions():
            super().reject()
