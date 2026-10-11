"""Case type choice and the "우선 확인할 상황" section of the case card (from the demo page).

Choosing a case type marks the situation groups recommended for it and checks them, until
the user changes the checks. Users can also add short search hints of their own.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from video_agent.agent.situations import (
    CASE_TYPES,
    SITUATION_LABELS,
    SITUATION_PATTERNS,
    recommended_for,
)
from video_agent.contracts import MAX_FOCUS_HINT_LENGTH, MAX_FOCUS_HINTS, FocusSituation
from video_agent.ui import theme
from video_agent.ui.flow_layout import FlowRow
from video_agent.ui.popover import info_mark

OTHER_TYPE = "기타"
FOCUS_HELP = (
    "선택한 상황은 확인 순서에만 반영합니다. 선택하지 않은 상황도 분석하며, 직접 입력한 "
    "내용은 추가 탐색 힌트로 사용합니다. 범죄 여부를 판단하는 기준이 아닙니다."
)


class CaseTypeChooser(QWidget):
    """[실종] [폭행] [아동학대] [절도] [기타] choice; "기타" opens a short text field."""

    changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for label in (*CASE_TYPES, OTHER_TYPE):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setAccessibleName(f"사건 유형: {label}")
            theme.mark_compact(button)
            self._group.addButton(button)
            self.buttons[label] = button
            row.addWidget(button)
        self.other_edit = QLineEdit()
        self.other_edit.setPlaceholderText("예: 주거침입")
        self.other_edit.setAccessibleName("기타 사건 유형")
        self.other_edit.setMaxLength(200)
        self.other_edit.setVisible(False)
        row.addWidget(self.other_edit, 1)
        row.addStretch(0)
        self._group.buttonClicked.connect(lambda _button: self._chosen())
        self.other_edit.textChanged.connect(lambda _text: self.changed.emit(self.value()))

    def value(self) -> str:
        checked = self._group.checkedButton()
        if checked is None:
            return ""
        if checked is self.buttons[OTHER_TYPE]:
            return self.other_edit.text().strip()
        return checked.text()

    def set_value(self, value: str) -> None:
        value = value.strip()
        self._group.setExclusive(False)
        for button in self.buttons.values():
            button.setChecked(False)
        self._group.setExclusive(True)
        self.other_edit.blockSignals(True)
        self.other_edit.clear()
        if value in CASE_TYPES:
            self.buttons[value].setChecked(True)
        elif value:
            self.buttons[OTHER_TYPE].setChecked(True)
            self.other_edit.setText(value)
        self.other_edit.blockSignals(False)
        self.other_edit.setVisible(self.buttons[OTHER_TYPE].isChecked())

    def _chosen(self) -> None:
        other = self.buttons[OTHER_TYPE].isChecked()
        self.other_edit.setVisible(other)
        if other:
            self.other_edit.setFocus()
        self.changed.emit(self.value())


class SituationTile(QFrame):
    """One bordered tile: check box, group name and a "추천" tag (as on the demo page)."""

    toggled = Signal(bool)

    def __init__(self, situation: FocusSituation) -> None:
        super().__init__()
        self.situation = situation
        self.setObjectName("situationTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(8)
        self.check = QCheckBox(SITUATION_LABELS[situation])
        examples = ", ".join(SITUATION_PATTERNS[situation])
        self.check.setToolTip(f"예: {examples}")
        self.check.setAccessibleDescription(f"예: {examples}")
        self.tag = QLabel("추천")
        self.tag.setObjectName("recommendTag")
        self.tag.setVisible(False)
        row.addWidget(self.check)
        row.addStretch(1)
        row.addWidget(self.tag)
        self.setToolTip(f"예: {examples}")
        self.check.toggled.connect(self._update_state)
        # Only user clicks count as a choice (not checks set from a recommendation).
        self.check.clicked.connect(lambda _checked=False: self.toggled.emit(self.check.isChecked()))

    def set_recommended(self, recommended: bool) -> None:
        self.tag.setVisible(recommended)
        self.check.setAccessibleName(
            f"{SITUATION_LABELS[self.situation]}{' (추천)' if recommended else ''}"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.check.isEnabled():
            self.check.click()
            return
        super().mousePressEvent(event)

    def _update_state(self, checked: bool) -> None:
        self.setProperty("checked", checked)
        self.style().unpolish(self)
        self.style().polish(self)


class FocusSection(QWidget):
    """Situation groups to check first, plus the user's own short hints."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._case_type = ""
        # Recommendations follow the case type only until the user changes the checks.
        self._touched = False
        self._hints: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("우선 확인할 상황")
        title.setObjectName("subsectionTitle")
        self.count_label = QLabel("선택 없음")
        self.count_label.setObjectName("subsectionAside")
        title_row.addWidget(title)
        title_row.addWidget(info_mark(FOCUS_HELP, "우선 확인할 상황 안내"))
        title_row.addStretch(1)
        title_row.addWidget(self.count_label)
        layout.addLayout(title_row)
        self._title_row = title_row

        # Two equal columns of tiles, as on the demo page.
        tiles = QGridLayout()
        tiles.setHorizontalSpacing(8)
        tiles.setVerticalSpacing(6)
        self.tiles: dict[FocusSituation, SituationTile] = {}
        for index, situation in enumerate(FocusSituation):
            tile = SituationTile(situation)
            tile.toggled.connect(self._user_changed)
            tile.check.toggled.connect(lambda _checked: self._refresh_count())
            self.tiles[situation] = tile
            tiles.addWidget(tile, index // 2, index % 2)
        tiles.setColumnStretch(0, 1)
        tiles.setColumnStretch(1, 1)
        layout.addLayout(tiles)

        self.hint_row = FlowRow(spacing=6)
        self.add_hint_button = QPushButton("직접 입력")
        theme.set_icon(self.add_hint_button, "plus")
        theme.mark_compact(self.add_hint_button)
        self.add_hint_button.setToolTip(f"찾을 상황을 {MAX_FOCUS_HINT_LENGTH}자 이내로 추가합니다")
        self.hint_edit = QLineEdit()
        self.hint_edit.setPlaceholderText("예: 가방을 든 사람")
        self.hint_edit.setAccessibleName("추가 탐색 힌트")
        self.hint_edit.setMaxLength(MAX_FOCUS_HINT_LENGTH)
        self.hint_edit.setMinimumWidth(200)
        # Same 32px height as the hint tags beside it.
        theme.mark_compact(self.hint_edit)
        self.hint_edit.setVisible(False)
        # "직접 입력" sits in the title line and the field opens in its place, so typing
        # moves nothing; the hint line below shows only when it has tags.
        self._title_row.addSpacing(8)
        self._title_row.addWidget(self.hint_edit)
        self._title_row.addWidget(self.add_hint_button)
        layout.addWidget(self.hint_row)
        self.hint_row.setVisible(False)
        self._hint_chips: list[QPushButton] = []

        self.add_hint_button.clicked.connect(self._start_hint)
        self.hint_edit.returnPressed.connect(self._add_hint)
        self.hint_edit.editingFinished.connect(self._finish_hint)

    # ----- values ----------------------------------------------------------------

    def focus(self) -> tuple[FocusSituation, ...]:
        return tuple(item for item, tile in self.tiles.items() if tile.check.isChecked())

    def hints(self) -> tuple[str, ...]:
        return tuple(self._hints)

    def set_values(
        self,
        case_type: str,
        focus: tuple[FocusSituation, ...],
        hints: tuple[str, ...],
    ) -> None:
        """Show a loaded case; saved checks are kept as the user's own choice."""
        self._touched = bool(focus or hints)
        self._case_type = case_type
        for situation, tile in self.tiles.items():
            tile.check.blockSignals(True)
            tile.check.setChecked(situation in focus)
            tile._update_state(situation in focus)
            tile.check.blockSignals(False)
        self._hints = list(hints)
        self._rebuild_hint_chips()
        self._apply_case_type(case_type, check=not self._touched)

    def set_case_type(self, case_type: str) -> None:
        """Mark the type's recommended groups; check them while the user has not chosen."""
        self._case_type = case_type
        self._apply_case_type(case_type, check=not self._touched)
        self.changed.emit()

    def set_editing_enabled(self, enabled: bool) -> None:
        for tile in self.tiles.values():
            tile.check.setEnabled(enabled)
        self.add_hint_button.setEnabled(enabled and len(self._hints) < MAX_FOCUS_HINTS)
        self.hint_edit.setEnabled(enabled)
        for chip in self._hint_chips:
            chip.setEnabled(enabled)

    # ----- internals ------------------------------------------------------------------

    def _apply_case_type(self, case_type: str, *, check: bool) -> None:
        recommended = recommended_for(case_type)
        for situation, tile in self.tiles.items():
            tile.set_recommended(situation in recommended)
            if check:
                tile.check.blockSignals(True)
                tile.check.setChecked(situation in recommended)
                tile._update_state(situation in recommended)
                tile.check.blockSignals(False)
        self._refresh_count()

    def _user_changed(self, *_args: object) -> None:
        self._touched = True
        self._refresh_count()
        self.changed.emit()

    def _refresh_count(self) -> None:
        count = len(self.focus())
        self.count_label.setText(f"선택 {count}개" if count else "선택 없음")

    def _start_hint(self) -> None:
        self.add_hint_button.setVisible(False)
        self.hint_edit.setVisible(True)
        self.hint_edit.setFocus()

    def _add_hint(self) -> None:
        text = " ".join(self.hint_edit.text().split())
        known = {item.casefold() for item in self._hints}
        if text and text.casefold() not in known and len(self._hints) < MAX_FOCUS_HINTS:
            self._hints.append(text)
            self._rebuild_hint_chips()
            self._touched = True
            self.changed.emit()
        self.hint_edit.clear()
        self._finish_hint()

    def _finish_hint(self) -> None:
        if self.hint_edit.text().strip():
            return
        # Hand focus back to the button first, so it does not jump to the clue table.
        refocus = self.hint_edit.hasFocus()
        self.add_hint_button.setVisible(True)
        if refocus:
            self.add_hint_button.setFocus()
        self.hint_edit.setVisible(False)
        self.hint_row.setVisible(bool(self._hints))
        self.add_hint_button.setEnabled(len(self._hints) < MAX_FOCUS_HINTS)

    def _remove_hint(self, text: str) -> None:
        if text in self._hints:
            self._hints.remove(text)
            self._rebuild_hint_chips()
            self._touched = True
            self.changed.emit()

    def _rebuild_hint_chips(self) -> None:
        for chip in self._hint_chips:
            self.hint_row.flow.removeWidget(chip)
            chip.deleteLater()
        self._hint_chips = []
        for index, text in enumerate(self._hints):
            chip = QPushButton(text)
            chip.setObjectName("hintChip")
            theme.set_icon(chip, "x")
            chip.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            theme.mark_compact(chip)
            chip.setToolTip("누르면 삭제")
            chip.setAccessibleName(f"추가 탐색 힌트 {text} 삭제")
            chip.clicked.connect(lambda _checked=False, value=text: self._remove_hint(value))
            self.hint_row.flow.insertWidget(index, chip)
            self._hint_chips.append(chip)
        self.hint_row.setVisible(bool(self._hints))
        self.add_hint_button.setEnabled(len(self._hints) < MAX_FOCUS_HINTS)
