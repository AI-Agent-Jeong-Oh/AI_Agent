"""Drawing of the step ③ candidate rows, like the demo page's list.

A row shows the candidate number (with a bookmark under it when marked), then the time
range and length, the summary (up to two lines) and the review state as a mark with its
word, plus "저장 안 됨" while edits are unsaved. The selected row gets a 3px blue line on
the left over the light blue selection. Message rows (no candidate) draw as plain text.
The items keep a plain text for screen readers; this only changes how a row looks.
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
SUMMARY_LINES = 2


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
        line = metrics.height()
        number_width = metrics.horizontalAdvance("00")
        left = opt.rect.left() + pad
        top = opt.rect.top() + pad

        bold = QFont(font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRect(left, top, number_width, line),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            str(row.number),
        )
        if row.bookmarked:
            size = round(_px(font) * 14 / 16)
            theme.icon("bookmark", theme.NAVY).paint(
                painter,
                QRect(left + (number_width - size) // 2, top + line + 2, size, size),
            )

        text_left = left + number_width + pad
        width = opt.rect.right() - pad - text_left
        painter.setFont(font)
        painter.drawText(
            QRect(text_left, top, width, line),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            row.time_text,
        )
        time_width = metrics.horizontalAdvance(row.time_text)
        painter.setFont(small)
        painter.setPen(QColor(theme.TEXT_SUB))
        painter.drawText(
            QRect(text_left + time_width + 6, top, max(0, width - time_width - 6), line),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            row.length_text,
        )

        y = top + line + 2
        summary_lines = self._summary_lines(row.summary, font, width)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        for text in summary_lines:
            painter.drawText(
                QRect(text_left, y, width, line),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            y += line
        y += 2
        state_line = small_metrics.height()
        mark = round(_px(font) * 10 / 16)
        paint_mark(
            painter,
            QRectF(text_left, y + (state_line - mark) / 2, mark, mark),
            JUDGEMENT_MARKS[row.judgement],
        )
        painter.setFont(small)
        painter.setPen(QColor(theme.TEXT))
        label_left = text_left + mark + 6
        painter.drawText(
            QRect(label_left, y, width, state_line),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            row.judgement_label,
        )
        if row.unsaved:
            painter.setPen(QColor(theme.WARNING_TEXT))
            painter.drawText(
                QRect(label_left + small_metrics.horizontalAdvance(row.judgement_label) + 12, y, width, state_line),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "저장 안 됨",
            )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        row = index.data(ROW_ROLE)
        if not isinstance(row, CandidateRow):
            return super().sizeHint(option, index)
        font = option.font
        metrics = QFontMetrics(font)
        pad = self._pad(font)
        widget = option.widget
        total = widget.viewport().width() if widget is not None else 320
        width = total - pad * 3 - metrics.horizontalAdvance("00")
        lines = len(self._summary_lines(row.summary, font, width))
        height = (
            pad * 2
            + metrics.height() * (1 + lines)
            + QFontMetrics(_small(font)).height()
            + 4
            + 1  # the row line from the style sheet
        )
        return QSize(total, height)

    @staticmethod
    def _pad(font: QFont) -> int:
        return max(6, round(_px(font) * 12 / 16))

    @staticmethod
    def _summary_lines(text: str, font: QFont, width: int) -> list[str]:
        """Up to two lines, broken at spaces where possible; the rest is elided."""
        metrics = QFontMetrics(font)
        width = max(40, width)
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            if metrics.horizontalAdvance(trial) <= width or not current:
                current = trial
                continue
            lines.append(current)
            current = word
            if len(lines) == SUMMARY_LINES:
                break
        if len(lines) < SUMMARY_LINES and current:
            lines.append(current)
            current = ""
        rest = " ".join(words[len(" ".join(lines).split()):])
        if rest and lines:
            lines[-1] = metrics.elidedText(f"{lines[-1]} {rest}", Qt.TextElideMode.ElideRight, width)
        return [metrics.elidedText(item, Qt.TextElideMode.ElideRight, width) for item in lines] or [""]
