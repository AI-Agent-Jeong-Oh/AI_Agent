"""Two-line drawing for the case clue list: the clue sentence, then source and certainty.

The list keeps its own check boxes and item data; this only changes how a row is drawn
and how tall it is, so long clues wrap instead of being cut off.
"""

from __future__ import annotations

import html
from collections.abc import Mapping

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QPainter, QTextDocument
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from video_agent.contracts import AnalysisClue, ClueSource, InformationCertainty
from video_agent.ui import theme

# Like the demo page's status marks: a small dot with the word, only for the states that
# need a second look. "확인" stays plain so it does not read as a verified fact.
_MARKED_CERTAINTY = {
    InformationCertainty.ESTIMATED: (theme.WARNING_DOT, theme.WARNING_TEXT),
    InformationCertainty.UNKNOWN: (theme.TEXT_MUTED, theme.TEXT_SUB),
}


class ClueItemDelegate(QStyledItemDelegate):
    def __init__(
        self,
        source_labels: Mapping[ClueSource, str],
        certainty_labels: Mapping[InformationCertainty, str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._source_labels = source_labels
        self._certainty_labels = certainty_labels

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        clue = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(clue, AnalysisClue):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        # Background, selection mark and the check box come from the style sheet.
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget
        )
        pad = self._padding(opt)
        document = self.document(opt, clue, text_rect.width())
        painter.save()
        painter.translate(text_rect.left(), text_rect.top() + pad)
        document.drawContents(painter)
        painter.restore()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        clue = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(clue, AnalysisClue):
            return super().sizeHint(option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        width = widget.viewport().width() if widget is not None else 320
        probe_height = 200
        opt.rect = QRect(0, 0, width, probe_height)
        style = widget.style() if widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        document = self.document(opt, clue, text_rect.width())
        # Item padding and border from the style sheet, plus our own breathing room.
        frame = probe_height - text_rect.height()
        height = int(document.size().height() + 0.999) + 2 * self._padding(opt) + frame
        return QSize(width, height)

    @staticmethod
    def _padding(opt: QStyleOptionViewItem) -> int:
        return max(2, opt.fontMetrics.height() // 4)

    def document(self, opt: QStyleOptionViewItem, clue: AnalysisClue, width: int) -> QTextDocument:
        # Demo page sizes relative to its 13px body: clue 14px, source and certainty 12px.
        base = opt.font.pointSizeF()
        sentence_size = base * 14 / 13
        meta_size = base * 12 / 13
        source = html.escape(self._source_labels[clue.source])
        certainty = html.escape(self._certainty_labels[clue.certainty])
        if clue.certainty in _MARKED_CERTAINTY:
            dot, color = _MARKED_CERTAINTY[clue.certainty]
            certainty_html = (
                f'<span style="color:{dot};">●</span>&nbsp;'
                f'<span style="color:{color};">{certainty}</span>'
            )
        else:
            certainty_html = f'<span style="color:{theme.TEXT_SUB};">{certainty}</span>'
        unused = (
            ""
            if clue.use_for_analysis
            else f' <span style="color:{theme.TEXT_MUTED};">· 분석에 사용 안 함</span>'
        )
        markup = (
            f'<p style="margin:0; font-size:{sentence_size:.1f}pt; color:{theme.TEXT};">'
            f"{html.escape(clue.text)}</p>"
            f'<p style="margin:0; margin-top:{max(2, int(base // 4))}px; '
            f'font-size:{meta_size:.1f}pt; color:{theme.TEXT_MUTED};">'
            f'출처&nbsp;<span style="color:{theme.TEXT_SUB};">{source}</span>'
            f" &nbsp;·&nbsp; 확실성&nbsp;{certainty_html}{unused}</p>"
        )
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(opt.font)
        document.setHtml(markup)
        document.setTextWidth(max(40, width))
        return document
