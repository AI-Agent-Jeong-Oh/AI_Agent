"""The "안내" explanation box (KRDS contextual help) and its trigger button.

Drawn as one speech bubble: a rounded box with a small pointer toward the button that
opened it. The fill and the outline come from the same painted path, so they always line
up (a style-sheet border around a separately painted fill did not on macOS). It closes on
Esc, a click outside, or its close button.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from video_agent.ui import theme

# KRDS contextual help: 360px wide, 16px padding, 12px corners, 16px pointer.
_WIDTH = 360
_PADDING = 16
_RADIUS = 12
_ARROW = 8  # half the pointer's base; it is this tall too
_ARROW_INSET = 24  # pointer distance from the box's left edge
_SHADOW = 2  # room for the antialiased outline


def place_below(popup: QWidget, anchor: QWidget, *, x_offset: int = 0) -> QPoint:
    """Top-left position under the anchor, kept on the anchor's screen."""
    popup.adjustSize()
    point = anchor.mapToGlobal(QPoint(x_offset, anchor.height() + 2))
    screen = anchor.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        point.setX(max(area.left(), min(point.x(), area.right() - popup.width())))
        if point.y() + popup.height() > area.bottom():
            point.setY(anchor.mapToGlobal(QPoint(0, 0)).y() - popup.height())
    return point


class InfoPopover(QWidget):
    def __init__(self, title: str, text: str, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._arrow_x = _ARROW_INSET
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _SHADOW + _PADDING, _ARROW + _PADDING - 4, _SHADOW + 8, _SHADOW + _PADDING
        )
        layout.setSpacing(8)
        header = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("popoverTitle")
        close = QPushButton()
        close.setObjectName("popoverClose")
        close.setAccessibleName("닫기")
        theme.set_icon(close, "x")
        close.clicked.connect(self.close)
        header.addWidget(heading, 1)
        header.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        body = QLabel(text)
        body.setObjectName("popoverText")
        body.setWordWrap(True)
        body.setFixedWidth(_WIDTH - 2 * _PADDING)
        layout.addWidget(body)
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)

    def show_below(self, anchor: QWidget) -> None:
        """Open under the anchor with the pointer at the anchor's middle."""
        anchor_middle = anchor.mapToGlobal(QPoint(anchor.width() // 2, 0)).x()
        point = place_below(self, anchor, x_offset=anchor.width() // 2 - _SHADOW - _ARROW_INSET)
        self._arrow_x = max(_RADIUS + _ARROW, min(anchor_middle - point.x() - _SHADOW, self.width() - 40))
        self.move(point)
        self.show()
        self.update()

    def _bubble(self) -> QPainterPath:
        box = QRectF(self.rect()).adjusted(_SHADOW + 0.5, _ARROW + 0.5, -_SHADOW - 0.5, -_SHADOW - 0.5)
        path = QPainterPath()
        path.addRoundedRect(box, _RADIUS, _RADIUS)
        pointer = QPainterPath()
        tip = QPointF(box.left() + self._arrow_x, box.top() - _ARROW)
        pointer.moveTo(tip.x() - _ARROW, box.top() + 1)
        pointer.lineTo(tip)
        pointer.lineTo(tip.x() + _ARROW, box.top() + 1)
        pointer.closeSubpath()
        return path.united(pointer)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bubble = self._bubble()
        painter.fillPath(bubble, QColor(theme.SURFACE))
        painter.setPen(QPen(QColor(theme.GRAY_30), 1))
        painter.drawPath(bubble)
        painter.end()


def info_mark(help_text: str, name: str, label: str = "안내") -> QPushButton:
    """Small 'i' button that opens the explanation box (click, Space or Enter)."""
    button = QPushButton(label)
    button.setObjectName("infoMark")
    theme.set_icon(button, "info", theme.PRIMARY)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAccessibleName(name)
    button.setAccessibleDescription(help_text)
    popover = InfoPopover(name, help_text, button)
    button.clicked.connect(lambda: popover.show_below(button))
    return button
