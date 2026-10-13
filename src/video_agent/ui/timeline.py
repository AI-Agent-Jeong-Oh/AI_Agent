"""Playback bar that also marks the analysed candidate segments (as on the demo page).

Each candidate is a thin band on the bar, marked by the reviewer's saved judgement (see
judgement_marks); the selected one is taller with a blue ring, and a bookmarked one has a
small bookmark above it. Clicking a band selects that candidate, and `TimelineLegend`
under the bar names the marks, so they are not told apart by colour alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontInfo, QFontMetrics, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QWidget

from video_agent.contracts import ReviewJudgement
from video_agent.ui import theme
from video_agent.ui.judgement_marks import JUDGEMENT_MARKS, MarkStyle, paint_mark


@dataclass(frozen=True, slots=True)
class TimelineMark:
    start_ms: int
    end_ms: int
    style: MarkStyle
    label: str
    selected: bool = False
    bookmarked: bool = False


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
        right = max(left + 6, self._x(mark.end_ms, groove, handle_width))
        unit = QFontInfo(self.font()).pixelSize() / 16
        height = (14 if mark.selected else 10) * unit
        top = groove.center().y() + 0.5 - height / 2
        return QRectF(left, top, right - left, height)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if not self._marks or self.maximum() <= self.minimum():
            return
        groove, handle_width = self._geometry()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        unit = QFontInfo(self.font()).pixelSize() / 16
        for mark in sorted(self._marks, key=lambda item: item.selected):
            band = self._band(mark, groove, handle_width)
            if mark.selected:
                painter.setPen(QPen(QColor(theme.PRIMARY), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(band.adjusted(-2, -2, 2, 2), 3, 3)
            paint_mark(painter, band, mark.style)
            if mark.bookmarked:
                size = round(10 * unit)
                theme.icon("bookmark", theme.NAVY).paint(
                    painter,
                    QRect(round(band.center().x() - size / 2), round(band.top() - size - 1), size, size),
                )
        # The handle goes back on top, so the position stays visible over a band.
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        option.subControls = QStyle.SubControl.SC_SliderHandle
        self.style().drawComplexControl(QStyle.ComplexControl.CC_Slider, option, painter, self)
        painter.end()

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

LEGEND_ITEMS = (
    (ReviewJudgement.UNREVIEWED, "AI 후보 (미검토)"),
    (ReviewJudgement.RELEVANT, "관련 있음"),
    (ReviewJudgement.NEEDS_CHECK, "추가 확인"),
    (ReviewJudgement.EXCLUDED, "제외"),
)


class TimelineLegend(QWidget):
    """One small line under the bar naming each mark (shown while marks are shown)."""

    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName(
            "후보 표시 범례: " + ", ".join(label for _judgement, label in LEGEND_ITEMS)
        )

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(max(9, round(QFontInfo(self.font()).pixelSize() * theme.CAPTION_PX / theme.BODY_PX)))
        return font

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        metrics = QFontMetrics(self._font())
        width = sum(metrics.horizontalAdvance(label) + 30 for _j, label in LEGEND_ITEMS)
        return QSize(width, metrics.height() + 2)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(0, self.sizeHint().height())

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        font = self._font()
        metrics = QFontMetrics(font)
        painter.setFont(font)
        mark = round(metrics.height() * 0.6)
        x = 0
        top = (self.height() - mark) / 2
        for judgement, label in LEGEND_ITEMS:
            paint_mark(painter, QRectF(x, top, mark, mark), JUDGEMENT_MARKS[judgement])
            x += mark + 5
            painter.setPen(QColor(theme.TEXT_SUB))
            painter.drawText(QRect(x, 0, metrics.horizontalAdvance(label) + 2, self.height()),
                             Qt.AlignmentFlag.AlignVCenter, label)
            x += metrics.horizontalAdvance(label) + 14
        painter.end()
