from datetime import datetime
from html import escape
from importlib.resources import files
from pathlib import Path
import time

from PySide6.QtCore import Qt, QSignalBlocker, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import RobotConfig, SERVICES, state_dir
from .nas import NasClient, NasEntry, NasPathError, SHARE_PATH
from .packages import DebPackage
from .robot import HostKeyUnknown, RobotClient, friendly_error, trust_host
from .storage import History
from .service_logs import ServiceLogDialog
from .shortcuts import WEB_SHORTCUTS, browser_environment, robot_web_url
from .terminal import TerminalWindow
from .widgets import PasswordEdit


def size_text(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024


class Worker(QThread):
    result = Signal(object)
    error = Signal(object)
    message = Signal(str)
    progress = Signal(int)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function(self.message.emit, self.progress.emit))
        except Exception as exc:
            self.error.emit(exc)


class DropZone(QPushButton):
    selected = Signal(str)

    def __init__(self):
        super().__init__("拖入 .deb 或点击选择文件")
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)

    def valid_path(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.is_file() and path.suffix.lower() == ".deb":
                return str(path)
        return None

    def dragEnterEvent(self, event):
        if self.isEnabled() and self.valid_path(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = self.valid_path(event)
        if self.isEnabled() and path:
            self.selected.emit(path)
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, auto_connect_nas=True):
        super().__init__()
        self.setWindowTitle("机器人 Web 部署工具")
        self.resize(1180, 800)
        self.setMinimumSize(920, 720)
        self.nas = NasClient()
        self.package = None
        self.selected_nas = None
        self.selected_path = None
        self.pending_deploy_config = None
        self.worker = None
        self.busy = False
        self.history = History()
        self.retry_action = None
        self.log_parts = []
        self.service_log_windows = []
        self.terminal_windows = []
        self._build()
        self.refresh_history()
        if auto_connect_nas:
            QTimer.singleShot(300, self.refresh_nas)

    def card(self):
        widget = QFrame()
        widget.setObjectName("card")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        return widget, layout

    def _build(self):
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("机器人 Web 部署")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.terminal_button = QPushButton("远程终端")
        self.terminal_button.setProperty("variant", "accent")
        self.terminal_button.clicked.connect(self.open_terminal)
        header.addWidget(self.terminal_button)
        self.web_buttons = {}
        for name, (port, path) in WEB_SHORTCUTS.items():
            button = QPushButton(name)
            button.setToolTip(f"使用当前机器人 IP，通过默认浏览器打开 :{port}{path}")
            button.clicked.connect(lambda checked=False, shortcut=name: self.open_robot_page(shortcut))
            self.web_buttons[name] = button
            header.addWidget(button)
        layout.addLayout(header)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left, form_layout = self.card()
        left.setMaximumWidth(345)
        form_layout.setSpacing(7)
        section = QLabel("连接机器人")
        section.setObjectName("section")
        form_layout.addWidget(section)
        form = QFormLayout()
        form.setSpacing(4)
        self.host = QLineEdit("192.168.121.1")
        self.username = QLineEdit("langyi")
        self.password = PasswordEdit("langyi")
        self.generation = QComboBox()
        self.generation.addItems(SERVICES)
        self.generation.currentTextChanged.connect(self.update_service)
        for label, edit in (
            ("机器人 IP", self.host),
            ("用户名", self.username),
            ("密码", self.password),
            ("系统代次", self.generation),
        ):
            form.addRow(label, edit)
        form_layout.addLayout(form)
        self.service_label = QLabel()
        self.service_label.setObjectName("service")
        self.service_label.setWordWrap(True)
        form_layout.addWidget(self.service_label)
        self.update_service()
        self.advanced_toggle = QCheckBox("高级连接选项")
        form_layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced_form = QFormLayout(self.advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setSpacing(4)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.sudo_password = PasswordEdit()
        self.sudo_password.setPlaceholderText("留空则使用登录密码")
        advanced_form.addRow("SSH 端口", self.port)
        advanced_form.addRow("sudo 密码", self.sudo_password)
        self.advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        form_layout.addWidget(self.advanced)
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self.test_connection)
        connection_actions = QHBoxLayout()
        connection_actions.addWidget(self.test_button)
        self.service_logs_button = QPushButton("查看服务日志")
        self.service_logs_button.clicked.connect(self.open_service_logs)
        connection_actions.addWidget(self.service_logs_button)
        form_layout.addLayout(connection_actions)
        self.robot_status = QLabel("等待连接机器人")
        self.robot_status.setObjectName("muted")
        self.robot_status.setWordWrap(True)
        form_layout.addWidget(self.robot_status)
        form_layout.addStretch()
        self.deploy_button = QPushButton("一键部署")
        self.deploy_button.setProperty("variant", "primary")
        self.deploy_button.setToolTip("安装过程中请勿关闭机器人电源。")
        self.deploy_button.setEnabled(False)
        self.deploy_button.clicked.connect(self.deploy)
        form_layout.addWidget(self.deploy_button)
        split.addWidget(left)
        right, right_layout = self.card()
        title_row = QHBoxLayout()
        label = QLabel("NAS 安装包")
        label.setObjectName("section")
        title_row.addWidget(label)
        title_row.addStretch()
        self.nas_status = QLabel("NAS 等待连接")
        self.nas_status.setObjectName("muted")
        title_row.addWidget(self.nas_status)
        self.refresh_button = QPushButton("刷新目录")
        self.refresh_button.clicked.connect(self.refresh_nas)
        title_row.addWidget(self.refresh_button)
        right_layout.addLayout(title_row)
        path_row = QHBoxLayout()
        self.nas_path = QLineEdit()
        self.nas_path.setPlaceholderText("/web_deb/…")
        self.nas_path.setAccessibleName("NAS 路径")
        self.nas_path.setToolTip("输入 /web_deb 下的目录或 DEB 文件路径")
        self.nas_path.setClearButtonEnabled(True)
        self.nas_path.returnPressed.connect(self.locate_nas_path)
        path_row.addWidget(self.nas_path, 1)
        self.locate_button = QPushButton("跳转")
        self.locate_button.clicked.connect(self.locate_nas_path)
        path_row.addWidget(self.locate_button)
        right_layout.addLayout(path_row)
        self.tree = QTreeWidget()
        self.tree.setObjectName("nasTree")
        self.tree.setHeaderLabels(["名称", "大小", "修改时间"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMouseTracking(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 82)
        self.tree.setColumnWidth(2, 140)
        self.tree.itemExpanded.connect(self.expand_directory)
        self.tree.itemDoubleClicked.connect(self.open_entry)
        self.tree.setMinimumHeight(160)
        right_layout.addWidget(self.tree, 1)
        self.drop = DropZone()
        self.drop.selected.connect(self.select_local)
        self.drop.clicked.connect(self.browse_local)
        right_layout.addWidget(self.drop)
        selection = QFrame()
        selection.setObjectName("packageSelection")
        selection_row = QHBoxLayout(selection)
        selection_row.setContentsMargins(10, 5, 5, 5)
        self.package_label = QLabel()
        self.package_label.setWordWrap(True)
        self.package_label.setTextFormat(Qt.PlainText)
        self.package_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        selection_row.addWidget(self.package_label, 1)
        self.download_button = QPushButton("下载")
        self.download_button.setProperty("variant", "accent")
        self.download_button.clicked.connect(self.download_selected)
        selection_row.addWidget(self.download_button)
        self.cancel_package_button = QPushButton("取消选择")
        self.cancel_package_button.clicked.connect(self.clear_package_selection)
        selection_row.addWidget(self.cancel_package_button)
        right_layout.addWidget(selection)
        self.tree.currentItemChanged.connect(self.select_nas_item)
        self.update_package_selection()
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([304, 830])
        layout.addWidget(split, 3)
        bottom, bottom_layout = self.card()
        bottom.setMinimumHeight(158)
        status_row = QHBoxLayout()
        self.task_status = QLabel("准备就绪")
        self.task_status.setTextFormat(Qt.PlainText)
        self.task_status.setObjectName("section")
        status_row.addWidget(self.task_status)
        status_row.addStretch()
        self.recover_button = QPushButton("恢复任务")
        self.recover_button.clicked.connect(self.recover)
        self.export_button = QPushButton("导出日志")
        self.export_button.clicked.connect(self.export_logs)
        status_row.addWidget(self.recover_button)
        status_row.addWidget(self.export_button)
        bottom_layout.addLayout(status_row)
        self.tabs = QTabWidget()
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(4)
        self.progress = QProgressBar()
        self.progress.setObjectName("logProgress")
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        self.progress.hide()
        self.progress.valueChanged.connect(self.update_log_progress)
        log_layout.addWidget(self.progress)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(6000)
        log_layout.addWidget(self.logs, 1)
        self.tabs.addTab(log_page, "部署日志")
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["时间", "机器人", "安装包", "版本", "状态", "任务编号"])
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabs.addTab(self.history_table, "部署记录")
        bottom_layout.addWidget(self.tabs, 1)
        layout.addWidget(bottom, 1)
        self.inputs = [
            self.host,
            self.username,
            self.password,
            self.generation,
            self.port,
            self.sudo_password,
            self.advanced_toggle,
            self.test_button,
            self.refresh_button,
            self.nas_path,
            self.locate_button,
            self.drop,
            self.tree,
            self.recover_button,
        ]

    def update_service(self):
        self.service_label.setText("目标服务：" + SERVICES[self.generation.currentText()])

    def open_robot_page(self, shortcut):
        try:
            url = robot_web_url(self.host.text(), shortcut)
        except ValueError as error:
            QMessageBox.warning(self, "机器人地址", str(error))
            return
        with browser_environment():
            opened = QDesktopServices.openUrl(url)
        if not opened:
            QMessageBox.warning(self, "无法打开浏览器", "请检查系统的默认浏览器设置。\n" + url.toString())

    def open_terminal(self):
        try:
            window = TerminalWindow(self.config())
        except ValueError as error:
            QMessageBox.warning(self, "连接参数", str(error))
            return
        self.terminal_windows.append(window)
        window.finished.connect(lambda _result: self.terminal_windows.remove(window))
        window.finished.connect(window.deleteLater)
        window.show()

    def open_service_logs(self):
        try:
            dialog = ServiceLogDialog(self.config())
        except ValueError as error:
            QMessageBox.warning(self, "连接参数", str(error))
            return
        self.service_log_windows.append(dialog)
        dialog.finished.connect(lambda _result: self.service_log_windows.remove(dialog))
        dialog.finished.connect(dialog.deleteLater)
        dialog.show()
        dialog.start()

    def config(self):
        config = RobotConfig(
            self.host.text().strip(),
            self.username.text().strip(),
            self.password.text(),
            self.port.value(),
            self.generation.currentText(),
            self.sudo_password.text(),
        )
        config.validate()
        return config

    def log(self, text):
        for edit in (self.password, self.sudo_password):
            if edit.text():
                text = text.replace(edit.text(), "***")
        stamped = f"[{time.strftime('%H:%M:%S')}] {text}"
        self.log_parts.append(stamped)
        self.logs.appendPlainText(stamped)
        self.logs.moveCursor(QTextCursor.End)
        with (state_dir() / "application.log").open("a", encoding="utf-8") as output:
            output.write(stamped + "\n")
        (state_dir() / "application.log").chmod(0o600)

    def set_busy(self, busy):
        self.busy = busy
        for widget in self.inputs:
            widget.setEnabled(not busy)
        self.update_package_selection()
        self.update_log_progress(self.progress.value())

    def select_nas_item(self, item, _previous=None):
        entry = item.data(0, Qt.UserRole) if item else None
        if not isinstance(entry, NasEntry) or entry.is_dir or not entry.name.lower().endswith(".deb"):
            return
        if entry != self.selected_nas:
            self.selected_nas = entry
            self.selected_path = None
            self.package = None
            self.update_package_selection()
            if not self.busy:
                self.task_status.setText("等待下载安装包")

    def clear_package_selection(self):
        self.selected_nas = None
        self.selected_path = None
        self.package = None
        with QSignalBlocker(self.tree):
            self.tree.setCurrentItem(None)
            self.tree.clearSelection()
        self.update_package_selection()
        if not self.busy:
            self.task_status.setText("准备就绪")

    def update_package_selection(self):
        if self.selected_nas is not None:
            name = self.selected_nas.name
        elif self.selected_path is not None:
            name = self.selected_path.name
        elif self.package is not None:
            name = self.package.path.name
        else:
            name = None
        self.package_label.setText(f"已选择 {name}" if name else "尚未选择安装包")
        details = escape(name) if name else ""
        if self.package is not None:
            package = self.package
            metadata = escape(
                f"{package.name} · {package.version} · {package.architecture} · {size_text(package.size)}"
            )
            details += f"<br>{metadata}<br>SHA-256：{package.digest}"
        self.package_label.setToolTip(f"<qt>{details}</qt>" if details else "")
        is_nas_package = self.selected_nas is not None
        self.download_button.setText("下载" if self.package is None else "重新下载")
        self.download_button.setVisible(is_nas_package)
        self.download_button.setEnabled(not self.busy and is_nas_package)
        self.cancel_package_button.setEnabled(not self.busy and name is not None)
        self.deploy_button.setEnabled(not self.busy and (self.package is not None or is_nas_package))

    def update_log_progress(self, value):
        self.progress.setVisible(self.busy and 0 < value < 100)

    def start_job(self, label, function, done, retry=None):
        if self.busy:
            return
        self.set_busy(True)
        self.task_status.setText(label)
        self.progress.setValue(0)
        self.retry_action = retry
        self.job_label = label
        worker = Worker(function, self)
        self.worker = worker
        worker.message.connect(self.log)
        worker.progress.connect(self.progress.setValue)
        worker.result.connect(done)
        worker.error.connect(self.job_error)
        worker.finished.connect(self.job_finished)
        worker.start()

    def job_finished(self):
        worker = self.worker
        self.worker = None
        self.set_busy(False)
        self.refresh_history()
        worker.deleteLater()
        config = self.pending_deploy_config
        self.pending_deploy_config = None
        if config is not None:
            self.deploy_package(config, self.package)

    def job_error(self, error):
        if isinstance(error, HostKeyUnknown):
            answer = QMessageBox.question(
                self,
                "首次连接机器人",
                "请核实机器人的 SSH 身份：\n\n" + str(error) + "\n\n是否信任并保存此指纹？",
            )
            if answer == QMessageBox.Yes:
                trust_host(error)
                action = self.retry_action
                if action:
                    # Wait for finished before retrying the operation.
                    QTimer.singleShot(100, lambda: self.retry_when_idle(action))
                return
            self.task_status.setText("已取消连接")
            return
        message = friendly_error(error)
        self.log(message)
        self.task_status.setText("操作未完成，请查看日志")
        if isinstance(error, NasPathError):
            self.task_status.setText("路径定位失败")
            QMessageBox.warning(self, "NAS 路径", message)
            return
        if "NAS" in self.job_label:
            self.nas_status.setText("NAS 暂不可用")
            self.task_status.setText("NAS 暂不可用")
            return
        if self.selected_path is not None and self.package is None:
            self.clear_package_selection()
        QMessageBox.warning(self, "操作未完成", message)

    def retry_when_idle(self, action):
        if self.busy:
            QTimer.singleShot(100, lambda: self.retry_when_idle(action))
        else:
            action()

    def refresh_nas(self):
        if self.busy:
            return

        def work(log, progress):
            self.nas.connect()
            return self.nas.list_dir(self.nas.root)

        def done(entries):
            root = self.reset_nas_tree(NasEntry(self.nas.root, SHARE_PATH.name, True))
            self.fill_entries(root, entries)
            root.setExpanded(True)
            self.nas_status.setText("NAS 已连接")
            self.task_status.setText("NAS 目录已更新")

        self.nas_status.setText("NAS 连接中…")
        self.start_job("读取 NAS 目录…", work, done)

    def locate_nas_path(self):
        path = self.nas_path.text()
        self.start_job("定位 NAS 路径…", lambda *_: self.nas.locate(path), self.show_nas_location)

    def show_nas_location(self, result):
        root, ancestors, contents = result
        with QSignalBlocker(self.tree):
            item = self.reset_nas_tree(root)
            for entries, child in ancestors:
                children = self.fill_entries(item, entries)
                item.setExpanded(True)
                item = children[child.path]
            if contents is not None:
                self.fill_entries(item, contents)
                item.setExpanded(True)
            self.tree.setCurrentItem(item)
        self.select_nas_item(item)
        self.tree.scrollToItem(item, QTreeWidget.PositionAtTop)
        self.nas_status.setText("NAS 已连接")
        self.task_status.setText("目录已定位" if contents is not None else "安装包已定位")

    def reset_nas_tree(self, entry):
        self.tree.clear()
        item = QTreeWidgetItem(self.tree, [str(SHARE_PATH)])
        item.setData(0, Qt.UserRole, entry)
        return item

    def fill_entries(self, parent, entries):
        parent.takeChildren()
        children = {}
        for entry in entries:
            modified = datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M") if entry.mtime else ""
            item = QTreeWidgetItem(
                parent, [entry.name, "—" if entry.is_dir else size_text(entry.size), modified]
            )
            item.setData(0, Qt.UserRole, entry)
            children[entry.path] = item
            if entry.is_dir:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            elif not entry.name.lower().endswith(".deb"):
                item.setForeground(0, Qt.gray)
        parent.setData(0, Qt.UserRole + 1, True)
        return children

    def expand_directory(self, item):
        entry = item.data(0, Qt.UserRole)
        if not isinstance(entry, NasEntry) or not entry.is_dir or item.data(0, Qt.UserRole + 1):
            return

        def done(entries):
            self.fill_entries(item, entries)
            self.task_status.setText("目录已加载")

        self.start_job("读取目录…", lambda *_: self.nas.list_dir(entry.path), done)

    def open_entry(self, item, _column):
        entry = item.data(0, Qt.UserRole)
        if isinstance(entry, NasEntry) and not entry.is_dir and entry.name.lower().endswith(".deb"):
            self.select_nas_item(item)
            self.download_selected()

    def download_selected(self):
        entry = self.selected_nas
        if entry is not None:
            self.download_package(entry, self.package_ready)

    def download_package(self, entry, done):
        def work(log, progress):
            log("从 NAS 下载：" + entry.name)
            path = self.nas.download(entry, lambda done, total: progress(int(done * 100 / max(total, 1))))
            return DebPackage.inspect(path)

        self.start_job("下载并校验 DEB…", work, done)

    def browse_local(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 DEB 安装包", str(Path.home()), "Debian 安装包 (*.deb)"
        )
        if path:
            self.select_local(path)

    def select_local(self, path):
        if self.busy:
            return
        self.clear_package_selection()
        self.selected_path = Path(path)
        self.update_package_selection()
        self.start_job("解析安装包…", lambda *_: DebPackage.inspect(path), self.package_ready)

    def package_ready(self, package):
        self.package = package
        self.update_package_selection()
        self.task_status.setText("安装包校验完成，可以部署")
        self.progress.setValue(100)

    def robot_job(self, config, action):
        client = RobotClient(config)
        try:
            client.connect()
            return action(client)
        finally:
            client.close()

    def test_connection(self):
        try:
            config = self.config()
        except ValueError as error:
            QMessageBox.warning(self, "连接参数", str(error))
            return

        def done(info):
            self.robot_status.setText(
                f"已连接 · {info.ubuntu}\n{info.architecture} · 剩余 {size_text(info.available_bytes)}\n"
                f"服务：{info.service_state}"
            )
            self.task_status.setText("机器人连接正常，sudo 权限检查通过")
            self.progress.setValue(100)

        self.start_job(
            "连接机器人…",
            lambda *_: self.robot_job(config, lambda c: c.inspect()),
            done,
            retry=self.test_connection,
        )

    def deploy(self):
        if self.busy or (self.package is None and self.selected_nas is None):
            return
        try:
            config = self.config()
        except ValueError as error:
            QMessageBox.warning(self, "连接参数", str(error))
            return
        if self.package is not None:
            self.deploy_package(config, self.package)
        else:
            def ready(package):
                self.package_ready(package)
                self.pending_deploy_config = config

            self.download_package(self.selected_nas, ready)

    def deploy_package(self, config, package):
        self.log(f"部署目标：{config.host} / {config.generation} / {config.service}")
        self.log(f"安装包：{package.name} {package.version} ({package.architecture})")

        def done(task_id):
            self.task_status.setText("部署成功 · 服务已设置开机自启并运行正常")
            self.log("任务完成：" + task_id)
            QMessageBox.information(
                self,
                "部署成功",
                f"{package.name} {package.version}\n{config.service} 已自启并持续运行 10 秒。",
            )

        self.start_job(
            "部署进行中…",
            lambda log, progress: self.robot_job(config, lambda c: c.deploy(package, log, progress)),
            done,
            retry=self.deploy,
        )

    def refresh_history(self):
        records = self.history.recent()
        self.history_table.setRowCount(len(records))
        for row, record in enumerate(records):
            for col, key in enumerate(("created", "host", "package", "version", "state", "id")):
                text = record[key]
                if key == "created":
                    text = datetime.fromisoformat(text).astimezone().strftime("%m-%d %H:%M")
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, record)
                item.setToolTip(record["detail"] or text)
                self.history_table.setItem(row, col, item)

    def recover(self):
        records = self.history.recent()
        row = self.history_table.currentRow()
        if row >= 0:
            record = self.history_table.item(row, 0).data(Qt.UserRole)
        else:
            record = next((r for r in records if r["state"] in ("准备中", "执行中", "待查询")), None)
        if record is None:
            QMessageBox.information(self, "恢复任务", "没有待查询任务。也可以在部署记录中选择一条任务。")
            return
        try:
            current = self.config()
            generation = next(name for name, service in SERVICES.items() if service == record["service"])
            config = RobotConfig(
                record["host"],
                record["username"],
                current.password,
                record["port"],
                generation,
                current.sudo_password,
            )
        except (ValueError, StopIteration) as error:
            QMessageBox.warning(self, "无法恢复任务", str(error))
            return
        self.log("恢复查询任务：" + record["id"] + " / " + config.host)

        def done(_):
            self.task_status.setText("任务已完成 · 服务运行正常")

        self.start_job(
            "恢复任务查询…",
            lambda log, progress: self.robot_job(config, lambda c: c.monitor(record["id"], log, progress)),
            done,
            retry=self.recover,
        )

    def export_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "robot-deploy.log", "日志 (*.log)")
        if path:
            source = state_dir() / "application.log"
            Path(path).write_text(
                source.read_text(encoding="utf-8") if source.exists() else "暂无日志\n", encoding="utf-8"
            )

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "操作正在进行", "请等待本次操作结束。远程安装开始后不能强制取消。")
            event.ignore()
        else:
            active_logs = [dialog for dialog in self.service_log_windows if dialog.worker is not None]
            if active_logs or self.terminal_windows:
                for dialog in [*active_logs, *self.terminal_windows[:]]:
                    dialog.close()
                QTimer.singleShot(100, self.close)
                event.ignore()
            else:
                event.accept()


def create_application():
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("RobotWebInstaller")
    app.setOrganizationName("Langyi")
    font = files("robot_installer").joinpath("assets/NotoSansCJK-Regular.ttc")
    QFontDatabase.addApplicationFont(str(font))
    mono = files("robot_installer").joinpath("assets/DejaVuSansMono.ttf")
    QFontDatabase.addApplicationFont(str(mono))
    app.setFont(QFont("Noto Sans CJK SC", 10))
    app.setStyle("Fusion")
    palette = app.palette()
    for role, color in {
        QPalette.Window: "#0F172A",
        QPalette.WindowText: "#E2E8F0",
        QPalette.Base: "#111C2E",
        QPalette.AlternateBase: "#152238",
        QPalette.Text: "#E2E8F0",
        QPalette.Button: "#19283E",
        QPalette.ButtonText: "#E2E8F0",
        QPalette.Highlight: "#24467D",
        QPalette.HighlightedText: "#F8FAFC",
        QPalette.PlaceholderText: "#8293AD",
    }.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.Disabled, QPalette.Base, QColor("#172031"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#8293AD"))
    app.setPalette(palette)
    app.setStyleSheet(files("robot_installer").joinpath("assets", "style.qss").read_text(encoding="utf-8"))
    return app


def launch():
    app = create_application()
    window = MainWindow()
    window.show()
    return app.exec()
