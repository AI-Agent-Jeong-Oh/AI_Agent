from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, Qt, Signal
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
    ClueSource,
    InformationCertainty,
)
from video_agent.ui import theme
from video_agent.ui.card import Card
from video_agent.ui.clue_delegate import ClueHeader, ClueItemDelegate, ClueList
from video_agent.ui.datetime_field import DateTimeField
from video_agent.ui.disclosure import DisclosureButton
from video_agent.ui.flow_layout import FlowRow
from video_agent.ui.focus_panel import CaseTypeChooser
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



def hint_clues(record: CaseRecord) -> tuple[AnalysisClue, ...]:
    """Search hints saved by older versions, as clues with an unknown source and certainty.

    Hints are no longer entered separately; a hint already written as a clue is skipped and
    the 20-clue limit is kept.
    """
    known = {" ".join(clue.text.split()).casefold() for clue in record.clues}
    room = max(0, 20 - len(record.clues))
    clues = []
    for hint in record.focus_hints:
        key = " ".join(hint.split()).casefold()
        if key in known or len(clues) >= room:
            continue
        known.add(key)
        clues.append(
            AnalysisClue(
                clue_id=f"clue-{uuid4().hex[:12]}",
                text=hint,
                source=ClueSource.OTHER,
                certainty=InformationCertainty.UNKNOWN,
                use_for_analysis=True,
            )
        )
    return tuple(clues)

class CaseContextPanel(Card):
    """Minimal case/prior-information UI that a teammate can extend independently."""

    changed = Signal()
    save_requested = Signal()
    load_requested = Signal()
    new_requested = Signal()
    # A case type was picked by the user (the window may suggest a purpose for it).
    case_type_chosen = Signal(str)

    def __init__(self) -> None:
        super().__init__("사건·사전 정보")
        self._editing_enabled = True
        self._loading = False
        self._build_ui()
        self._connect_signals()
        self._update_occurrence_field()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(12, 8, 12, 8)
        # Explanation behind a mark next to the title; loading (with "new case") is one
        # menu beside saving, at the right of the header.
        self.add_header_widget(
            info_mark(
                "사건명·관리 메모는 AI에 보내지 않습니다. 체크한 단서만 분석에 쓰며, "
                "단서는 영상으로 확인한 사실이 아닙니다.",
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
        # The card top keeps [불러오기]; the window puts [사건 저장] in the bottom bar.
        theme.mark_compact(self.load_button)
        self.add_header_widget(self.load_button)

        form = QFormLayout()
        theme.polish_form(form)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("예: ○○동 문 앞 택배 분실")
        self.title_edit.setAccessibleName("사건명")
        # Shown under the name only when a save finds it empty.
        self.title_error = QLabel("사건명을 입력해 주세요")
        self.title_error.setObjectName("fieldError")
        self.title_error.setVisible(False)
        title_widget = QWidget()
        title_column = QVBoxLayout(title_widget)
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(4)
        title_column.addWidget(self.title_edit)
        title_column.addWidget(self.title_error)
        # Chosen type marks the situation groups to check first (in step ② settings).
        self.case_type = CaseTypeChooser()
        occurrence_widget = QWidget()
        occurrence_row = QHBoxLayout(occurrence_widget)
        occurrence_row.setContentsMargins(0, 0, 0, 0)
        # Picked from a calendar that opens when the field is clicked; how sure it is goes
        # in the certainty choice beside it.
        self.occurrence_edit = DateTimeField()
        self.occurrence_edit.setPlaceholderText("날짜와 시각 선택")
        self.occurrence_edit.setAccessibleName("발생 시각")
        self.occurrence_certainty = QComboBox()
        self.occurrence_certainty.setAccessibleName("발생 시각 확실성")
        self._add_certainty_items(self.occurrence_certainty)
        self._set_combo_value(self.occurrence_certainty, InformationCertainty.UNKNOWN)
        # How sure comes first; with "미확인" there is no date to enter, so the field hides.
        occurrence_row.addWidget(self.occurrence_certainty)
        occurrence_row.addWidget(self.occurrence_edit, 1)
        # Holds the row's spare width while the date field is hidden ("미확인"); no text.
        self._occurrence_hint = QLabel("")
        self._occurrence_hint.setObjectName("subsectionHelp")
        occurrence_row.addWidget(self._occurrence_hint, 1)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("담당자, 처리 메모 등")
        # Two lines; fixed so the clue list, not the memo, gives way when the step is short.
        self.notes_edit.setFixedHeight(64)
        # The memo is kept for management only (never sent to the model), so it is
        # folded; it opens by itself when a loaded case has one.
        self.admin_toggle = DisclosureButton("관리 메모")
        form.addRow("사건 유형", self.case_type)
        form.addRow("사건명", title_widget)
        form.addRow("발생 시각", occurrence_widget)
        # Not asked any more (the user's call); the fields stay hidden so an older case's
        # time is kept as it was when the case is saved again.
        form.setRowVisible(occurrence_widget, False)
        form.addRow(self.admin_toggle)
        form.addRow("관리 메모", self.notes_edit)
        self._form = form
        layout.addLayout(form)
        self._update_admin_rows()

        divider = QFrame()
        divider.setObjectName("sectionDivider")
        layout.addWidget(divider)
        title_row = QHBoxLayout()
        clue_title = QLabel("분석 단서")
        clue_title.setObjectName("subsectionTitle")
        title_row.addWidget(clue_title)
        title_row.addStretch(1)
        self.clue_count_label = QLabel("분석 사용 0 / 전체 0")
        self.clue_count_label.setObjectName("subsectionAside")
        title_row.addWidget(self.clue_count_label, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(title_row)

        editor = QWidget()
        editor.setObjectName("clueEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        # The source is chosen first; the sentence field opens after it. Clues are not
        # rated for certainty: none of them is checked against the video yet (미확인).
        clue_row = FlowRow(spacing=16)
        source_label = QLabel("출처")
        source_label.setObjectName("fieldLabel")
        self.clue_source = QComboBox()
        self.clue_source.setAccessibleName("출처")
        self.clue_source.setPlaceholderText("선택")
        for value, label in SOURCE_LABELS.items():
            self.clue_source.addItem(label, value)
        self._clear_clue_choices()
        self.clue_use = QCheckBox("분석 사용")
        self.clue_use.setChecked(True)
        # Label and choice stay together; the groups wrap at large text sizes.
        for parts in ((source_label, self.clue_source),):
            group = QWidget()
            group_layout = QHBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            for part in parts:
                group_layout.addWidget(part)
            clue_row.flow.addWidget(group)
        clue_row.flow.addWidget(self.clue_use)
        editor_layout.addWidget(clue_row)

        self.clue_text = QLineEdit()
        self.clue_text.setAccessibleName("단서 문장")
        self.add_clue_button = QPushButton("단서 추가")
        theme.set_icon(self.add_clue_button, "plus")
        text_row = QHBoxLayout()
        text_row.addWidget(self.clue_text, 1)
        text_row.addWidget(self.add_clue_button)
        editor_layout.addLayout(text_row)
        layout.addWidget(editor)

        # A table like the CCTV video list: grey header, one line per clue.
        self.clue_list = ClueList()
        self.clue_list.setMinimumHeight(100)
        # Take the space left in the step instead of Qt's default list height, so the
        # larger clue rows scroll inside the list rather than the whole step.
        self.clue_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.clue_list.setToolTip(
            "체크된 항목만 분석 계획과 모델 입력에 포함됩니다. 단서를 고르면 위에서 고칠 수 있습니다."
        )
        self.clue_list.setAccessibleName("분석 단서 목록")
        self._clue_delegate = ClueItemDelegate(SOURCE_LABELS, self.clue_list)
        self.clue_list.setItemDelegate(self._clue_delegate)
        self.clue_list.setFrameShape(QFrame.Shape.NoFrame)
        # Rows wrap to the list width, so their heights follow it.
        self.clue_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.clue_list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.clue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.clue_header = ClueHeader(self.clue_list, self._clue_delegate)
        table = QVBoxLayout()
        table.setSpacing(0)
        table.addWidget(self.clue_header)
        table.addWidget(self.clue_list, 1)
        layout.addLayout(table, 1)
        self._update_clue_inputs()

    @staticmethod
    def _add_certainty_items(combo: QComboBox) -> None:
        for value, label in CERTAINTY_LABELS.items():
            combo.addItem(label, value)

    def _connect_signals(self) -> None:
        for edit in (
            self.title_edit,
            self.occurrence_edit,
        ):
            edit.textChanged.connect(self._emit_changed)
        self.notes_edit.textChanged.connect(self._emit_changed)
        self.occurrence_certainty.currentIndexChanged.connect(self._occurrence_certainty_changed)
        self.add_clue_button.clicked.connect(self._add_clue)
        self.clue_text.returnPressed.connect(self._add_clue)
        # Leaving an edit without saving: Esc in the sentence field, the chosen row
        # again, or an empty part of the table (see ClueList).
        self.clue_text.installEventFilter(self)
        self.clue_list.release_requested.connect(self._cancel_clue_edit)
        self.clue_list.itemChanged.connect(self._clue_item_changed)
        self.clue_list.currentRowChanged.connect(self._select_clue)
        self._clue_delegate.edit_requested.connect(self.clue_list.setCurrentRow)
        self._clue_delegate.remove_requested.connect(self._remove_clue_row)
        self.clue_source.currentIndexChanged.connect(lambda _index: self._update_clue_inputs())
        self.title_edit.textChanged.connect(lambda _text: self.title_error.setVisible(False))
        self.save_button.clicked.connect(self.save_requested.emit)
        self.load_button.clicked.connect(self._show_case_menu)
        self.admin_toggle.toggled.connect(lambda _checked: self._update_admin_rows())
        self.case_type.changed.connect(self._case_type_changed)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if (
            watched is self.clue_text
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
            and self._editing_clue_row() is not None
        ):
            self._cancel_clue_edit()
            return True
        return super().eventFilter(watched, event)

    def _occurrence_certainty_changed(self, *_args: object) -> None:
        if self.occurrence_certainty.currentData() == InformationCertainty.UNKNOWN and not self._loading:
            # An unknown time has no date; a date typed before is not kept.
            self.occurrence_edit.clear()
        self._update_occurrence_field()
        self._emit_changed()

    def _update_occurrence_field(self) -> None:
        # An older case may hold a date marked unknown; it stays visible until changed.
        show = (
            self.occurrence_certainty.currentData() != InformationCertainty.UNKNOWN
            or bool(self.occurrence_edit.text().strip())
        )
        self.occurrence_edit.setVisible(show)
        self._occurrence_hint.setVisible(not show)

    def _case_type_changed(self, value: str) -> None:
        # The "중점 관찰 항목" checks in step ② follow the type (see the main window).
        if not self._loading:
            self.case_type_chosen.emit(value)

    def case_type_text(self) -> str:
        return self.case_type.value()

    def _update_admin_rows(self) -> None:
        self._form.setRowVisible(self.notes_edit, self.admin_toggle.isChecked())

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
        if not text or not self._clue_choices_made():
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
            certainty=InformationCertainty.UNKNOWN,
            use_for_analysis=self.clue_use.isChecked(),
        )
        self._append_clue(clue)
        self.clue_text.clear()
        # The next clue usually comes from the same report, so the choice stays.
        self._last_clue_choice = clue.source
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
        else:
            self.clue_text.clear()
            self._restore_clue_choices()
        self.add_clue_button.setText("변경 저장" if editing else "단서 추가")
        self.clue_use.setEnabled(not editing and self._editing_enabled)
        self._update_clue_inputs()

    def _clear_clue_choices(self) -> None:
        self.clue_source.setCurrentIndex(-1)

    def _restore_clue_choices(self) -> None:
        """Back to the last added clue's source (blank before the first)."""
        last = getattr(self, "_last_clue_choice", None)
        if last is None:
            self._clear_clue_choices()
            return
        self._set_combo_value(self.clue_source, last)

    def _clue_choices_made(self) -> bool:
        return self.clue_source.currentIndex() >= 0

    def _update_clue_inputs(self) -> None:
        """The sentence field and add button open once source and certainty are chosen."""
        ready = self._clue_choices_made() and self._editing_enabled
        self.clue_text.setEnabled(ready)
        self.add_clue_button.setEnabled(ready)
        self.clue_text.setPlaceholderText(
            "예: 신고자는 사람이 문 앞 상자를 들어 이동했다고 설명함"
            if ready
            else "출처를 먼저 선택하세요"
        )

    def require_title(self) -> bool:
        """Before saving: the case name is needed; say so under the field when it is empty."""
        if self.title_edit.text().strip():
            return True
        self.title_error.setVisible(True)
        self.title_edit.setFocus()
        return False

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

    def _remove_clue_row(self, row: int) -> None:
        if row < 0 or row >= self.clue_list.count() or not self._editing_enabled:
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
        return f"{clue.text} — 출처: {SOURCE_LABELS[clue.source]}"

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

    def set_record(self, record: CaseRecord) -> None:
        self._loading = True
        try:
            self.title_edit.setText(record.title)
            self.case_type.set_value(record.case_type)
            self.occurrence_edit.setText(record.occurrence_time_text)
            self._set_combo_value(
                self.occurrence_certainty,
                record.occurrence_time_certainty,
            )
            self._update_occurrence_field()
            self.notes_edit.setPlainText(record.management_notes)
            if record.management_notes:
                self.admin_toggle.setChecked(True)
            self.clue_list.clear()
            self._last_clue_choice = None  # a new or loaded case starts with a blank choice
            self._cancel_clue_edit()
            for clue in record.clues:
                self._append_clue(clue)
            for clue in hint_clues(record):
                self._append_clue(clue)
            self._update_clue_count()
        finally:
            self._loading = False

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

    def set_editing_enabled(self, enabled: bool) -> None:
        self._editing_enabled = enabled
        for widget in (
            self.load_button,
            self.save_button,
            self.title_edit,
            *self.case_type.buttons.values(),
            self.case_type.other_edit,
            self.occurrence_edit,
            self.occurrence_certainty,
            self.notes_edit,
            self.clue_text,
            self.clue_source,
            self.clue_use,
            self.add_clue_button,
            self.clue_list,
        ):
            widget.setEnabled(enabled)
        self.clue_use.setEnabled(enabled and self._editing_clue_row() is None)
        self._update_clue_inputs()
