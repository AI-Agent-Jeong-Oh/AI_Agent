from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from video_agent.contracts import (
    AnalysisClue,
    CaseRecord,
    CaseVideoLink,
    ClueSource,
    InformationCertainty,
)


_CERTAINTY_LABELS = {
    InformationCertainty.CONFIRMED: "확인",
    InformationCertainty.ESTIMATED: "추정",
    InformationCertainty.UNKNOWN: "미확인",
}
_SOURCE_LABELS = {
    ClueSource.USER_STATEMENT: "사용자 진술",
    ClueSource.REPORT: "신고·보고",
    ClueSource.DOCUMENT: "문서",
    ClueSource.OTHER: "기타",
}


class CaseContextPanel(QGroupBox):
    """Minimal case/prior-information UI that a teammate can extend independently."""

    changed = Signal()
    save_requested = Signal()
    load_requested = Signal()
    new_requested = Signal()

    def __init__(self) -> None:
        super().__init__("사건·사전 정보")
        self._loading = False
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        help_label = QLabel(
            "사건명·유형·관리 메모는 저장용이며 모델에 보내지 않습니다. "
            "아래 단서 중 '분석 사용'으로 체크한 항목만 계획에 포함됩니다. "
            "사전 정보는 영상에서 확인된 사실이 아닙니다."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        saved_row = QHBoxLayout()
        self.saved_cases = QComboBox()
        self.load_button = QPushButton("선택 사건 불러오기")
        self.new_button = QPushButton("새 사건")
        self.save_button = QPushButton("사건 저장")
        saved_row.addWidget(self.saved_cases, 1)
        saved_row.addWidget(self.load_button)
        saved_row.addWidget(self.new_button)
        saved_row.addWidget(self.save_button)
        layout.addLayout(saved_row)

        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("예: 문 앞 택배 이동 확인")
        self.type_edit = QLineEdit()
        self.type_edit.setPlaceholderText("관리용 분류 · 모델에는 전송하지 않음")
        occurrence_widget = QWidget()
        occurrence_row = QHBoxLayout(occurrence_widget)
        occurrence_row.setContentsMargins(0, 0, 0, 0)
        self.occurrence_edit = QLineEdit()
        self.occurrence_edit.setPlaceholderText("예: 2026-10-10 20시 전후 · 원본 경과 초와 구분")
        self.occurrence_certainty = QComboBox()
        self._add_certainty_items(self.occurrence_certainty)
        occurrence_row.addWidget(self.occurrence_edit, 1)
        occurrence_row.addWidget(self.occurrence_certainty)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("담당자·관리 메모 등 · 모델에는 전송하지 않음")
        self.notes_edit.setMaximumHeight(70)
        form.addRow("사건명", self.title_edit)
        form.addRow("사건 유형", self.type_edit)
        form.addRow("발생 시각", occurrence_widget)
        form.addRow("관리 메모", self.notes_edit)
        layout.addLayout(form)

        video_form = QFormLayout()
        self.camera_location_edit = QLineEdit()
        self.camera_location_edit.setPlaceholderText("예: 공동현관 앞 CCTV")
        clock_widget = QWidget()
        clock_row = QHBoxLayout(clock_widget)
        clock_row.setContentsMargins(0, 0, 0, 0)
        self.clock_offset = QDoubleSpinBox()
        self.clock_offset.setRange(-86400.0, 86400.0)
        self.clock_offset.setDecimals(1)
        self.clock_offset.setSuffix(" 초")
        self.clock_certainty = QComboBox()
        self._add_certainty_items(self.clock_certainty)
        clock_row.addWidget(self.clock_offset)
        clock_row.addWidget(self.clock_certainty)
        video_form.addRow("카메라 위치", self.camera_location_edit)
        video_form.addRow("카메라 시각 오차", clock_widget)
        layout.addLayout(video_form)

        clue_label = QLabel(
            "분석 단서: 데이터셋 폴더명·라벨 정답을 복사하지 말고, 사용자가 실제로 제공한 정보만 등록하세요."
        )
        clue_label.setWordWrap(True)
        layout.addWidget(clue_label)
        clue_row = QHBoxLayout()
        self.clue_text = QLineEdit()
        self.clue_text.setPlaceholderText("예: 신고자는 사람이 문 앞 상자를 들어 이동했다고 설명함")
        self.clue_source = QComboBox()
        for value, label in _SOURCE_LABELS.items():
            self.clue_source.addItem(label, value)
        self.clue_certainty = QComboBox()
        self._add_certainty_items(self.clue_certainty)
        self.clue_use = QCheckBox("분석 사용")
        self.clue_use.setChecked(True)
        self.add_clue_button = QPushButton("단서 추가")
        clue_row.addWidget(self.clue_text, 1)
        clue_row.addWidget(self.clue_source)
        clue_row.addWidget(self.clue_certainty)
        clue_row.addWidget(self.clue_use)
        clue_row.addWidget(self.add_clue_button)
        layout.addLayout(clue_row)

        self.clue_list = QListWidget()
        self.clue_list.setMinimumHeight(100)
        self.clue_list.setToolTip("체크된 항목만 분석 계획과 모델 입력에 포함됩니다.")
        layout.addWidget(self.clue_list)
        clue_buttons = QHBoxLayout()
        self.remove_clue_button = QPushButton("선택 단서 삭제")
        self.clue_count_label = QLabel("분석 사용 단서: 0개")
        clue_buttons.addWidget(self.remove_clue_button)
        clue_buttons.addStretch()
        clue_buttons.addWidget(self.clue_count_label)
        layout.addLayout(clue_buttons)

    @staticmethod
    def _add_certainty_items(combo: QComboBox) -> None:
        for value, label in _CERTAINTY_LABELS.items():
            combo.addItem(label, value)

    def _connect_signals(self) -> None:
        for edit in (
            self.title_edit,
            self.type_edit,
            self.occurrence_edit,
            self.camera_location_edit,
        ):
            edit.textChanged.connect(self._emit_changed)
        self.notes_edit.textChanged.connect(self._emit_changed)
        self.occurrence_certainty.currentIndexChanged.connect(self._emit_changed)
        self.clock_offset.valueChanged.connect(self._emit_changed)
        self.clock_certainty.currentIndexChanged.connect(self._emit_changed)
        self.add_clue_button.clicked.connect(self._add_clue)
        self.clue_text.returnPressed.connect(self._add_clue)
        self.remove_clue_button.clicked.connect(self._remove_selected_clue)
        self.clue_list.itemChanged.connect(self._clue_item_changed)
        self.save_button.clicked.connect(self.save_requested.emit)
        self.load_button.clicked.connect(self.load_requested.emit)
        self.new_button.clicked.connect(self.new_requested.emit)

    def _emit_changed(self, *_args: object) -> None:
        if not self._loading:
            self.changed.emit()

    def _add_clue(self) -> None:
        text = self.clue_text.text().strip()
        if not text or self.clue_list.count() >= 20:
            return
        clue = AnalysisClue(
            clue_id=f"clue-{uuid4().hex[:12]}",
            text=text,
            source=self.clue_source.currentData(),
            certainty=self.clue_certainty.currentData(),
            use_for_analysis=self.clue_use.isChecked(),
        )
        self._append_clue(clue)
        self.clue_text.clear()
        self._update_clue_count()
        self.changed.emit()

    def _append_clue(self, clue: AnalysisClue) -> None:
        item = QListWidgetItem(self._clue_text(clue))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if clue.use_for_analysis else Qt.CheckState.Unchecked
        )
        item.setData(Qt.ItemDataRole.UserRole, clue)
        self.clue_list.addItem(item)

    def _remove_selected_clue(self) -> None:
        row = self.clue_list.currentRow()
        if row < 0:
            return
        self.clue_list.takeItem(row)
        self._update_clue_count()
        self.changed.emit()

    def _clue_item_changed(self, item: QListWidgetItem) -> None:
        clue = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(clue, AnalysisClue):
            return
        updated = replace(
            clue,
            use_for_analysis=item.checkState() == Qt.CheckState.Checked,
        )
        self.clue_list.blockSignals(True)
        try:
            item.setData(Qt.ItemDataRole.UserRole, updated)
            item.setText(self._clue_text(updated))
        finally:
            self.clue_list.blockSignals(False)
        self._update_clue_count()
        self._emit_changed()

    @staticmethod
    def _clue_text(clue: AnalysisClue) -> str:
        state = "분석 사용" if clue.use_for_analysis else "관리만"
        return (
            f"[{state}] {clue.text} · {_SOURCE_LABELS[clue.source]} · "
            f"{_CERTAINTY_LABELS[clue.certainty]}"
        )

    def _update_clue_count(self) -> None:
        count = sum(item.use_for_analysis for item in self.clues())
        self.clue_count_label.setText(f"분석 사용 단서: {count}개")

    def clues(self) -> tuple[AnalysisClue, ...]:
        values: list[AnalysisClue] = []
        for row in range(self.clue_list.count()):
            item = self.clue_list.item(row)
            clue = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(clue, AnalysisClue):
                values.append(
                    replace(
                        clue,
                        use_for_analysis=item.checkState() == Qt.CheckState.Checked,
                    )
                )
        return tuple(values)

    def video_link(self, video_id: str | None) -> CaseVideoLink | None:
        if not video_id:
            return None
        return CaseVideoLink(
            video_id=video_id,
            camera_location=self.camera_location_edit.text().strip(),
            clock_offset_seconds=self.clock_offset.value(),
            clock_certainty=self.clock_certainty.currentData(),
        )

    def set_record(self, record: CaseRecord, video_id: str | None = None) -> None:
        self._loading = True
        try:
            self.title_edit.setText(record.title)
            self.type_edit.setText(record.case_type)
            self.occurrence_edit.setText(record.occurrence_time_text)
            self._set_combo_value(
                self.occurrence_certainty,
                record.occurrence_time_certainty,
            )
            self.notes_edit.setPlainText(record.management_notes)
            self.clue_list.clear()
            for clue in record.clues:
                self._append_clue(clue)
            link = next(
                (item for item in record.video_links if item.video_id == video_id),
                record.video_links[0] if record.video_links and video_id is None else None,
            )
            self._set_video_link(link)
            self._update_clue_count()
        finally:
            self._loading = False

    def show_video_link(self, record: CaseRecord, video_id: str | None) -> None:
        self._loading = True
        try:
            link = next(
                (item for item in record.video_links if item.video_id == video_id),
                None,
            )
            self._set_video_link(link)
        finally:
            self._loading = False

    def _set_video_link(self, link: CaseVideoLink | None) -> None:
        self.camera_location_edit.setText(link.camera_location if link else "")
        self.clock_offset.setValue(link.clock_offset_seconds if link else 0.0)
        self._set_combo_value(
            self.clock_certainty,
            link.clock_certainty if link else InformationCertainty.UNKNOWN,
        )

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def set_saved_cases(self, records: tuple[CaseRecord, ...], selected_id: str) -> None:
        self.saved_cases.blockSignals(True)
        self.saved_cases.clear()
        for record in records:
            self.saved_cases.addItem(
                f"{record.title} · v{record.revision}",
                record.case_id,
            )
        index = self.saved_cases.findData(selected_id)
        if index >= 0:
            self.saved_cases.setCurrentIndex(index)
        self.saved_cases.blockSignals(False)
        self.load_button.setEnabled(self.saved_cases.count() > 0)

    def selected_case_id(self) -> str | None:
        value = self.saved_cases.currentData()
        return str(value) if value else None

    def set_editing_enabled(self, enabled: bool) -> None:
        for widget in (
            self.saved_cases,
            self.load_button,
            self.new_button,
            self.save_button,
            self.title_edit,
            self.type_edit,
            self.occurrence_edit,
            self.occurrence_certainty,
            self.notes_edit,
            self.camera_location_edit,
            self.clock_offset,
            self.clock_certainty,
            self.clue_text,
            self.clue_source,
            self.clue_certainty,
            self.clue_use,
            self.add_clue_button,
            self.remove_clue_button,
            self.clue_list,
        ):
            widget.setEnabled(enabled)
