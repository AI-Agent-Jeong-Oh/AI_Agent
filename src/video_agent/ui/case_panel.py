from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
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
from video_agent.ui.clue_delegate import ClueItemDelegate


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
CERTAINTY_HELP = (
    "확실성은 정보를 준 사람이나 자료가 얼마나 분명하게 말했는지입니다. "
    "'확인'도 영상이나 수사로 사실이 검증됐다는 뜻은 아닙니다."
)


class CaseContextPanel(QGroupBox):
    """Minimal case/prior-information UI that a teammate can extend independently."""

    changed = Signal()
    save_requested = Signal()
    load_requested = Signal()
    new_requested = Signal()

    def __init__(self) -> None:
        super().__init__("사건·사전 정보")
        self._video_open = False
        self._editing_enabled = True
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
        # Fixed so the clue list, not the memo, gives way when the step is short.
        self.notes_edit.setFixedHeight(70)
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

        divider = QFrame()
        divider.setObjectName("sectionDivider")
        layout.addWidget(divider)
        title_row = QHBoxLayout()
        clue_title = QLabel("분석 단서")
        clue_title.setObjectName("subsectionTitle")
        clue_aside = QLabel("체크한 단서만 분석 계획과 모델 입력에 포함")
        clue_aside.setObjectName("subsectionAside")
        title_row.addWidget(clue_title)
        title_row.addSpacing(8)
        title_row.addWidget(clue_aside, 0, Qt.AlignmentFlag.AlignBottom)
        title_row.addStretch(1)
        layout.addLayout(title_row)
        clue_help = QLabel(
            "데이터셋 폴더명·라벨 정답을 복사하지 말고, 사용자가 실제로 제공한 정보만 등록하세요."
        )
        clue_help.setObjectName("subsectionHelp")
        clue_help.setWordWrap(True)
        layout.addWidget(clue_help)

        editor = QWidget()
        editor.setObjectName("clueEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.clue_text = QLineEdit()
        self.clue_text.setPlaceholderText("예: 신고자는 사람이 문 앞 상자를 들어 이동했다고 설명함")
        self.clue_text.setAccessibleName("단서 문장")
        text_row = QHBoxLayout()
        editor_layout.addLayout(text_row)
        clue_row = QHBoxLayout()
        source_label = QLabel("출처")
        source_label.setObjectName("fieldLabel")
        self.clue_source = QComboBox()
        self.clue_source.setAccessibleName("출처")
        for value, label in _SOURCE_LABELS.items():
            self.clue_source.addItem(label, value)
        certainty_label = QLabel("확실성")
        certainty_label.setObjectName("fieldLabel")
        certainty_label.setToolTip(CERTAINTY_HELP)
        self.clue_certainty = QComboBox()
        self.clue_certainty.setAccessibleName("확실성")
        self.clue_certainty.setToolTip(CERTAINTY_HELP)
        self._add_certainty_items(self.clue_certainty)
        self.clue_use = QCheckBox("분석 사용")
        self.clue_use.setChecked(True)
        self.cancel_clue_edit_button = QPushButton("선택 해제")
        self.cancel_clue_edit_button.setVisible(False)
        self.add_clue_button = QPushButton("단서 추가")
        clue_row.addWidget(source_label)
        clue_row.addWidget(self.clue_source)
        clue_row.addSpacing(8)
        clue_row.addWidget(certainty_label)
        clue_row.addWidget(self.clue_certainty)
        clue_row.addSpacing(8)
        clue_row.addWidget(self.clue_use)
        clue_row.addStretch(1)
        text_row.addWidget(self.clue_text, 1)
        text_row.addWidget(self.cancel_clue_edit_button)
        text_row.addWidget(self.add_clue_button)
        editor_layout.addLayout(clue_row)
        self.clue_edit_label = QLabel(
            "선택한 단서를 고치는 중 · 분석 사용 여부는 목록의 체크박스로 바꿉니다"
        )
        self.clue_edit_label.setObjectName("subsectionHelp")
        self.clue_edit_label.setWordWrap(True)
        self.clue_edit_label.setVisible(False)
        editor_layout.addWidget(self.clue_edit_label)
        layout.addWidget(editor)

        self.clue_list = QListWidget()
        self.clue_list.setMinimumHeight(100)
        # Take the space left in the step instead of Qt's default list height, so the
        # larger clue rows scroll inside the list rather than the whole step.
        self.clue_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.clue_list.setToolTip(
            "체크된 항목만 분석 계획과 모델 입력에 포함됩니다. 단서를 고르면 위에서 고칠 수 있습니다."
        )
        self.clue_list.setAccessibleName("분석 단서 목록")
        self.clue_list.setItemDelegate(
            ClueItemDelegate(_SOURCE_LABELS, _CERTAINTY_LABELS, self.clue_list)
        )
        # Rows wrap to the list width, so their heights follow it.
        self.clue_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.clue_list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.clue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.clue_list, 1)
        clue_buttons = QHBoxLayout()
        self.remove_clue_button = QPushButton("선택 단서 삭제")
        self.clue_count_label = QLabel("분석 사용 0 / 전체 0")
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
        self.cancel_clue_edit_button.clicked.connect(self._cancel_clue_edit)
        self.remove_clue_button.clicked.connect(self._remove_selected_clue)
        self.clue_list.itemChanged.connect(self._clue_item_changed)
        self.clue_list.currentRowChanged.connect(self._select_clue)
        self.save_button.clicked.connect(self.save_requested.emit)
        self.load_button.clicked.connect(self.load_requested.emit)
        self.new_button.clicked.connect(self.new_requested.emit)

    def _emit_changed(self, *_args: object) -> None:
        if not self._loading:
            self.changed.emit()

    def _add_clue(self) -> None:
        """Add a new clue, or save the selected one when a clue is being edited."""
        text = self.clue_text.text().strip()
        if not text:
            return
        if self._editing_clue_row() is not None:
            self._save_clue_edit(text)
            return
        if self.clue_list.count() >= 20:
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

    def _editing_clue_row(self) -> int | None:
        row = self.clue_list.currentRow()
        return row if row >= 0 and self.clue_list.item(row).isSelected() else None

    def _select_clue(self, row: int) -> None:
        """Fill the input row from the chosen clue so its text, source and certainty can change."""
        item = self.clue_list.item(row) if row >= 0 else None
        clue = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        editing = isinstance(clue, AnalysisClue)
        if editing:
            self.clue_text.setText(clue.text)
            self._set_combo_value(self.clue_source, clue.source)
            self._set_combo_value(self.clue_certainty, clue.certainty)
        else:
            self.clue_text.clear()
        self.add_clue_button.setText("변경 저장" if editing else "단서 추가")
        self.cancel_clue_edit_button.setVisible(editing)
        self.clue_edit_label.setVisible(editing)
        self.clue_use.setEnabled(not editing and self._editing_enabled)

    def _save_clue_edit(self, text: str) -> None:
        row = self.clue_list.currentRow()
        item = self.clue_list.item(row)
        clue = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(clue, AnalysisClue):
            return
        # Same clue ID; analysis use stays whatever the list check box says.
        updated = replace(
            clue,
            text=text,
            source=self.clue_source.currentData(),
            certainty=self.clue_certainty.currentData(),
            use_for_analysis=item.checkState() == Qt.CheckState.Checked,
        )
        self.clue_list.blockSignals(True)
        try:
            item.setData(Qt.ItemDataRole.UserRole, updated)
            item.setText(self._clue_text(updated))
        finally:
            self.clue_list.blockSignals(False)
        self.clue_list.doItemsLayout()
        self._cancel_clue_edit()
        self._update_clue_count()
        self.changed.emit()

    def _cancel_clue_edit(self) -> None:
        self.clue_list.clearSelection()
        self.clue_list.setCurrentRow(-1)
        self._select_clue(-1)

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
        self._cancel_clue_edit()
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
        """Plain text for screen readers; the list draws the two-line form itself."""
        unused = ", 분석에 사용 안 함" if not clue.use_for_analysis else ""
        return (
            f"{clue.text} — 출처 {_SOURCE_LABELS[clue.source]}, "
            f"확실성 {_CERTAINTY_LABELS[clue.certainty]}{unused}"
        )

    def _update_clue_count(self) -> None:
        clues = self.clues()
        count = sum(item.use_for_analysis for item in clues)
        self.clue_count_label.setText(f"분석 사용 {count} / 전체 {len(clues)}")

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
            self._cancel_clue_edit()
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
        link = next(
            (item for item in record.video_links if item.video_id == video_id),
            None,
        )
        self.show_link(link)

    def show_link(self, link: CaseVideoLink | None) -> None:
        """Show one video's camera details without reporting an edit."""
        self._loading = True
        try:
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

    def set_video_open(self, video_open: bool) -> None:
        """Camera details belong to one video; without an open video they stay empty."""
        self._video_open = video_open
        if not video_open:
            self.show_link(None)
        self._update_camera_fields()

    def _update_camera_fields(self) -> None:
        enabled = self._video_open and self._editing_enabled
        for widget in (self.camera_location_edit, self.clock_offset, self.clock_certainty):
            widget.setEnabled(enabled)

    def set_editing_enabled(self, enabled: bool) -> None:
        self._editing_enabled = enabled
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
            self.clue_text,
            self.clue_source,
            self.clue_certainty,
            self.clue_use,
            self.add_clue_button,
            self.cancel_clue_edit_button,
            self.remove_clue_button,
            self.clue_list,
        ):
            widget.setEnabled(enabled)
        self.clue_use.setEnabled(enabled and self._editing_clue_row() is None)
        self._update_camera_fields()
