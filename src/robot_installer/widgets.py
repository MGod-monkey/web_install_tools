from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLineEdit, QToolButton


class PasswordEdit(QLineEdit):
    def __init__(self, text=""):
        super().__init__(text)
        self.setObjectName("passwordField")
        self.setInputMethodHints(Qt.ImhSensitiveData | Qt.ImhNoPredictiveText)
        self.show_icon = self.eye_icon(False)
        self.hide_icon = self.eye_icon(True)
        self.visibility_action = self.addAction(self.show_icon, QLineEdit.TrailingPosition)
        self.visibility_action.setCheckable(True)
        self.visibility_action.toggled.connect(self.set_password_visible)
        self.findChild(QToolButton).setFocusPolicy(Qt.TabFocus)
        self.set_password_visible(False)

    def set_password_visible(self, visible):
        self.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.visibility_action.setIcon(self.hide_icon if visible else self.show_icon)
        label = "隐藏密码" if visible else "显示密码"
        self.visibility_action.setText(label)
        self.visibility_action.setToolTip(label)

    @staticmethod
    def eye_icon(crossed):
        pixmap = QPixmap(40, 40)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)
        with QPainter(pixmap) as painter:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#AEBFD7"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            outline = QPainterPath(QPointF(2, 10))
            outline.cubicTo(6, 3, 14, 3, 18, 10)
            outline.cubicTo(14, 17, 6, 17, 2, 10)
            painter.drawPath(outline)
            painter.drawEllipse(QPointF(10, 10), 2.25, 2.25)
            if crossed:
                painter.drawLine(QPointF(3, 3), QPointF(17, 17))
        return QIcon(pixmap)
