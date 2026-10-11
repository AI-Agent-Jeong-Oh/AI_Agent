"""CCTV video list for one case: register several local videos and pick the one to work on.

Paths are kept only for the running app. A case stores video IDs and camera details, so
videos of a reopened case show as "파일 다시 연결 필요" until the same file is chosen again.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from video_agent.ui.video_table import CLOCK_TEXT, CURRENT_ROLE, ENTRY_ROLE, VideoRowDelegate, row_height

VIDEO_ID_ROLE = Qt.ItemDataRole.UserRole + 1

# Columns of the video table; steps ② and ③ keep only the file and its status.
COLUMNS = ("영상", "기준 시각", "분석 상태")
_DETAIL_COLUMNS = (1,)


@dataclass(frozen=True, slots=True)
class VideoEntry:
    video_id: str
    file_name: str | None
    camera_location: str = ""
    duration_seconds: float | None = None
    clock_certainty: InformationCertainty = InformationCertainty.UNKNOWN
    analysis_status: str = "분석 전"
    analyzed: bool = False

    @property
    def linked(self) -> bool:
        return self.file_name is not None


class CaseVideoList(Card):
    add_requested = Signal()
    video_activated = Signal(str)
    relink_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__("CCTV 영상")
        self._entries: tuple[VideoEntry, ...] = ()
        self._current_id: str | None = None
        self._locked = False
        self._refreshing = False
        self._select_only = False
        self._detail_widgets: list[QWidget] = []

        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(12, 12, 12, 12)
        # Count beside the title and "add" at the right of the header, as on the demo page.
        self.count_label = self.aside_label
        self.set_aside("영상 0건")
        self.add_button = QPushButton("영상 추가")
        theme.set_icon(self.add_button, "plus")
        theme.mark_compact(self.add_button)
        self.add_button.setToolTip("이 사건의 CCTV 영상을 하나 이상 고릅니다. 원본은 복사하거나 바꾸지 않습니다.")
        self.add_header_widget(self.add_button)

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

        self.metadata_label = QLabel("영상 정보 확인 전")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        footer = QHBoxLayout()
        self.relink_button = QPushButton("파일 연결")
        self.relink_button.setToolTip("사건에 연결된 영상과 같은 파일을 다시 고릅니다")
        self.remove_button = QPushButton("목록에서 빼기")
        theme.set_icon(self.relink_button, "link")
        theme.set_icon(self.remove_button, "x")
        self.remove_button.setToolTip("사건과의 연결만 끊습니다. 파일은 지우지 않습니다.")
        footer.addWidget(self.metadata_label, 1)
        footer.addWidget(self.relink_button)
        footer.addWidget(self.remove_button)
        layout.addLayout(footer)
        # Spare height (beside the taller settings card in step ②) stays below the card.
        layout.addStretch(1)
        self._layout = layout

        self.add_button.clicked.connect(self.add_requested.emit)
        self.list.currentCellChanged.connect(self._current_changed)
        self.list.itemDoubleClicked.connect(self._item_double_clicked)
        self.relink_button.clicked.connect(self._emit_relink)
        self.remove_button.clicked.connect(self._emit_remove)
        self._update_buttons()

    def add_detail_widget(self, widget: QWidget) -> None:
        """Show per-video fields (camera details) under the list, above the file actions."""
        self._layout.insertWidget(self._layout.count() - 2, widget)
        self._detail_widgets.append(widget)

    def set_select_only(self, select_only: bool) -> None:
        """Steps ② and ③ only pick a registered video: no adding, relinking or removing."""
        self._select_only = select_only
        self.add_button.setVisible(not select_only)
        self.relink_button.setVisible(not select_only)
        self.remove_button.setVisible(not select_only)
        for widget in self._detail_widgets:
            widget.setVisible(not select_only)
        for column in _DETAIL_COLUMNS:
            self.list.setColumnHidden(column, select_only)
        self.list.setAccessibleDescription(
            "목록에서 고르면 그 영상을 엽니다" if select_only else ""
        )

    def apply_scale(self, scaled) -> None:
        self.list.verticalHeader().setDefaultSectionSize(row_height(self.list.font()))
        self.list.setMinimumHeight(scaled(96))
        self.list.setMaximumHeight(scaled(170))

    @property
    def entries(self) -> tuple[VideoEntry, ...]:
        return self._entries

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
                for column, item in enumerate(self._row_items(entry, entry.video_id == current_id)):
                    self.list.setItem(row, column, item)
                if entry.video_id == current_id:
                    select_row = row
                elif select_row < 0 and entry.video_id == keep and current_id is None:
                    select_row = row
            if select_row >= 0:
                self.list.setCurrentCell(select_row, 0)
            else:
                self.list.setCurrentCell(-1, -1)
        finally:
            self._refreshing = False
        linked = sum(entry.linked for entry in entries)
        analyzed = sum(entry.analyzed for entry in entries)
        parts = [f"영상 {len(entries)}건"]
        if linked < len(entries):
            parts.append(f"파일 연결 필요 {len(entries) - linked}건")
        if analyzed:
            parts.append(f"분석 {analyzed}건")
        self.count_label.setText(" · ".join(parts))
        self._update_buttons()

    def set_locked(self, locked: bool) -> None:
        """Block switching, relinking and removing while background work runs."""
        self._locked = locked
        self.list.setEnabled(not locked)
        self._update_buttons()

    @staticmethod
    def _row_items(entry: VideoEntry, current: bool) -> list[QTableWidgetItem]:
        """One row: file, clock check and status (plain text for screen readers)."""
        values = (
            entry.file_name or "파일 다시 연결 필요",
            CLOCK_TEXT[entry.clock_certainty][0],
            entry.analysis_status,
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

    def _entry(self, video_id: str | None) -> VideoEntry | None:
        return next((entry for entry in self._entries if entry.video_id == video_id), None)

    def _current_changed(self, row: int, _column: int, previous_row: int, _previous_column: int) -> None:
        self._update_buttons()
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

    def _emit_relink(self) -> None:
        video_id = self.selected_video_id()
        if video_id is not None:
            self.relink_requested.emit(video_id)

    def _emit_remove(self) -> None:
        video_id = self.selected_video_id()
        if video_id is not None:
            self.remove_requested.emit(video_id)

    def _update_buttons(self) -> None:
        entry = self._entry(self.selected_video_id())
        self.relink_button.setEnabled(not self._locked and entry is not None and not entry.linked)
        self.remove_button.setEnabled(
            not self._locked and entry is not None and entry.video_id != self._current_id
        )
