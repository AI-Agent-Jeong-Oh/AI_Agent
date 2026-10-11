"""Drawing of the CCTV video table rows, in the demo page's style.

Each row shows the file name with a small grey line under it (location and length, or what
is missing), and short status words led by a coloured dot (never colour alone). The table
items keep their plain text for screen readers; this only changes how a row looks.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from video_agent.contracts import InformationCertainty
from video_agent.ui import theme
from video_agent.video.files import format_milliseconds, seconds_to_milliseconds

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 2
CURRENT_ROLE = Qt.ItemDataRole.UserRole + 3

CLOCK_TEXT = {
    InformationCertainty.CONFIRMED: ("확인됨", theme.SUCCESS),
    InformationCertainty.ESTIMATED: ("추정", theme.WARNING_DOT),
    InformationCertainty.UNKNOWN: ("미확인", theme.GRAY_40),
}
_PADDING_X = 12
_DOT = 8
_GAP = 6


def status_color(status: str) -> str:
    """Dot colour for a video's analysis status line."""
    if status.startswith("분석 중"):
        return theme.PRIMARY_FILL
    if status.startswith("최근 실행 실패"):
        return theme.DANGER
    if status.startswith("일부 완료"):
        return theme.WARNING_DOT
    if status.startswith("분석 완료"):
        return theme.SUCCESS
    return theme.GRAY_40


def _small(font: QFont) -> QFont:
    small = QFont(font)
    small.setPixelSize(max(9, round(font.pixelSize() * theme.CAPTION_PX / theme.BODY_PX)))
    return small


class VideoRowDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        entry = index.data(ENTRY_ROLE)
        if entry is None:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = QIcon()
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = opt.rect.adjusted(_PADDING_X, 0, -_PADDING_X // 2, 0)
        painter.save()
        column = index.column()
        if column == 0:
            self._paint_file(painter, rect, opt.font, entry, bool(index.data(CURRENT_ROLE)))
        elif column == 1:
            text, color = CLOCK_TEXT[entry.clock_certainty]
            self._paint_dot_lines(painter, rect, opt.font, color, text, "")
        else:
            main, _, sub = entry.analysis_status.partition(" · ")
            self._paint_dot_lines(painter, rect, opt.font, status_color(main), main, sub)
        painter.restore()

    def _paint_file(self, painter: QPainter, rect: QRect, font: QFont, entry, current: bool) -> None:
        name_font = QFont(font)
        name_font.setBold(current)
        small = _small(font)
        top, bottom = self._line_rects(rect, font)
        tag = "재생 중" if current else ""
        tag_width = QFontMetrics(small).horizontalAdvance(tag) + _GAP if tag else 0
        name = entry.file_name or "파일 다시 연결 필요"
        painter.setFont(name_font)
        painter.setPen(QColor(theme.TEXT if entry.linked else theme.TEXT_FAINT))
        elided = QFontMetrics(name_font).elidedText(
            name, Qt.TextElideMode.ElideMiddle, max(10, top.width() - tag_width)
        )
        painter.drawText(top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
        if tag:
            painter.setFont(small)
            painter.setPen(QColor(theme.PRIMARY))
            left = top.left() + QFontMetrics(name_font).horizontalAdvance(elided) + _GAP
            painter.drawText(
                QRect(left, top.top(), tag_width, top.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                tag,
            )
        if entry.linked:
            duration = (
                f"길이 {format_milliseconds(seconds_to_milliseconds(entry.duration_seconds))}"
                if entry.duration_seconds is not None
                else "길이 확인 중"
            )
            # A location saved in an older case still shows in front of the length.
            detail = f"{entry.camera_location} · {duration}" if entry.camera_location else duration
            color = theme.TEXT_SUB
        else:
            detail, color = "같은 파일을 다시 연결해야 재생할 수 있음", theme.WARNING_TEXT
        painter.setFont(small)
        painter.setPen(QColor(color))
        painter.drawText(
            bottom,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(small).elidedText(detail, Qt.TextElideMode.ElideRight, bottom.width()),
        )

    def _paint_dot_lines(
        self, painter: QPainter, rect: QRect, font: QFont, color: str, main: str, sub: str
    ) -> None:
        top, bottom = self._line_rects(rect, font) if sub else (rect, rect)
        dot_top = top.center().y() - _DOT // 2 + 1
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(top.left(), dot_top, _DOT, _DOT)
        text_left = top.left() + _DOT + _GAP
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRect(text_left, top.top(), top.right() - text_left, top.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            main,
        )
        if sub:
            small = _small(font)
            painter.setFont(small)
            painter.setPen(QColor(theme.TEXT_SUB))
            painter.drawText(
                QRect(text_left, bottom.top(), bottom.right() - text_left, bottom.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                QFontMetrics(small).elidedText(sub, Qt.TextElideMode.ElideRight, bottom.right() - text_left),
            )

    @staticmethod
    def _line_rects(rect: QRect, font: QFont) -> tuple[QRect, QRect]:
        first = QFontMetrics(font).height()
        second = QFontMetrics(_small(font)).height()
        top = rect.top() + (rect.height() - first - second - 2) // 2
        return (
            QRect(rect.left(), top, rect.width(), first),
            QRect(rect.left(), top + first + 2, rect.width(), second),
        )

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        entry = index.data(ENTRY_ROLE)
        if entry is None:
            return super().sizeHint(option, index)
        metrics = QFontMetrics(option.font)
        small = QFontMetrics(_small(option.font))
        height = row_height(option.font)
        if index.column() == 1:
            width = _DOT + _GAP + max(metrics.horizontalAdvance(text) for text, _ in CLOCK_TEXT.values())
        elif index.column() == 2:
            main, _, sub = entry.analysis_status.partition(" · ")
            width = _DOT + _GAP + max(metrics.horizontalAdvance(main), small.horizontalAdvance(sub))
        else:
            width = metrics.horizontalAdvance(entry.file_name or "파일 다시 연결 필요")
        return QSize(width + _PADDING_X * 2, height)


def row_height(font: QFont) -> int:
    """Two lines (name and detail) with breathing room above and below."""
    return QFontMetrics(font).height() + QFontMetrics(_small(font)).height() + 18
