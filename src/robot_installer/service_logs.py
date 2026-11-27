"""Independent read-only journal viewer; deployment can continue while logs are open."""

from datetime import datetime
from pathlib import Path
import threading

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .robot import HostKeyUnknown, RobotClient, friendly_error, trust_host


class ServiceLogWorker(QThread):
    message = Signal(str)
    connected = Signal()
    error = Signal(object)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.stop_event = threading.Event()
        self.connection = None
        self.lock = threading.Lock()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.connection is not None:
                self.connection.interrupt()

    def run(self):
        client = None
        try:
            client = RobotClient(self.config)
            with self.lock:
                self.connection = client
            if not self.stop_event.is_set():
                client.connect()
            if not self.stop_event.is_set():
                client.follow_service_logs(self.stop_event, self.message.emit, self.connected.emit)
        except Exception as error:
            if not self.stop_event.is_set():
                self.error.emit(error)
        finally:
            with self.lock:
                self.connection = None
            if client is not None:
                client.close()


class ServiceLogDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config  # Immutable snapshot; changing the main form never mixes targets.
        self.worker = None
        self.closing = False
        self.retry = False
        self.failed = False
        self.setWindowTitle(f"实时服务日志 · {config.generation} · {config.host}")
        self.resize(1000, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel(f"{config.generation}  ·  {config.service}")
        title.setObjectName("section")
        layout.addWidget(title)
        self.target = QLabel(f"机器人：{config.host}:{config.port}  ·  用户：{config.username}")
        self.target.setTextFormat(Qt.PlainText)
        layout.addWidget(self.target)
        hint = QLabel("先显示最近 200 行，再实时跟踪；窗口保留最近 5000 行。仅显示 journal 收集的服务输出。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.start_button = QPushButton("开始跟踪")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("停止跟踪")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        self.clear_button = QPushButton("清空窗口")
        self.clear_button.clicked.connect(self.clear)
        self.export_button = QPushButton("导出服务日志")
        self.export_button.clicked.connect(self.export)
        self.auto_scroll = QCheckBox("自动滚动")
        self.auto_scroll.setChecked(True)
        for widget in (
            self.start_button,
            self.stop_button,
            self.clear_button,
            self.export_button,
            self.auto_scroll,
        ):
            row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setMaximumBlockCount(5000)
        layout.addWidget(self.output, 1)
        self.status = QLabel("等待连接")
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)

    def start(self):
        if self.worker is not None or self.closing:
            return
        self.failed = False
        self.retry = False
        self.status.setText("正在连接机器人…")
        self.append(
            f"── {datetime.now().astimezone().isoformat(timespec='seconds')} · "
            f"{self.config.host} · {self.config.service} ──"
        )
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        worker = ServiceLogWorker(self.config, self)
        self.worker = worker
        worker.message.connect(self.append)
        worker.connected.connect(self.on_connected)
        worker.error.connect(self.on_error)
        worker.finished.connect(self.on_finished)
        worker.start()

    def append(self, text):
        for secret in (self.config.password, self.config.sudo_password):
            if secret:
                text = text.replace(secret, "***")
        scroll = self.output.verticalScrollBar()
        position = scroll.value()
        self.output.appendPlainText(text)
        if self.auto_scroll.isChecked():
            self.output.moveCursor(QTextCursor.End)
        else:
            scroll.setValue(position)

    def on_connected(self):
        if self.worker and not self.worker.stop_event.is_set():
            self.status.setText(f"已连接 · 正在跟踪 {self.config.service}")

    def stop(self):
        self.retry = False
        if self.worker:
            self.status.setText("正在断开日志连接…")
            self.stop_button.setEnabled(False)
            self.worker.stop()

    def on_error(self, error):
        if self.closing:
            return
        self.failed = True
        if isinstance(error, HostKeyUnknown):
            answer = QMessageBox.question(
                self,
                "首次连接机器人",
                "请核实机器人的 SSH 身份：\n\n" + str(error) + "\n\n是否信任并保存此指纹？",
            )
            if answer == QMessageBox.Yes:
                trust_host(error)
                self.retry = True
                # The nested dialog event loop may have already delivered finished().
                if self.worker is None:
                    QTimer.singleShot(0, self.start)
            else:
                self.status.setText("未信任机器人身份，连接已取消")
            return
        message = friendly_error(error)
        self.append("[连接错误] " + message)
        self.status.setText("连接未完成或已断开，可重新开始跟踪")

    def on_finished(self):
        worker = self.worker
        self.worker = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if not self.failed:
            self.status.setText("已停止跟踪，机器人服务继续运行")
        worker.deleteLater()
        if self.closing:
            self.close()
        elif self.retry:
            QTimer.singleShot(0, self.start)

    def clear(self):
        self.output.clear()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出服务日志",
            f"{self.config.service}-{datetime.now():%Y%m%d-%H%M%S}.log",
            "日志 (*.log)",
        )
        if path:
            try:
                header = (
                    f"{self.config.generation} | {self.config.service} | "
                    f"{self.config.host}:{self.config.port}\n"
                )
                Path(path).write_text(header + self.output.toPlainText() + "\n", encoding="utf-8")
            except OSError as error:
                QMessageBox.warning(self, "导出失败", str(error))

    def closeEvent(self, event):
        self.closing = True
        if self.worker is not None:
            self.stop()
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        # Escape invokes reject() directly, bypassing closeEvent().
        self.closing = True
        if self.worker is not None:
            self.stop()
        else:
            super().reject()
