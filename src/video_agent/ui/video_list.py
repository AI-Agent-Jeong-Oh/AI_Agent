"""CCTV video list for one case: register several local videos and pick the one to work on.

Paths are kept only for the running app. A case stores video IDs and camera details, so
videos of a reopened case show as "파일 다시 연결 필요" until the same file is chosen again.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from video_agent.contracts import InformationCertainty
from video_agent.ui import theme
from video_agent.video.files import format_milliseconds, seconds_to_milliseconds

VIDEO_ID_ROLE = Qt.ItemDataRole.UserRole + 1

_CERTAINTY_TEXT = {
    InformationCertainty.CONFIRMED: "시각 확인",
    InformationCertainty.ESTIMATED: "시각 추정",
    InformationCertainty.UNKNOWN: "시각 미확인",
}


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


class CaseVideoList(QGroupBox):
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

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.count_label = QLabel("등록 0")
        self.count_label.setObjectName("fieldLabel")
        self.add_button = QPushButton("+ 영상 추가")
        self.add_button.setToolTip("이 사건의 CCTV 영상을 하나 이상 고릅니다. 원본은 복사하거나 바꾸지 않습니다.")
        header.addWidget(self.count_label, 1)
        header.addWidget(self.add_button)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setObjectName("videoList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setAccessibleName("사건의 CCTV 영상 목록")
        self.list.setWordWrap(True)
        layout.addWidget(self.list)

        self.metadata_label = QLabel("메타데이터 미확인")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        footer = QHBoxLayout()
        self.relink_button = QPushButton("파일 연결")
        self.relink_button.setToolTip("사건에 연결된 영상과 같은 파일을 다시 고릅니다")
        self.remove_button = QPushButton("목록에서 빼기")
        self.remove_button.setToolTip("사건과의 연결만 끊습니다. 파일은 지우지 않습니다.")
        footer.addWidget(self.metadata_label, 1)
        footer.addWidget(self.relink_button)
        footer.addWidget(self.remove_button)
        layout.addLayout(footer)
        self._layout = layout

        self.add_button.clicked.connect(self.add_requested.emit)
        self.list.currentItemChanged.connect(self._current_changed)
        self.list.itemDoubleClicked.connect(self._item_double_clicked)
        self.relink_button.clicked.connect(self._emit_relink)
        self.remove_button.clicked.connect(self._emit_remove)
        self._update_buttons()

    def add_detail_widget(self, widget: QWidget) -> None:
        """Show per-video fields (camera details) under the list, above the file actions."""
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def apply_scale(self, scaled) -> None:
        self.list.setMinimumHeight(scaled(64))
        self.list.setMaximumHeight(scaled(120))

    @property
    def entries(self) -> tuple[VideoEntry, ...]:
        return self._entries

    def selected_video_id(self) -> str | None:
        item = self.list.currentItem()
        value = item.data(VIDEO_ID_ROLE) if item is not None else None
        return value if isinstance(value, str) else None

    def set_entries(self, entries: tuple[VideoEntry, ...], current_id: str | None) -> None:
        """Redraw the list and select the video that is currently open."""
        self._entries = entries
        self._current_id = current_id
        self._refreshing = True
        try:
            keep = self.selected_video_id()
            self.list.clear()
            select_row = -1
            for row, entry in enumerate(entries):
                item = QListWidgetItem(self._item_text(entry, entry.video_id == current_id))
                item.setData(VIDEO_ID_ROLE, entry.video_id)
                item.setToolTip(f"영상 ID {entry.video_id}")
                if not entry.linked:
                    item.setForeground(QColor(theme.TEXT_FAINT))
                self.list.addItem(item)
                if entry.video_id == current_id:
                    select_row = row
                elif select_row < 0 and entry.video_id == keep and current_id is None:
                    select_row = row
            self.list.setCurrentRow(select_row)
        finally:
            self._refreshing = False
        linked = sum(entry.linked for entry in entries)
        analyzed = sum(entry.analyzed for entry in entries)
        self.count_label.setText(
            f"등록 {len(entries)} · 파일 연결 {linked} · 분석 결과 {analyzed}"
        )
        self._update_buttons()

    def set_locked(self, locked: bool) -> None:
        """Block switching, relinking and removing while background work runs."""
        self._locked = locked
        self.list.setEnabled(not locked)
        self._update_buttons()

    @staticmethod
    def _item_text(entry: VideoEntry, current: bool) -> str:
        name = entry.file_name or "파일 다시 연결 필요"
        mark = "▶ " if current else ""
        location = entry.camera_location or "위치 미입력"
        duration = (
            format_milliseconds(seconds_to_milliseconds(entry.duration_seconds))
            if entry.duration_seconds is not None
            else "길이 미확인"
        )
        details = f"{location} · {duration} · {_CERTAINTY_TEXT[entry.clock_certainty]}"
        return f"{mark}{name}\n{details}\n{entry.analysis_status}"

    def _entry(self, video_id: str | None) -> VideoEntry | None:
        return next((entry for entry in self._entries if entry.video_id == video_id), None)

    def _current_changed(self, current: QListWidgetItem | None, _previous) -> None:
        self._update_buttons()
        if self._refreshing or current is None:
            return
        entry = self._entry(current.data(VIDEO_ID_ROLE))
        if entry is not None and entry.linked and entry.video_id != self._current_id:
            self.video_activated.emit(entry.video_id)

    def _item_double_clicked(self, item: QListWidgetItem) -> None:
        entry = self._entry(item.data(VIDEO_ID_ROLE))
        if entry is not None and not entry.linked and not self._locked:
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
