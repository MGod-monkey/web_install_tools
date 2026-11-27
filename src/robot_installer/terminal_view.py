import math

from PySide6.QtCore import Qt, QRect, QPointF, Signal, QTimer
from PySide6.QtGui import QClipboard, QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QMenu

from .terminal_screen import TerminalBuffer

COLORS = {
    "default": "#d8e7ea",
    "black": "#17252f",
    "red": "#ed6a77",
    "green": "#8bd49c",
    "brown": "#e5c07b",
    "blue": "#79b4f1",
    "magenta": "#c49cec",
    "cyan": "#73cfd2",
    "white": "#d8e7ea",
    "brightblack": "#82919d",
    "brightred": "#ff8c96",
    "brightgreen": "#a1edb3",
    "brightbrown": "#ffe1a2",
    "brightblue": "#a0cdff",
    "brightmagenta": "#dfb8ff",
    "brightcyan": "#9feef1",
    "brightwhite": "#ffffff",
}
BACKGROUND = QColor("#0B1220")


class TerminalView(QAbstractScrollArea):
    input_bytes = Signal(bytes)
    terminal_resized = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_InputMethodEnabled)
        self.viewport().setAttribute(Qt.WA_InputMethodEnabled)
        self.viewport().setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMinimumSize(180, 100)
        self.setObjectName("terminalViewport")
        self.selection = None
        self.dragging = False
        self.reported_button = None
        self.enabled_input = False
        self.cursor_on = True
        self.buffer = TerminalBuffer(reply=lambda value: self.input_bytes.emit(value.encode()))
        self.set_font_size(12)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)
        self.blink = QTimer(self)
        self.blink.setInterval(500)
        self.blink.timeout.connect(self.blink_cursor)
        self.blink.start()
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.setInterval(120)
        self.resize_timer.timeout.connect(self.update_dimensions)

    def set_font_size(self, size):
        self.terminal_font = QFont("DejaVu Sans Mono", size)
        self.terminal_font.setStyleHint(QFont.Monospace)
        self.terminal_font.setFixedPitch(True)
        metrics = QFontMetricsF(self.terminal_font)
        self.cell_width = math.ceil(metrics.horizontalAdvance("M"))
        self.cell_height = math.ceil(metrics.height()) + 2
        self.baseline = metrics.ascent() + 1
        if hasattr(self, "resize_timer"):
            self.resize_timer.start()
        self.viewport().update()

    def update_dimensions(self):
        columns = max(2, (self.viewport().width() - 8) // self.cell_width)
        lines = max(2, (self.viewport().height() - 8) // self.cell_height)
        if (columns, lines) != (self.buffer.columns, self.buffer.lines):
            self.buffer.resize(columns, lines)
            self.selection = None
            self.refresh_scroll(True)
            self.terminal_resized.emit(columns, lines)
            self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "resize_timer"):
            self.resize_timer.start()

    def reset_screen(self):
        self.buffer = TerminalBuffer(
            self.buffer.columns, self.buffer.lines, reply=lambda value: self.input_bytes.emit(value.encode())
        )
        self.selection = None
        self.refresh_scroll(True)
        self.viewport().update()

    def feed(self, data):
        bar = self.verticalScrollBar()
        follow = bar.value() == bar.maximum()
        self.buffer.feed(data)
        self.refresh_scroll(follow)
        self.viewport().update()

    def refresh_scroll(self, follow):
        bar = self.verticalScrollBar()
        maximum = len(self.buffer.normal.history.top) if self.buffer.active is self.buffer.normal else 0
        bar.setRange(0, maximum)
        bar.setPageStep(self.buffer.lines)
        if follow:
            bar.setValue(maximum)

    def blink_cursor(self):
        self.cursor_on = not self.cursor_on
        self.viewport().update()

    def selected(self, row, col):
        if not self.selection:
            return False
        start, end = sorted(self.selection)
        return start <= (row, col) < end

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), BACKGROUND)
        rows = self.buffer.rows()
        offset = self.verticalScrollBar().value()
        default = self.buffer.active.default_char
        for y, row in enumerate(rows[offset : offset + self.buffer.lines]):
            for x in range(self.buffer.columns):
                char = row.get(x, default)
                fg = QColor(COLORS.get(char.fg, "#" + char.fg))
                bg = BACKGROUND if char.bg == "default" else QColor(COLORS.get(char.bg, "#" + char.bg))
                if char.reverse:
                    bg = fg
                if self.selected(offset + y, x):
                    bg = QColor("#38647e")
                painter.fillRect(
                    4 + x * self.cell_width, 4 + y * self.cell_height, self.cell_width, self.cell_height, bg
                )
            for x in range(self.buffer.columns):
                char = row.get(x, default)
                if not char.data or char.data == " ":
                    continue
                fg = QColor(COLORS.get(char.fg, "#" + char.fg))
                if char.reverse:
                    fg = BACKGROUND if char.bg == "default" else QColor(COLORS.get(char.bg, "#" + char.bg))
                font = QFont(self.terminal_font)
                font.setBold(char.bold)
                font.setItalic(char.italics)
                font.setUnderline(char.underscore)
                font.setStrikeOut(char.strikethrough)
                painter.setFont(font)
                painter.setPen(fg)
                painter.drawText(
                    QPointF(4 + x * self.cell_width, 4 + y * self.cell_height + self.baseline), char.data
                )
        cursor = self.buffer.cursor
        if (
            self.enabled_input
            and self.hasFocus()
            and self.cursor_on
            and not cursor.hidden
            and offset == self.verticalScrollBar().maximum()
        ):
            painter.setPen(QPen(QColor("#d8e7ea"), 1))
            painter.drawRect(
                4 + min(cursor.x, self.buffer.columns - 1) * self.cell_width,
                4 + cursor.y * self.cell_height,
                self.cell_width - 1,
                self.cell_height - 1,
            )

    def send(self, data):
        if self.enabled_input:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            self.input_bytes.emit(data)

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        ctrl = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        alt = bool(modifiers & Qt.AltModifier)
        if ctrl and shift and key == Qt.Key_C:
            self.copy_selection()
            return
        if ctrl and shift and key == Qt.Key_V:
            self.paste_clipboard()
            return
        if shift and key == Qt.Key_Insert:
            self.paste_clipboard()
            return
        if shift and key in (Qt.Key_PageUp, Qt.Key_PageDown):
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + (-1 if key == Qt.Key_PageUp else 1) * self.buffer.lines)
            return
        if not self.enabled_input:
            return
        arrows = {
            Qt.Key_Up: "A",
            Qt.Key_Down: "B",
            Qt.Key_Right: "C",
            Qt.Key_Left: "D",
            Qt.Key_Home: "H",
            Qt.Key_End: "F",
        }
        data = None
        if key in arrows:
            suffix = arrows[key]
            modifier = 1 + int(shift) + 2 * int(alt) + 4 * int(ctrl)
            if modifier > 1:
                data = f"\x1b[1;{modifier}{suffix}".encode()
            else:
                data = ("\x1bO" if 1 << 5 in self.buffer.mode else "\x1b[").encode() + suffix.encode()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            data = b"\r"
        elif key == Qt.Key_Backspace:
            data = b"\x1b\x7f" if alt else b"\x7f"
        elif key == Qt.Key_Tab:
            data = b"\t"
        elif key == Qt.Key_Backtab:
            data = b"\x1b[Z"
        elif key == Qt.Key_Escape:
            data = b"\x1b"
        elif ctrl and Qt.Key_A <= key <= Qt.Key_Z:
            data = bytes([key - Qt.Key_A + 1])
        elif ctrl and key in (Qt.Key_Space, Qt.Key_At):
            data = b"\0"
        elif ctrl and key in (
            Qt.Key_BracketLeft,
            Qt.Key_Backslash,
            Qt.Key_BracketRight,
            Qt.Key_AsciiCircum,
            Qt.Key_Underscore,
        ):
            data = bytes([int(key) & 31])
        else:
            special = {
                Qt.Key_Insert: "2~",
                Qt.Key_Delete: "3~",
                Qt.Key_PageUp: "5~",
                Qt.Key_PageDown: "6~",
                Qt.Key_F5: "15~",
                Qt.Key_F6: "17~",
                Qt.Key_F7: "18~",
                Qt.Key_F8: "19~",
                Qt.Key_F9: "20~",
                Qt.Key_F10: "21~",
                Qt.Key_F11: "23~",
                Qt.Key_F12: "24~",
            }
            if Qt.Key_F1 <= key <= Qt.Key_F4:
                data = b"\x1bO" + bytes([ord("P") + key - Qt.Key_F1])
            elif key in special:
                data = ("\x1b[" + special[key]).encode()
            elif event.text():
                data = event.text().encode("utf-8")
                if alt:
                    data = b"\x1b" + data
        if data is not None:
            self.send(data)
        event.accept()

    def focusNextPrevChild(self, next):
        return False  # Tab belongs to remote shell completion, never widget navigation.

    def inputMethodEvent(self, event):
        if event.commitString():
            self.send(event.commitString().encode("utf-8"))
        event.accept()

    def inputMethodQuery(self, query):
        if query == Qt.ImCursorRectangle:
            return QRect(
                4 + self.buffer.cursor.x * self.cell_width,
                4 + self.buffer.cursor.y * self.cell_height,
                self.cell_width,
                self.cell_height,
            )
        if query == Qt.ImFont:
            return self.terminal_font
        if query == Qt.ImEnabled:
            return self.enabled_input
        return super().inputMethodQuery(query)

    def position(self, point):
        return (
            self.verticalScrollBar().value()
            + max(0, min(self.buffer.lines - 1, int((point.y() - 4) // self.cell_height))),
            max(0, min(self.buffer.columns, int((point.x() - 4) // self.cell_width))),
        )

    def mousePressEvent(self, event):
        self.setFocus()
        if self.mouse_reporting(event):
            button = {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}.get(event.button())
            if button is not None:
                self.reported_button = button
                self.report_mouse(button, event)
            return
        if event.button() == Qt.LeftButton:
            position = self.position(event.position())
            self.selection = (position, position)
            self.dragging = True
            self.viewport().update()
        elif event.button() == Qt.MiddleButton:
            self.paste_clipboard(selection=True)

    def mouseMoveEvent(self, event):
        if self.mouse_reporting(event) and (
            1003 << 5 in self.buffer.mode
            or (1002 << 5 in self.buffer.mode and self.reported_button is not None)
        ):
            self.report_mouse(32 + (self.reported_button if self.reported_button is not None else 3), event)
            return
        if self.dragging:
            self.selection = (self.selection[0], self.position(event.position()))
            self.viewport().update()

    def mouseReleaseEvent(self, event):
        if self.reported_button is not None:
            self.report_mouse(self.reported_button, event, release=True)
            self.reported_button = None
            return
        if event.button() == Qt.LeftButton:
            self.dragging = False
            if self.selected_text() and QApplication.clipboard().supportsSelection():
                QApplication.clipboard().setText(self.selected_text(), QClipboard.Selection)

    def selected_text(self):
        if not self.selection:
            return ""
        start, end = sorted(self.selection)
        rows = self.buffer.rows()
        result = []
        for y in range(start[0], min(end[0] + 1, len(rows))):
            left = start[1] if y == start[0] else 0
            right = end[1] if y == end[0] else self.buffer.columns
            result.append(
                "".join(rows[y].get(x, self.buffer.default_char).data for x in range(left, right)).rstrip()
            )
        return "\n".join(result)

    def copy_selection(self):
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)

    def paste_clipboard(self, selection=False):
        clipboard = QApplication.clipboard()
        text = clipboard.text(QClipboard.Selection if selection else QClipboard.Clipboard)
        if len(text.encode("utf-8")) <= 1024 * 1024:
            self.send(self.buffer.paste(text))

    def contextMenuEvent(self, event):
        if self.mouse_reporting(event):
            return
        menu = QMenu(self)
        copy = menu.addAction("复制  Ctrl+Shift+C", self.copy_selection)
        copy.setEnabled(bool(self.selected_text()))
        paste = menu.addAction("粘贴  Ctrl+Shift+V", self.paste_clipboard)
        paste.setEnabled(self.enabled_input)
        menu.exec(event.globalPos())

    def wheelEvent(self, event):
        if self.mouse_reporting(event):
            for _ in range(abs(event.angleDelta().y()) // 120):
                self.report_mouse(64 if event.angleDelta().y() > 0 else 65, event)
            event.accept()
            return
        if self.enabled_input and self.buffer.active is self.buffer.alternate:
            self.send((b"\x1b[A" if event.angleDelta().y() > 0 else b"\x1b[B") * 3)
            event.accept()
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - event.angleDelta().y() // 120 * 3)
        event.accept()

    def mouse_reporting(self, event):
        return (
            self.enabled_input
            and not event.modifiers() & Qt.ShiftModifier
            and any(mode << 5 in self.buffer.mode for mode in (1000, 1002, 1003))
        )

    def report_mouse(self, button, event, release=False):
        row, column = self.position(event.position())
        row -= self.verticalScrollBar().value()
        column = min(column, self.buffer.columns - 1)
        if event.modifiers() & Qt.AltModifier:
            button += 8
        if event.modifiers() & Qt.ControlModifier:
            button += 16
        if 1006 << 5 in self.buffer.mode:
            self.send(f"\x1b[<{button};{column + 1};{row + 1}{'m' if release else 'M'}".encode())
        elif column < 223 and row < 223:
            self.send(b"\x1b[M" + bytes([32 + (3 if release else button), 33 + column, 33 + row]))
