"""Two-line drawing for the case clue list: the clue sentence, then source and certainty.

The list keeps its own check boxes and item data; this only changes how a row is drawn
and how tall it is, so long clues wrap instead of being cut off. The check box (analysis
use) sits beside the first line of the clue, and no words repeat what it shows.
"""

from __future__ import annotations

import html
from collections.abc import Mapping

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtGui import QFontInfo, QMouseEvent, QPainter, QTextDocument
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from video_agent.contracts import AnalysisClue, ClueSource, InformationCertainty
from video_agent.ui import theme

# Like the demo page's status marks: a small dot with the word, only for the states that
# need a second look. "확인" stays plain so it does not read as a verified fact.
_MARKED_CERTAINTY = {
    InformationCertainty.ESTIMATED: (theme.WARNING_DOT, theme.WARNING_TEXT),
    InformationCertainty.UNKNOWN: (theme.GRAY_50, theme.TEXT),
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
        opt, style, text_rect, check_rect, document = self._layout(option, index, clue)
        # Background and selection mark come from the style sheet; the check box is drawn
        # separately so it lines up with the first line of a wrapped clue.
        background = QStyleOptionViewItem(opt)
        background.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, opt.widget)
        check = QStyleOptionViewItem(opt)
        check.rect = check_rect
        # The indicator reads On/Off from the state flags, as the item view sets them.
        check.state &= ~(QStyle.StateFlag.State_On | QStyle.StateFlag.State_Off)
        check.state |= (
            QStyle.StateFlag.State_On
            if opt.checkState == Qt.CheckState.Checked
            else QStyle.StateFlag.State_Off
        )
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, check, painter, opt.widget
        )
        painter.save()
        painter.translate(text_rect.left(), text_rect.top() + self._padding(opt))
        document.drawContents(painter)
        painter.restore()

    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        """Toggle analysis use only on the (moved) check box; keys work as before."""
        clue = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(clue, AnalysisClue) or not isinstance(event, QMouseEvent):
            return super().editorEvent(event, model, option, index)
        if not index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return False
        check_rect = self._layout(option, index, clue)[3]
        if not check_rect.contains(event.position().toPoint()):
            return False
        released = event.type() == QEvent.Type.MouseButtonRelease
        if released and event.button() == Qt.MouseButton.LeftButton:
            state = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole))
            new_state = (
                Qt.CheckState.Unchecked if state == Qt.CheckState.Checked else Qt.CheckState.Checked
            )
            return model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)
        # Press and double click on the box are consumed so they do not start editing.
        return event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)

    def _layout(self, option: QStyleOptionViewItem, index, clue: AnalysisClue):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        check_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, opt.widget
        )
        # KRDS check box + label gap (8px at 100%).
        gap = max(4, round(QFontInfo(opt.font).pixelSize() / 2))
        text_rect.setLeft(max(text_rect.left(), check_rect.right() + 1 + gap))
        document = self.document(opt, clue, text_rect.width())
        document.size()  # lays the text out so the first line height is known
        first = document.firstBlock().layout()
        line_height = first.lineAt(0).height() if first.lineCount() else check_rect.height()
        top = text_rect.top() + self._padding(opt) + round((line_height - check_rect.height()) / 2)
        check_rect.moveTop(top)
        return opt, style, text_rect, check_rect, document

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        clue = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(clue, AnalysisClue):
            return super().sizeHint(option, index)
        probe = QStyleOptionViewItem(option)
        widget = probe.widget
        width = widget.viewport().width() if widget is not None else 320
        probe_height = 200
        probe.rect = QRect(0, 0, width, probe_height)
        opt, _style, text_rect, _check, document = self._layout(probe, index, clue)
        # Item padding and border from the style sheet, plus our own breathing room.
        frame = probe_height - text_rect.height()
        height = int(document.size().height() + 0.999) + 2 * self._padding(opt) + frame
        return QSize(width, height)

    @staticmethod
    def _padding(opt: QStyleOptionViewItem) -> int:
        return max(2, opt.fontMetrics.height() // 4)

    def document(self, opt: QStyleOptionViewItem, clue: AnalysisClue, width: int) -> QTextDocument:
        # KRDS sizes relative to the 16px body: clue body-medium 17px, label-small 15px.
        base = QFontInfo(opt.font).pixelSize()
        sentence_size = base * theme.CONTENT_PX / theme.BODY_PX
        meta_size = base * theme.LABEL_PX / theme.BODY_PX
        source = html.escape(self._source_labels[clue.source])
        certainty = html.escape(self._certainty_labels[clue.certainty])
        if clue.certainty in _MARKED_CERTAINTY:
            dot, color = _MARKED_CERTAINTY[clue.certainty]
            certainty_html = (
                f'<span style="color:{dot};">●</span>&nbsp;'
                f'<span style="color:{color};">{certainty}</span>'
            )
        else:
            certainty_html = f'<span style="color:{theme.TEXT};">{certainty}</span>'
        markup = (
            f'<p style="margin:0; font-size:{sentence_size:.0f}px; line-height:150%; color:{theme.TEXT};">'
            f"{html.escape(clue.text)}</p>"
            f'<p style="margin:0; margin-top:{max(2, int(base // 4))}px; '
            f'font-size:{meta_size:.0f}px; line-height:150%; color:{theme.TEXT_SUB};">'
            f'출처:&nbsp;<span style="color:{theme.TEXT};">{source}</span>'
            f'&nbsp;&nbsp;<span style="color:{theme.GRAY_40};">|</span>&nbsp;&nbsp;'
            f"확실성:&nbsp;{certainty_html}</p>"
        )
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(opt.font)
        document.setHtml(markup)
        document.setTextWidth(max(40, width))
        return document
