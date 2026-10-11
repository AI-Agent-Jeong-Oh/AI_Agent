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
    QMenu,
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
from video_agent.ui import theme
from video_agent.ui.card import Card
from video_agent.ui.clue_delegate import ClueItemDelegate
from video_agent.ui.disclosure import DisclosureButton
from video_agent.ui.datetime_field import DateTimeField
from video_agent.ui.flow_layout import FlowRow
from video_agent.ui.popover import info_mark
from video_agent.ui.wrap_label import WrapLabel


CERTAINTY_LABELS = {
    InformationCertainty.CONFIRMED: "확인",
    InformationCertainty.ESTIMATED: "추정",
    InformationCertainty.UNKNOWN: "미확인",
}
SOURCE_LABELS = {
    ClueSource.USER_STATEMENT: "사용자 진술",
    ClueSource.REPORT: "신고·보고",
    ClueSource.DOCUMENT: "문서",
    ClueSource.OTHER: "기타",
}
CERTAINTY_HELP = (
    "확실성은 정보를 준 사람이나 자료가 얼마나 분명하게 말했는지입니다. "
    "'확인'도 영상이나 수사로 사실이 검증됐다는 뜻은 아닙니다."
)


class CaseContextPanel(Card):
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
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(12, 12, 12, 12)
        # Explanation behind a mark next to the title; loading (with "new case") is one
        # menu beside saving, at the right of the header.
        self.add_header_widget(
            info_mark(
                "사건명·유형·관리 메모는 저장용이며 모델에 보내지 않습니다. "
                "아래 단서 중 '분석 사용'으로 체크한 항목만 계획에 포함됩니다. "
                "사전 정보는 영상에서 확인된 사실이 아닙니다.",
                "사건 정보 안내",
            ),
            after_title=True,
        )
        self._saved_cases: tuple[tuple[str, str], ...] = ()
        self._selected_case_id: str | None = None
        self.load_button = QPushButton("불러오기")
        theme.set_icon(self.load_button, "folder-open")
        self.load_button.setToolTip("저장한 사건을 불러오거나 새 사건을 시작합니다")
        self.load_button.setAccessibleName("사건 불러오기 또는 새 사건")
        self.save_button = QPushButton("사건 저장")
        theme.set_icon(self.save_button, "save")
        for button in (self.load_button, self.save_button):
            theme.mark_compact(button)
            self.add_header_widget(button)

        form = QFormLayout()
        theme.polish_form(form)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("예: 문 앞 택배 이동 확인")
        self.type_edit = QLineEdit()
        self.type_edit.setPlaceholderText("예: 절도")
        occurrence_widget = QWidget()
        occurrence_row = QHBoxLayout(occurrence_widget)
        occurrence_row.setContentsMargins(0, 0, 0, 0)
        # Picked from a calendar that opens when the field is clicked; how sure it is goes
        # in the certainty choice beside it.
        self.occurrence_edit = DateTimeField()
        self.occurrence_edit.setPlaceholderText("날짜와 시각 선택")
        self.occurrence_edit.setAccessibleName("발생 시각")
        self.occurrence_certainty = QComboBox()
        self._add_certainty_items(self.occurrence_certainty)
        occurrence_row.addWidget(self.occurrence_edit, 1)
        occurrence_row.addWidget(self.occurrence_certainty)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("담당자, 처리 메모 등")
        # Two lines; fixed so the clue list, not the memo, gives way when the step is short.
        self.notes_edit.setFixedHeight(64)
        # Type and memo are kept for management only (never sent to the model), so they
        # are folded; they open by themselves when a loaded case has them filled in.
        self.admin_toggle = DisclosureButton("사건 유형·관리 메모")
        form.addRow("사건명", self.title_edit)
        form.addRow("발생 시각", occurrence_widget)
        form.addRow(self.admin_toggle)
        form.addRow("사건 유형", self.type_edit)
        form.addRow("관리 메모", self.notes_edit)
        self._form = form
        layout.addLayout(form)
        self._update_admin_rows()

        # Camera details belong to the open video, so the CCTV card shows this row under
        # its list (see camera_fields); the panel still owns the values and signals.
        self.camera_location_edit = QLineEdit()
        self.camera_location_edit.setPlaceholderText("예: 공동현관 앞 CCTV")
        self.camera_location_edit.setAccessibleName("카메라 위치")
        self.camera_location_edit.setMinimumWidth(200)
        self.clock_offset = QDoubleSpinBox()
        self.clock_offset.setRange(-86400.0, 86400.0)
        self.clock_offset.setDecimals(1)
        self.clock_offset.setSuffix(" 초")
        self.clock_offset.setAccessibleName("카메라 시각 오차")
        self.clock_certainty = QComboBox()
        self.clock_certainty.setAccessibleName("카메라 시각 확실성")
        self._add_certainty_items(self.clock_certainty)
        self.camera_fields = FlowRow(spacing=12)
        self.camera_fields.setAccessibleName("선택한 영상의 카메라 정보")
        location_group = QWidget()
        location_row = QHBoxLayout(location_group)
        location_row.setContentsMargins(0, 0, 0, 0)
        location_label = QLabel("카메라 위치")
        location_label.setObjectName("fieldLabel")
        location_row.addWidget(location_label)
        location_row.addWidget(self.camera_location_edit)
        clock_group = QWidget()
        clock_row = QHBoxLayout(clock_group)
        clock_row.setContentsMargins(0, 0, 0, 0)
        clock_label = QLabel("시각 오차")
        clock_label.setObjectName("fieldLabel")
        clock_row.addWidget(clock_label)
        clock_row.addWidget(self.clock_offset)
        clock_row.addWidget(self.clock_certainty)
        self.camera_fields.flow.addWidget(location_group)
        self.camera_fields.flow.addWidget(clock_group)

        divider = QFrame()
        divider.setObjectName("sectionDivider")
        layout.addWidget(divider)
        title_row = QHBoxLayout()
        clue_title = QLabel("분석 단서")
        clue_title.setObjectName("subsectionTitle")
        clue_aside = WrapLabel("체크한 단서만 분석에 사용")
        clue_aside.setObjectName("subsectionAside")
        title_row.addWidget(clue_title)
        title_row.addSpacing(8)
        title_row.addWidget(clue_aside, 1, Qt.AlignmentFlag.AlignBottom)
        title_row.addWidget(
            info_mark(
                "데이터셋 폴더명·라벨 정답을 복사하지 말고, 사용자가 실제로 제공한 정보만 등록하세요.",
                "분석 단서 안내",
            ),
            0,
            Qt.AlignmentFlag.AlignBottom,
        )
        self.clue_count_label = QLabel("분석 사용 0 / 전체 0")
        self.clue_count_label.setObjectName("subsectionAside")
        title_row.addWidget(self.clue_count_label, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(title_row)

        editor = QWidget()
        editor.setObjectName("clueEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.clue_text = QLineEdit()
        self.clue_text.setPlaceholderText("예: 신고자는 사람이 문 앞 상자를 들어 이동했다고 설명함")
        self.clue_text.setAccessibleName("단서 문장")
        text_row = QHBoxLayout()
        editor_layout.addLayout(text_row)
        clue_row = FlowRow(spacing=16)
        source_label = QLabel("출처")
        source_label.setObjectName("fieldLabel")
        self.clue_source = QComboBox()
        self.clue_source.setAccessibleName("출처")
        for value, label in SOURCE_LABELS.items():
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
        # Like the demo page: deleting is offered while a clue is selected for editing.
        self.remove_clue_button = QPushButton("삭제")
        self.remove_clue_button.setAccessibleName("선택 단서 삭제")
        self.remove_clue_button.setVisible(False)
        self.add_clue_button = QPushButton("단서 추가")
        theme.set_icon(self.add_clue_button, "plus")
        theme.set_icon(self.remove_clue_button, "trash-2")
        # Label and choice stay together; the groups wrap at large text sizes.
        for parts in ((source_label, self.clue_source), (certainty_label, self.clue_certainty)):
            group = QWidget()
            group_layout = QHBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            for part in parts:
                group_layout.addWidget(part)
            clue_row.flow.addWidget(group)
        clue_row.flow.addWidget(self.clue_use)
        text_row.addWidget(self.clue_text, 1)
        text_row.addWidget(self.remove_clue_button)
        text_row.addWidget(self.cancel_clue_edit_button)
        text_row.addWidget(self.add_clue_button)
        editor_layout.addWidget(clue_row)
        self.clue_edit_label = WrapLabel(
            "단서 수정 중"
        )
        self.clue_edit_label.setObjectName("subsectionHelp")
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
            ClueItemDelegate(SOURCE_LABELS, CERTAINTY_LABELS, self.clue_list)
        )
        # Rows wrap to the list width, so their heights follow it.
        self.clue_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.clue_list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.clue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.clue_list, 1)

    @staticmethod
    def _add_certainty_items(combo: QComboBox) -> None:
        for value, label in CERTAINTY_LABELS.items():
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
        self.load_button.clicked.connect(self._show_case_menu)
        self.admin_toggle.toggled.connect(lambda _checked: self._update_admin_rows())

    def _update_admin_rows(self) -> None:
        for widget in (self.type_edit, self.notes_edit):
            self._form.setRowVisible(widget, self.admin_toggle.isChecked())

    def _show_case_menu(self) -> None:
        menu = QMenu(self)
        self.fill_case_menu(menu)
        menu.popup(self.load_button.mapToGlobal(self.load_button.rect().bottomLeft()))

    def fill_case_menu(self, menu: QMenu, *, with_new: bool = True) -> None:
        """Saved cases to load (the current one checked), then "new case" if asked."""
        menu.clear()
        for case_id, label in self._saved_cases:
            current = case_id == self._selected_case_id
            action = menu.addAction(f"{label}  (현재)" if current else label)
            if current:
                font = action.font()
                font.setBold(True)
                action.setFont(font)
            action.triggered.connect(
                lambda _checked=False, value=case_id: self._load_saved_case(value)
            )
        if not self._saved_cases:
            menu.addAction("저장한 사건 없음").setEnabled(False)
        if with_new:
            menu.addSeparator()
            menu.addAction("새 사건 시작").triggered.connect(self.new_requested.emit)

    def _load_saved_case(self, case_id: str) -> None:
        self._selected_case_id = case_id
        self.load_requested.emit()

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
        self.remove_clue_button.setVisible(editing)
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
        """Plain text for screen readers; the list draws the two-line form itself.

        Analysis use is the item's check state, which screen readers announce themselves.
        """
        return (
            f"{clue.text} — 출처: {SOURCE_LABELS[clue.source]}, "
            f"확실성: {CERTAINTY_LABELS[clue.certainty]}"
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
            if record.case_type or record.management_notes:
                self.admin_toggle.setChecked(True)
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
        self._saved_cases = tuple(
            (record.case_id, f"{record.title} · v{record.revision}") for record in records
        )
        ids = [case_id for case_id, _label in self._saved_cases]
        self._selected_case_id = selected_id if selected_id in ids else (ids[0] if ids else None)

    def saved_case_labels(self) -> tuple[str, ...]:
        return tuple(label for _case_id, label in self._saved_cases)

    def selected_case_id(self) -> str | None:
        return self._selected_case_id

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
            self.load_button,
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
