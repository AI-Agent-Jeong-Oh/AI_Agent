"""Column layout for the motion candidate list: check box, number, range, peak, strength.

The list keeps its items, check states and text (read by screen readers and tests); this
only draws each row in columns under a matching header, like a table.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QListWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from video_agent.contracts import MotionProposal
from video_agent.ui import theme
from video_agent.video.files import format_milliseconds, seconds_to_milliseconds

HEADERS = ("", "번호", "구간", "최대 변화", "변화 크기")


def column_edges(metrics: QFontMetrics) -> tuple[int, ...]:
    """Left edges of the columns, sized from the font so they follow the zoom."""
    gap = metrics.horizontalAdvance("  ")
    widths = (
        metrics.height() + gap,  # check box
        metrics.horizontalAdvance("번호") + gap,
        metrics.horizontalAdvance("00:00:00–00:00:00") + gap,
        metrics.horizontalAdvance("00:00:00") + gap * 2,
    )
    edges = [metrics.horizontalAdvance(" ")]
    for width in widths:
        edges.append(edges[-1] + width)
    return tuple(edges)


def strength_label(score: float, top: float) -> str:
    """Relative to the largest change in this search, not a probability."""
    if top <= 0:
        return "-"
    ratio = score / top
    return "큼" if ratio >= 0.67 else "보통" if ratio >= 0.34 else "작음"


def _time(seconds: float) -> str:
    return format_milliseconds(seconds_to_milliseconds(seconds))


def _column_color(column: int) -> QColor:
    """Number and peak time are secondary; range and strength are read first."""
    return QColor(theme.TEXT_SUB if column in (1, 3) else theme.TEXT)


class MotionItemDelegate(QStyledItemDelegate):
    def _top_score(self, index: QModelIndex) -> float:
        model = index.model()
        scores = [
            proposal.motion_score
            for row in range(model.rowCount())
            if isinstance(
                proposal := model.index(row, 0).data(Qt.ItemDataRole.UserRole), MotionProposal
            )
        ]
        return max(scores, default=0.0)

    def _check_rect(self, option: QStyleOptionViewItem, edges: tuple[int, ...]) -> QRect:
        size = option.fontMetrics.height()
        top = option.rect.top() + (option.rect.height() - size) // 2
        return QRect(option.rect.left() + edges[0], top, size, size)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        proposal = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(proposal, MotionProposal):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget is not None else None
        edges = column_edges(opt.fontMetrics)
        background = QStyleOptionViewItem(opt)
        background.text = ""
        background.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, opt.widget)
        check = QStyleOptionViewItem(opt)
        check.rect = self._check_rect(opt, edges)
        check.state &= ~(QStyle.StateFlag.State_On | QStyle.StateFlag.State_Off)
        check.state |= (
            QStyle.StateFlag.State_On
            if opt.checkState == Qt.CheckState.Checked
            else QStyle.StateFlag.State_Off
        )
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, check, painter, opt.widget
        )
        values = (
            str(index.row() + 1),
            f"{_time(proposal.start_sec)}–{_time(proposal.end_sec)}",
            _time(proposal.peak_timestamp_sec),
            strength_label(proposal.motion_score, self._top_score(index)),
        )
        painter.save()
        painter.setFont(opt.font)
        for column, value in enumerate(values, start=1):
            left = opt.rect.left() + edges[column]
            right = opt.rect.left() + edges[column + 1] if column + 1 < len(edges) else opt.rect.right()
            rect = QRect(left, opt.rect.top(), right - left, opt.rect.height())
            painter.setPen(_column_color(column))
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, value)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), option.fontMetrics.height() * 2))

    def editorEvent(self, event: QEvent, model, option: QStyleOptionViewItem, index: QModelIndex) -> bool:
        """Toggle only on the drawn check box; keys and other clicks work as before."""
        if not isinstance(event, QMouseEvent) or not isinstance(
            index.data(Qt.ItemDataRole.UserRole), MotionProposal
        ):
            return super().editorEvent(event, model, option, index)
        rect = self._check_rect(option, column_edges(option.fontMetrics))
        if not rect.adjusted(-4, -4, 4, 4).contains(event.position().toPoint()):
            return False
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            state = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole))
            new_state = Qt.CheckState.Unchecked if state == Qt.CheckState.Checked else Qt.CheckState.Checked
            return model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)
        return event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)


class MotionHeader(QWidget):
    """Column names drawn at the same positions as the delegate's columns."""

    def __init__(self, candidates: QListWidget) -> None:
        super().__init__()
        self._candidates = candidates
        self.setObjectName("motionHeader")
        self.setAccessibleName("움직임 구간 열: 번호, 구간, 최대 변화, 변화 크기")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(200, self._candidates.fontMetrics().height() + 8)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setPen(_column_color(1))
        font = self._candidates.font()
        font.setBold(True)
        painter.setFont(font)
        # Same edges as the rows: the list's own font, from its viewport's left side.
        edges = column_edges(self._candidates.fontMetrics())
        offset = self._candidates.viewport().x()
        for column, title in enumerate(HEADERS):
            if not title:
                continue
            left = offset + edges[column]
            painter.drawText(
                QRect(left, 0, self.width() - left, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                title,
            )
        painter.end()
