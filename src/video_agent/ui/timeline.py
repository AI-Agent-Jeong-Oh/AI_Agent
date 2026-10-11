"""Playback bar that also marks the analysed candidate segments (as on the demo page).

Each candidate is a band over the bar, coloured by the reviewer's saved judgement; the
selected one is solid and taller. Clicking a band selects that candidate, and hovering
names it, so the marks are not told apart by colour alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QHelpEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QToolTip

from video_agent.ui import theme


@dataclass(frozen=True, slots=True)
class TimelineMark:
    start_ms: int
    end_ms: int
    color: str
    label: str
    selected: bool = False


class TimelineSlider(QSlider):
    mark_clicked = Signal(int)

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setObjectName("timeline")
        self.setMouseTracking(True)
        self._marks: tuple[TimelineMark, ...] = ()

    def set_marks(self, marks: tuple[TimelineMark, ...]) -> None:
        self._marks = marks
        self.setAccessibleDescription(
            f"분석 후보 {len(marks)}개 표시" if marks else ""
        )
        self.update()

    @property
    def marks(self) -> tuple[TimelineMark, ...]:
        return self._marks

    def _geometry(self) -> tuple[QRect, int]:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
        )
        return groove, handle.width()

    def _x(self, value: int, groove: QRect, handle_width: int) -> float:
        span = max(1, groove.width() - handle_width)
        position = QStyle.sliderPositionFromValue(
            self.minimum(), max(self.minimum() + 1, self.maximum()), value, span
        )
        return groove.left() + handle_width / 2 + position

    def _band(self, mark: TimelineMark, groove: QRect, handle_width: int) -> QRectF:
        left = self._x(mark.start_ms, groove, handle_width)
        right = max(left + 4, self._x(mark.end_ms, groove, handle_width))
        height = 20 if mark.selected else 14
        top = self.height() / 2 - height / 2
        return QRectF(left, top, right - left, height)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._marks and self.maximum() > self.minimum():
            groove, handle_width = self._geometry()
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for mark in sorted(self._marks, key=lambda item: item.selected):
                band = self._band(mark, groove, handle_width)
                color = QColor(mark.color)
                if not mark.selected:
                    color.setAlpha(150)
                painter.setBrush(color)
                painter.setPen(QPen(QColor(theme.PRIMARY_70), 1.5) if mark.selected else Qt.PenStyle.NoPen)
                painter.drawRoundedRect(band, 3, 3)
            painter.end()
        # The bar and handle go on top, so the position stays visible over a band.
        super().paintEvent(event)

    def mark_at(self, point: QPoint) -> int | None:
        if not self._marks or self.maximum() <= self.minimum():
            return None
        groove, handle_width = self._geometry()
        for index, mark in enumerate(self._marks):
            if self._band(mark, groove, handle_width).adjusted(-2, -4, 2, 4).contains(point):
                return index
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
        )
        index = self.mark_at(event.position().toPoint())
        if (
            event.button() == Qt.MouseButton.LeftButton
            and index is not None
            and not handle.contains(event.position().toPoint())
        ):
            self.mark_clicked.emit(index)
            return
        super().mousePressEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            index = self.mark_at(event.pos())
            if index is not None:
                QToolTip.showText(event.globalPos(), self._marks[index].label, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)
