"""One real SSH PTY per terminal pane; bounded queues keep the GUI responsive."""

import queue
import socket
import threading

from PySide6.QtCore import QThread, Signal

from .robot import RobotClient


class TerminalWorker(QThread):
    connected = Signal()
    error = Signal(object)

    def __init__(self, config, columns=80, lines=24, parent=None):
        super().__init__(parent)
        self.config = config
        self.stop_event = threading.Event()
        self.incoming = queue.Queue(maxsize=16)
        self.outgoing = queue.Queue(maxsize=128)
        self.size = (columns, lines)
        self.lock = threading.Lock()
        self.connection = None

    def send_input(self, data):
        if not data:
            return
        try:
            self.outgoing.put_nowait(data)
        except queue.Full:
            self.error.emit(RuntimeError("终端输入缓冲区已满，请等待远端响应后重试"))

    def resize(self, columns, lines):
        with self.lock:
            self.size = (max(2, columns), max(2, lines))

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.connection is not None:
                self.connection.interrupt()

    def run(self):
        client = None
        channel = None
        try:
            client = RobotClient(self.config)
            with self.lock:
                self.connection = client
            if self.stop_event.is_set():
                return
            client.connect()
            if self.stop_event.is_set():
                return
            with self.lock:
                columns, lines = self.size
            channel = client.client.invoke_shell(term="xterm-256color", width=columns, height=lines)
            channel.settimeout(0.0)
            last_size = (columns, lines)
            pending = b""
            self.connected.emit()
            while not self.stop_event.is_set():
                with self.lock:
                    size = self.size
                if size != last_size:
                    channel.resize_pty(width=size[0], height=size[1])
                    last_size = size
                if not pending:
                    try:
                        pending = self.outgoing.get_nowait()
                    except queue.Empty:
                        pass
                if pending and channel.send_ready():
                    try:
                        sent = channel.send(pending[:32768])
                        if sent == 0:
                            raise EOFError("终端连接已关闭")
                        pending = pending[sent:]
                    except socket.timeout:
                        pass
                if channel.recv_ready() and not self.incoming.full():
                    data = channel.recv(32768)
                    if data:
                        self.incoming.put_nowait(data)
                if channel.exit_status_ready() and not channel.recv_ready():
                    return
                if not client.client.get_transport().is_active():
                    raise RuntimeError("SSH 终端连接已断开，请检查网络后重新连接")
                self.stop_event.wait(0.01)
        except Exception as error:
            if not self.stop_event.is_set():
                self.error.emit(error)
        finally:
            if channel is not None:
                channel.close()
            with self.lock:
                self.connection = None
            if client is not None:
                client.close()
