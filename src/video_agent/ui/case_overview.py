"""Read-only list of every camera's candidates in one case, for finding and jumping to them.

Reviewer notes are still entered in the review panel for one result at a time; this list
only shows saved judgements so the whole case can be scanned at once.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QPushButton

from video_agent.video.files import format_milliseconds, seconds_to_milliseconds

ROW_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass(frozen=True, slots=True)
class OverviewRow:
    video_id: str
    job_id: str
    segment_index: int
    camera: str
    start_sec: float
    end_sec: float
    summary: str
    judgement: str
    bookmarked: bool = False


class CaseCandidateOverview(QListWidget):
    """Collapsed by default; the toggle button shows the case-wide counts."""

    candidate_activated = Signal(str, str, int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("caseOverview")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setWordWrap(True)
        self.setAccessibleName("사건 전체 후보 목록")
        self.setToolTip("두 번 누르면 그 카메라의 결과를 열고 해당 후보를 선택합니다")
        self.toggle_button = QPushButton("▸ 사건 전체 후보")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setObjectName("overviewToggle")
        self.toggle_button.setAccessibleName("사건 전체 후보 목록 펼치기")
        self.toggle_button.toggled.connect(self._toggled)
        self.itemActivated.connect(self._activated)
        self.setVisible(False)
        self._rows: tuple[OverviewRow, ...] = ()
        self._judgement_order: tuple[str, ...] = ()

    @property
    def rows(self) -> tuple[OverviewRow, ...]:
        return self._rows

    def apply_scale(self, scaled) -> None:
        self.setMinimumHeight(scaled(70))
        self.setMaximumHeight(scaled(150))

    def set_rows(self, rows: tuple[OverviewRow, ...], judgement_order: tuple[str, ...]) -> None:
        self._rows = rows
        self._judgement_order = judgement_order
        self.clear()
        for row in rows:
            start = format_milliseconds(seconds_to_milliseconds(row.start_sec))
            end = format_milliseconds(seconds_to_milliseconds(row.end_sec))
            mark = " ★" if row.bookmarked else ""
            item = QListWidgetItem(
                f"[{row.judgement}]{mark} {row.camera} · 후보 #{row.segment_index + 1} · "
                f"{start}–{end}\n{row.summary}"
            )
            item.setData(ROW_ROLE, row)
            self.addItem(item)
        self._update_toggle_text()

    def _update_toggle_text(self) -> None:
        arrow = "▾" if self.toggle_button.isChecked() else "▸"
        counts = {label: 0 for label in self._judgement_order}
        for row in self._rows:
            counts[row.judgement] = counts.get(row.judgement, 0) + 1
        parts = [f"{label} {count}" for label, count in counts.items() if count]
        summary = f"사건 전체 후보 {len(self._rows)}" + (f" · {' · '.join(parts)}" if parts else "")
        self.toggle_button.setText(f"{arrow} 전체 후보 {len(self._rows)}")
        self.toggle_button.setToolTip(summary)
        self.toggle_button.setAccessibleDescription(summary)

    def _toggled(self, checked: bool) -> None:
        self.setVisible(checked)
        self.toggle_button.setAccessibleName(
            "사건 전체 후보 목록 접기" if checked else "사건 전체 후보 목록 펼치기"
        )
        self._update_toggle_text()

    def _activated(self, item: QListWidgetItem) -> None:
        row = item.data(ROW_ROLE)
        if isinstance(row, OverviewRow):
            self.candidate_activated.emit(row.video_id, row.job_id, row.segment_index)
