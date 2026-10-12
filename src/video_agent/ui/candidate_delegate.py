"""Drawing of the step ③ candidate rows (the list above the video).

A row is one line: the candidate number (and a bookmark when marked), the time range, the
summary cut to fit, "저장 안 됨" while edits are unsaved, and the review state as a mark
with its word. The selected row gets a 3px blue line on the left over the light blue
selection. Message rows (no candidate) draw as plain text. The items keep a plain text
for screen readers; this only changes how a row looks.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from video_agent.contracts import ReviewJudgement
from video_agent.ui import theme
from video_agent.ui.judgement_marks import JUDGEMENT_MARKS, paint_mark

ROW_ROLE = Qt.ItemDataRole.UserRole + 10


@dataclass(frozen=True, slots=True)
class CandidateRow:
    number: int
    time_text: str
    length_text: str
    summary: str
    judgement: ReviewJudgement
    judgement_label: str
    bookmarked: bool = False
    unsaved: bool = False


def _px(font: QFont) -> int:
    return QFontInfo(font).pixelSize()


def _small(font: QFont) -> QFont:
    small = QFont(font)
    small.setPixelSize(max(9, round(_px(font) * theme.LABEL_PX / theme.BODY_PX)))
    return small


class CandidateDelegate(QStyledItemDelegate):
    """One line per candidate: number, time, summary (cut to fit) and the review mark.

    The full AI result shows beside the list, so a row only needs to tell candidates apart.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(ROW_ROLE)
        if not isinstance(row, CandidateRow):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = QIcon()
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        # Background, row line and selection colour come from the style sheet.
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        if selected:
            painter.fillRect(QRect(opt.rect.left(), opt.rect.top(), 3, opt.rect.height() - 1), QColor(theme.PRIMARY))
        font = opt.font
        small = _small(font)
        metrics = QFontMetrics(font)
        small_metrics = QFontMetrics(small)
        pad = self._pad(font)
        top = opt.rect.top()
        height = opt.rect.height() - 1
        x = opt.rect.left() + pad
        right = opt.rect.right() - pad

        def text(rect: QRect, value: str, color: str, use: QFont, align=Qt.AlignmentFlag.AlignLeft) -> None:
            painter.setFont(use)
            painter.setPen(QColor(color))
            painter.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, value)

        # Number (bold), then a bookmark mark when set.
        bold = QFont(font)
        bold.setBold(True)
        number_width = metrics.horizontalAdvance("00")
        text(QRect(x, top, number_width, height), str(row.number), theme.TEXT, bold, Qt.AlignmentFlag.AlignHCenter)
        x += number_width + 4
        icon = round(_px(font) * 14 / 16)
        if row.bookmarked:
            theme.icon("bookmark", theme.NAVY).paint(painter, QRect(x, top + (height - icon) // 2, icon, icon))
        x += icon + pad // 2
        # Time range.
        time_width = metrics.horizontalAdvance("00:00–00:00")
        text(QRect(x, top, time_width, height), row.time_text, theme.TEXT, font)
        x += time_width + pad
        # Review mark and word at the right end, "저장 안 됨" before it while unsaved.
        mark = round(_px(font) * 10 / 16)
        label_width = max(small_metrics.horizontalAdvance(word) for word in ("관련 있음", "추가 확인", "미검토", "제외"))
        state_left = right - label_width - mark - 6
        paint_mark(
            painter,
            QRectF(state_left, top + (height - mark) / 2, mark, mark),
            JUDGEMENT_MARKS[row.judgement],
        )
        text(QRect(state_left + mark + 6, top, label_width, height), row.judgement_label, theme.TEXT, small)
        summary_right = state_left - pad
        if row.unsaved:
            unsaved_width = small_metrics.horizontalAdvance("저장 안 됨")
            summary_right -= unsaved_width + pad // 2
            text(QRect(summary_right + pad // 2, top, unsaved_width, height), "저장 안 됨", theme.WARNING_TEXT, small)
        # The summary takes what is left, cut with "…".
        width = max(0, summary_right - x)
        text(
            QRect(x, top, width, height),
            metrics.elidedText(row.summary, Qt.TextElideMode.ElideRight, width),
            theme.TEXT,
            font,
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        row = index.data(ROW_ROLE)
        if not isinstance(row, CandidateRow):
            return super().sizeHint(option, index)
        widget = option.widget
        total = widget.viewport().width() if widget is not None else 320
        # One line with room above and below, plus the 1px row line.
        return QSize(total, QFontMetrics(option.font).height() + 2 * self._pad(option.font) + 1)

    @staticmethod
    def _pad(font: QFont) -> int:
        return max(6, round(_px(font) * 10 / 16))
