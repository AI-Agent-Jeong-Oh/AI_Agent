"""Table-style drawing of the case clue list, like the CCTV video table.

Each row has columns: analysis use (check box), the clue sentence (wrapping onto more
lines), source, and edit/delete marks at the end. Clues are not rated for certainty: all
of them are prior information not yet checked against the video. `ClueHeader` draws the grey
header line above the list from the same column positions. The list keeps its own check
boxes and item data; this only changes how a row looks and where clicks land.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontInfo,
    QFontMetrics,
    QHelpEvent,
    QIcon,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QWidget,
)

from video_agent.contracts import AnalysisClue, ClueSource
from video_agent.ui import theme

HEADER_LABELS = ("사용", "단서", "출처")


@dataclass(frozen=True)
class ClueColumns:
    check: QRect
    text: QRect
    source: QRect
    edit: QRect
    remove: QRect


def _px(font: QFont) -> int:
    return QFontInfo(font).pixelSize()


def clue_columns(
    rect: QRect,
    font: QFont,
    source_labels: Mapping[ClueSource, str],
) -> ClueColumns:
    """Column boxes of one row (or of the header), all `rect.height()` tall."""
    base = _px(font)
    pad = round(base * 12 / 16)
    metrics = QFontMetrics(font)
    indicator = round(base * 20 / 16)
    use_width = max(indicator, metrics.horizontalAdvance(HEADER_LABELS[0]))
    source_width = max(
        metrics.horizontalAdvance(label) for label in (*source_labels.values(), HEADER_LABELS[2])
    ) + pad
    action = round(base * 28 / 16)
    top, height = rect.top(), rect.height()
    left = rect.left() + pad
    check = QRect(left, top, use_width, height)
    right = rect.right() - pad // 2
    remove = QRect(right - action + 1, top, action, height)
    edit = QRect(remove.left() - action - 2, top, action, height)
    source = QRect(edit.left() - pad // 2 - source_width, top, source_width, height)
    text_left = check.right() + 1 + pad
    text = QRect(text_left, top, max(40, source.left() - pad - text_left), height)
    return ClueColumns(check, text, source, edit, remove)


class ClueItemDelegate(QStyledItemDelegate):
    edit_requested = Signal(int)
    remove_requested = Signal(int)

    def __init__(self, source_labels: Mapping[ClueSource, str], parent=None) -> None:
        super().__init__(parent)
        self._source_labels = source_labels

    def columns(self, rect: QRect, font: QFont) -> ClueColumns:
        return clue_columns(rect, font, self._source_labels)

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
        opt, style, cells, check_rect, document, line = self._layout(option, index, clue)
        # Background, row line and selection come from the style sheet; the check box is
        # drawn separately so it lines up with the first line of a wrapped clue.
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
        # Everything in a row sits in the middle of its height (a wrapped clue too).
        middle = opt.rect.center().y()
        # 150% line height adds its space under each line; half of it is added back on top
        # so the letters, not the line box, sit in the middle.
        first_line = document.firstBlock().layout().lineAt(0)
        leading = (
            first_line.height() - first_line.ascent() - first_line.descent()
            if first_line.isValid()
            else 0
        )
        painter.save()
        painter.translate(cells.text.left(), middle - (document.size().height() - leading) / 2)
        document.drawContents(painter)
        painter.restore()

        painter.save()
        first = QRect(0, middle - line // 2, 0, line)
        # The source as a small grey badge at the right of the first line.
        small = QFont(opt.font)
        small.setPixelSize(max(9, round(_px(opt.font) * theme.CAPTION_PX / theme.BODY_PX)))
        label = self._source_labels[clue.source]
        metrics = QFontMetrics(small)
        badge_w = metrics.horizontalAdvance(label) + round(_px(opt.font) * 16 / 16)
        badge_h = metrics.height() + 4
        badge = QRect(
            cells.source.right() - badge_w, first.top() + (line - badge_h) // 2, badge_w, badge_h
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.GRAY_10))
        painter.drawRoundedRect(badge, badge_h / 2, badge_h / 2)
        painter.setFont(small)
        painter.setPen(QColor(theme.TEXT_SUB))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        mode = QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled
        size = round(_px(opt.font) * 18 / 16)
        for name, cell in (("pencil", cells.edit), ("x", cells.remove)):
            target = QRect(
                cell.center().x() - size // 2, first.top() + (line - size) // 2, size, size
            )
            theme.icon(name).paint(painter, target, Qt.AlignmentFlag.AlignCenter, mode)
        painter.restore()

    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        """Toggle analysis use only on the (moved) check box; the end marks edit or delete."""
        clue = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(clue, AnalysisClue) or not isinstance(event, QMouseEvent):
            return super().editorEvent(event, model, option, index)
        _opt, _style, cells, check_rect, _document, _line = self._layout(option, index, clue)
        point = event.position().toPoint()
        released = (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        )
        consumed = event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
        for cell, signal in ((cells.edit, self.edit_requested), (cells.remove, self.remove_requested)):
            if cell.contains(point):
                if released:
                    signal.emit(index.row())
                    return True
                return consumed
        if not index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return False
        if not check_rect.adjusted(-4, -4, 4, 4).contains(point):
            return False
        if released:
            state = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole))
            new_state = (
                Qt.CheckState.Unchecked if state == Qt.CheckState.Checked else Qt.CheckState.Checked
            )
            return model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)
        # Press and double click on the box are consumed so they do not start editing.
        return consumed

    def helpEvent(self, event: QHelpEvent, view, option: QStyleOptionViewItem, index) -> bool:  # noqa: N802
        clue = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(clue, AnalysisClue) and event.type() == QEvent.Type.ToolTip:
            cells = self._layout(option, index, clue)[2]
            for cell, tip in ((cells.edit, "이 단서 수정"), (cells.remove, "이 단서 삭제")):
                if cell.contains(event.pos()):
                    QToolTip.showText(event.globalPos(), tip, view)
                    return True
        return super().helpEvent(event, view, option, index)

    def _layout(self, option: QStyleOptionViewItem, index, clue: AnalysisClue):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        cells = self.columns(opt.rect, opt.font)
        # A clue left out of the analysis reads grey.
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        muted = state is not None and Qt.CheckState(state) == Qt.CheckState.Unchecked
        document = self.document(opt, clue, cells.text.width(), muted=muted)
        document.size()  # lays the text out so the first line height is known
        first = document.firstBlock().layout()
        line = round(first.lineAt(0).height()) if first.lineCount() else QFontMetrics(opt.font).height()
        indicator = round(_px(opt.font) * 20 / 16)
        top = opt.rect.center().y() - indicator // 2
        check_rect = QRect(cells.check.left(), top, indicator, indicator)
        return opt, style, cells, check_rect, document, line

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
        probe.rect = QRect(0, 0, width, 200)
        opt, _style, _cells, _check, document, _line = self._layout(probe, index, clue)
        # Text plus room above and below, and the 1px row line from the style sheet.
        height = int(document.size().height() + 0.999) + 2 * self._padding(opt) + 1
        return QSize(width, height)

    @staticmethod
    def _padding(opt: QStyleOptionViewItem) -> int:
        return max(4, round(_px(opt.font) * 10 / 16))

    def document(
        self, opt: QStyleOptionViewItem, clue: AnalysisClue, width: int, *, muted: bool = False
    ) -> QTextDocument:
        # KRDS body-medium 17px for the clue sentence (relative to the 16px body).
        base = _px(opt.font)
        sentence_size = base * theme.CONTENT_PX / theme.BODY_PX
        markup = (
            f'<p style="margin:0; font-size:{sentence_size:.0f}px; line-height:150%; '
            f'color:{theme.TEXT_MUTED if muted else theme.TEXT};">{html.escape(clue.text)}</p>'
        )
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(opt.font)
        document.setHtml(markup)
        document.setTextWidth(max(40, width))
        return document


class ClueHeader(QWidget):
    """Grey header line over the clue list, with the same columns as the rows."""

    def __init__(self, view: QListWidget, delegate: ClueItemDelegate) -> None:
        super().__init__()
        self.setObjectName("clueHeader")
        self._view = view
        self._delegate = delegate
        self.setAccessibleName("분석 단서 표 머리줄: " + ", ".join(HEADER_LABELS))
        view.viewport().installEventFilter(self)

    def eventFilter(self, watched, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Resize:
            self.update()
        return False

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(200, round(_px(self._label_font()) * 30 / 15))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    def _label_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(max(9, round(_px(self._view.font()) * theme.LABEL_PX / theme.BODY_PX)))
        return font

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        # KRDS data table header: secondary-5 with a secondary-10 line, bold text.
        painter.fillRect(self.rect(), QColor(theme.TABLE_HEAD))
        painter.setPen(QColor(theme.TABLE_HEAD_LINE))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        width = self._view.viewport().width() or self.width()
        cells = self._delegate.columns(QRect(0, 0, width, self.height()), self._view.font())
        font = self._label_font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_BOLDER))
        for label, cell in zip(
            HEADER_LABELS, (cells.check, cells.text, cells.source), strict=True
        ):
            painter.drawText(
                QRect(cell.left(), 0, max(cell.width(), 60), self.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )


class ClueList(QListWidget):
    """The clue rows; says so when there are none yet.

    A clue is edited only from its pencil mark: clicking the sentence does not pick it
    (picking it by accident made the next new clue overwrite it). While one is being
    edited, a click anywhere else in the table lets go of it (nothing is saved).
    """

    EMPTY_TEXT = "등록된 단서가 없습니다"
    release_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("clueList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def _on_marks(self, event: QMouseEvent) -> bool:
        """Whether the click is on a row's check box or its edit/delete marks."""
        item = self.itemAt(event.position().toPoint())
        if item is None:
            return False
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.rect = self.visualItemRect(item)
        cells = self.itemDelegate().columns(option.rect, option.font)
        point = event.position().toPoint()
        return any(cell.contains(point) for cell in (cells.check, cells.edit, cells.remove))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._on_marks(event):
            # The delegate toggles the check box or opens the edit; no row is picked here.
            index = self.indexAt(event.position().toPoint())
            self.itemDelegate().editorEvent(event, self.model(), self.viewOptions(index), index)
            return
        chosen = self.currentItem()
        if chosen is not None and chosen.isSelected():
            self.release_requested.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._on_marks(event):
            index = self.indexAt(event.position().toPoint())
            self.itemDelegate().editorEvent(event, self.model(), self.viewOptions(index), index)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        return

    def viewOptions(self, index) -> QStyleOptionViewItem:  # noqa: N802 - small helper
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.rect = self.visualRect(index)
        return option

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if self.count():
            return
        painter = QPainter(self.viewport())
        painter.setPen(QColor(theme.TEXT_SUB))
        painter.drawText(
            self.viewport().rect().adjusted(0, 12, 0, 0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self.EMPTY_TEXT,
        )
