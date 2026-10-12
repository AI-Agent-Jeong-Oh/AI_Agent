"""CCTV video list for one case: register several local videos and pick the one to work on.

Paths are kept only for the running app. A case stores video IDs and camera details, so
videos of a reopened case show as "파일 다시 연결 필요" until the same file is chosen again.

As on the demo page, the table comes first (no card title) and each row carries its own
small actions: edit the camera clock difference, link the file again, remove from the case.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_agent.contracts import InformationCertainty
from video_agent.ui import theme
from video_agent.ui.card import Card
from video_agent.ui.popover import place_below
from video_agent.ui.video_table import (
    CLOCK_TEXT,
    CURRENT_ROLE,
    ENTRY_ROLE,
    VideoRowDelegate,
    clock_text,
    row_height,
)

VIDEO_ID_ROLE = Qt.ItemDataRole.UserRole + 1

# Columns of the video table; steps ② and ③ keep only the file and its status.
COLUMNS = ("영상", "기준 시각", "분석 상태", "")
_ACTIONS_COLUMN = 3
_DETAIL_COLUMNS = (1, _ACTIONS_COLUMN)


@dataclass(frozen=True, slots=True)
class VideoEntry:
    video_id: str
    file_name: str | None
    camera_location: str = ""
    duration_seconds: float | None = None
    clock_certainty: InformationCertainty = InformationCertainty.UNKNOWN
    analysis_status: str = "분석 전"
    analyzed: bool = False
    clock_offset_seconds: float = 0.0

    @property
    def linked(self) -> bool:
        return self.file_name is not None


class ClockEditor(QFrame):
    """Small popup to enter one camera's clock difference and how sure it is."""

    applied = Signal(float, object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("datePopover")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("popoverTitle")
        row = QHBoxLayout()
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-86400.0, 86400.0)
        self.offset.setDecimals(1)
        self.offset.setSuffix(" 초")
        self.offset.setAccessibleName("카메라 시각 오차")
        theme.polish_spin_box(self.offset)
        self.certainty = QComboBox()
        self.certainty.setAccessibleName("시각 오차 확실성")
        for value, (label, _color) in CLOCK_TEXT.items():
            self.certainty.addItem(label, value)
        theme.polish_combo(self.certainty)
        label = QLabel("시각 오차")
        label.setObjectName("fieldLabel")
        row.addWidget(label)
        row.addWidget(self.offset, 1)
        row.addWidget(self.certainty)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("취소")
        apply = QPushButton("적용")
        for button in (cancel, apply):
            theme.mark_compact(button)
        theme.mark_primary(apply)
        actions.addWidget(cancel)
        actions.addWidget(apply)
        layout.addWidget(self.title)
        layout.addLayout(row)
        layout.addLayout(actions)
        cancel.clicked.connect(self.close)
        apply.clicked.connect(self._apply)

    def open_for(self, entry: VideoEntry, anchor: QWidget) -> None:
        self.title.setText(entry.file_name or "파일 다시 연결 필요")
        self.offset.setValue(entry.clock_offset_seconds)
        self.certainty.setCurrentIndex(max(0, self.certainty.findData(entry.clock_certainty)))
        self.move(place_below(self, anchor))
        self.show()
        self.offset.setFocus()

    def _apply(self) -> None:
        self.applied.emit(self.offset.value(), InformationCertainty(self.certainty.currentData()))
        self.close()


class CaseVideoList(Card):
    add_requested = Signal()
    video_activated = Signal(str)
    relink_requested = Signal(str)
    remove_requested = Signal(str)
    clock_changed = Signal(str, float, object)

    def __init__(self) -> None:
        super().__init__("CCTV 영상")
        self.hide_header()
        self._entries: tuple[VideoEntry, ...] = ()
        self._current_id: str | None = None
        self._locked = False
        self._refreshing = False
        self._select_only = False
        self._editing_id: str | None = None

        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(12, 8, 12, 12)

        self.list = QTableWidget(0, len(COLUMNS))
        self.list.setObjectName("videoList")
        self.list.setHorizontalHeaderLabels(COLUMNS)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setShowGrid(False)
        self.list.setWordWrap(False)
        # Rows are drawn by VideoRowDelegate: name with a detail line, dot-led statuses.
        self.list.setItemDelegate(VideoRowDelegate(self.list))
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.verticalHeader().setVisible(False)
        self.list.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.list.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.list.setAccessibleName("사건의 CCTV 영상 목록")
        layout.addWidget(self.list)

        # One short line under the table: add a video, and the open video's details.
        footer = QHBoxLayout()
        self.add_button = QPushButton("영상 추가")
        theme.set_icon(self.add_button, "plus")
        theme.mark_compact(self.add_button)
        self.add_button.setToolTip("이 사건의 CCTV 영상을 하나 이상 고릅니다. 원본은 복사하거나 바꾸지 않습니다.")
        self.metadata_label = QLabel("영상 정보 확인 전")
        self.metadata_label.setObjectName("subsectionHelp")
        self.metadata_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        footer.addWidget(self.add_button)
        footer.addStretch(1)
        footer.addWidget(self.metadata_label)
        layout.addLayout(footer)
        # Spare height (beside the taller settings card in step ②) stays below the card.
        layout.addStretch(1)

        self._clock_editor = ClockEditor(self)
        self._clock_editor.applied.connect(self._clock_applied)
        self.add_button.clicked.connect(self.add_requested.emit)
        self.list.currentCellChanged.connect(self._current_changed)
        self.list.itemDoubleClicked.connect(self._item_double_clicked)

    def set_select_only(self, select_only: bool) -> None:
        """Steps ② and ③ only pick a registered video: no adding, editing or removing."""
        self._select_only = select_only
        self.add_button.setVisible(not select_only)
        for column in _DETAIL_COLUMNS:
            self.list.setColumnHidden(column, select_only)

    def apply_scale(self, scaled) -> None:
        self.list.verticalHeader().setDefaultSectionSize(row_height(self.list.font()))
        self._max_table_height = scaled(190)
        self._fit_table_height()

    def _fit_table_height(self) -> None:
        """As tall as its rows (at least one, at most the old height, then it scrolls),
        so the space goes to the video below."""
        header = self.list.horizontalHeader().sizeHint().height()
        rows = max(1, self.list.rowCount())
        height = header + rows * self.list.verticalHeader().defaultSectionSize() + 2
        self.list.setFixedHeight(min(height, getattr(self, "_max_table_height", 190)))

    @property
    def entries(self) -> tuple[VideoEntry, ...]:
        return self._entries

    def summary_text(self) -> str:
        """Count of videos (and what needs a file), for the table's accessible description."""
        linked = sum(entry.linked for entry in self._entries)
        analyzed = sum(entry.analyzed for entry in self._entries)
        parts = [f"영상 {len(self._entries)}건"]
        if linked < len(self._entries):
            parts.append(f"파일 연결 필요 {len(self._entries) - linked}건")
        if analyzed:
            parts.append(f"분석 {analyzed}건")
        return " · ".join(parts)

    def selected_video_id(self) -> str | None:
        row = self.list.currentRow()
        item = self.list.item(row, 0) if row >= 0 else None
        value = item.data(VIDEO_ID_ROLE) if item is not None else None
        return value if isinstance(value, str) else None

    def set_entries(self, entries: tuple[VideoEntry, ...], current_id: str | None) -> None:
        """Redraw the list and select the video that is currently open."""
        self._entries = entries
        self._current_id = current_id
        self._refreshing = True
        try:
            keep = self.selected_video_id()
            self.list.setRowCount(0)
            self.list.setRowCount(len(entries))
            select_row = -1
            for row, entry in enumerate(entries):
                current = entry.video_id == current_id
                for column, item in enumerate(self._row_items(entry, current)):
                    self.list.setItem(row, column, item)
                self.list.setCellWidget(row, _ACTIONS_COLUMN, self._row_actions(entry, current))
                if current:
                    select_row = row
                elif select_row < 0 and entry.video_id == keep and current_id is None:
                    select_row = row
            if select_row >= 0:
                self.list.setCurrentCell(select_row, 0)
            else:
                self.list.setCurrentCell(-1, -1)
        finally:
            self._refreshing = False
        self._fit_table_height()
        description = self.summary_text()
        if self._select_only:
            description += " · 목록에서 고르면 그 영상을 엽니다"
        self.list.setAccessibleDescription(description)

    def set_locked(self, locked: bool) -> None:
        """Block switching, relinking and removing while background work runs."""
        self._locked = locked
        self.list.setEnabled(not locked)

    @staticmethod
    def _row_items(entry: VideoEntry, current: bool) -> list[QTableWidgetItem]:
        """One row: file, clock check and status (plain text for screen readers)."""
        values = (
            entry.file_name or "파일 다시 연결 필요",
            clock_text(entry.clock_certainty, entry.clock_offset_seconds),
            entry.analysis_status,
            "",
        )
        items = []
        for value in values:
            item = QTableWidgetItem(value)
            item.setData(ENTRY_ROLE, entry)
            item.setData(CURRENT_ROLE, current)
            item.setToolTip(value)
            items.append(item)
        name = items[0]
        name.setData(VIDEO_ID_ROLE, entry.video_id)
        name.setToolTip(f"{values[0]}\n영상 ID {entry.video_id}")
        if current:
            name.setData(Qt.ItemDataRole.AccessibleDescriptionRole, "재생 중인 영상")
        return items

    def _row_actions(self, entry: VideoEntry, current: bool) -> QWidget:
        """Icon buttons at the end of a row, like the demo's edit and delete marks."""
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(2)
        name = entry.file_name or "연결 안 된 영상"
        actions = [("pencil", f"{name} 시각 오차 입력", lambda: self._edit_clock(entry, box), True)]
        if not entry.linked:
            actions.append(
                (
                    "link",
                    f"{name} 같은 파일 다시 연결",
                    lambda: self.relink_requested.emit(entry.video_id),
                    True,
                )
            )
        actions.append(
            (
                "x",
                "지금 연 영상은 뺄 수 없습니다"
                if current
                else f"{name} 사건 목록에서 빼기 (파일은 지우지 않음)",
                lambda: self.remove_requested.emit(entry.video_id),
                not current,
            )
        )
        for icon, tip, handler, enabled in actions:
            button = QPushButton()
            button.setObjectName("rowAction")
            theme.set_icon(button, icon)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            # Locking disables the whole list (and so these buttons); disabling them here
            # too would leave them off after the list is unlocked.
            button.setEnabled(enabled)
            button.clicked.connect(handler)
            row.addWidget(button)
        return box

    def _edit_clock(self, entry: VideoEntry, anchor: QWidget) -> None:
        self._editing_id = entry.video_id
        self._clock_editor.open_for(entry, anchor)

    def _clock_applied(self, offset: float, certainty: object) -> None:
        if self._editing_id is not None:
            self.clock_changed.emit(self._editing_id, offset, certainty)

    def _entry(self, video_id: str | None) -> VideoEntry | None:
        return next((entry for entry in self._entries if entry.video_id == video_id), None)

    def _current_changed(self, row: int, _column: int, previous_row: int, _previous_column: int) -> None:
        if self._refreshing or row < 0 or row == previous_row:
            return
        entry = self._entry(self.selected_video_id())
        if entry is not None and entry.linked and entry.video_id != self._current_id:
            self.video_activated.emit(entry.video_id)

    def _item_double_clicked(self, item: QTableWidgetItem) -> None:
        name = self.list.item(item.row(), 0)
        entry = self._entry(name.data(VIDEO_ID_ROLE) if name is not None else None)
        if entry is not None and not entry.linked and not self._locked and not self._select_only:
            self.relink_requested.emit(entry.video_id)
