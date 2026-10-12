"""Case type choice (step ①) and the "중점 관찰 항목" checks (step ② settings), from the demo page.

Choosing a case type marks the situation groups recommended for it and checks them, until
the user changes the checks. Search details of one's own go in the case clues, with their
source and certainty, not here.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QResizeEvent
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
from video_agent.contracts import FocusSituation
from video_agent.ui import theme

OTHER_TYPE = "기타"


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
    """One check: check box, group name and a "추천" tag; a checked one reads bold navy."""

    toggled = Signal(bool)

    def __init__(self, situation: FocusSituation) -> None:
        super().__init__()
        self.situation = situation
        self.setObjectName("situationTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 8, 4)
        row.setSpacing(8)
        self.check = QCheckBox(SITUATION_LABELS[situation])
        examples = ", ".join(SITUATION_PATTERNS[situation])
        self.check.setToolTip(f"예: {examples}")
        self.check.setAccessibleDescription(f"예: {examples}")
        self.tag = QLabel("추천")
        self.tag.setObjectName("recommendTag")
        self.tag.setVisible(False)
        row.addWidget(self.check)
        row.addWidget(self.tag)
        row.addStretch(1)
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
        # The check box's own look (bold navy when checked) follows the tile's property.
        for widget in (self, self.check):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class FocusSection(QWidget):
    """Situation groups to check first (checks only; search details go in the case clues)."""

    changed = Signal()

    def __init__(self, columns: int = 2) -> None:
        super().__init__()
        self._case_type = ""
        # Recommendations follow the case type only until the user changes the checks.
        self._touched = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("중점 관찰 항목")
        title.setObjectName("subsectionTitle")
        self.count_label = QLabel("선택 없음")
        self.count_label.setObjectName("subsectionAside")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.count_label)
        layout.addLayout(title_row)

        # Equal columns of tiles, as on the demo page: up to `columns`, fewer when the
        # card is narrow (small window or large text), so it never forces a side scroll.
        self._max_columns = max(1, columns)
        self._columns = 0
        self._tiles_layout = QGridLayout()
        self._tiles_layout.setHorizontalSpacing(8)
        self._tiles_layout.setVerticalSpacing(6)
        self.tiles: dict[FocusSituation, SituationTile] = {}
        for situation in FocusSituation:
            tile = SituationTile(situation)
            tile.toggled.connect(self._user_changed)
            tile.check.toggled.connect(lambda _checked: self._refresh_count())
            self.tiles[situation] = tile
        self._arrange(self._max_columns)
        layout.addLayout(self._tiles_layout)

    # ----- layout ----------------------------------------------------------------

    def columns(self) -> int:
        return self._columns

    def _arrange(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        grid = self._tiles_layout
        for tile in self.tiles.values():
            grid.removeWidget(tile)
        for column in range(self._max_columns):
            grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, tile in enumerate(self.tiles.values()):
            grid.addWidget(tile, index // columns, index % columns)
        self.updateGeometry()

    def _tile_width(self) -> int:
        return max(tile.minimumSizeHint().width() for tile in self.tiles.values())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        # As narrow as one column; wider space gets more columns (see resizeEvent).
        hint = super().minimumSizeHint()
        return QSize(self._tile_width(), hint.height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        spacing = self._tiles_layout.horizontalSpacing()
        fit = (event.size().width() + spacing) // (self._tile_width() + spacing)
        self._arrange(max(1, min(self._max_columns, fit)))
        super().resizeEvent(event)

    # ----- values ----------------------------------------------------------------

    def focus(self) -> tuple[FocusSituation, ...]:
        return tuple(item for item, tile in self.tiles.items() if tile.check.isChecked())

    def set_values(self, case_type: str, focus: tuple[FocusSituation, ...]) -> None:
        """Show a loaded case; saved checks are kept as the user's own choice."""
        self._touched = bool(focus)
        self._case_type = case_type
        for situation, tile in self.tiles.items():
            tile.check.blockSignals(True)
            tile.check.setChecked(situation in focus)
            tile._update_state(situation in focus)
            tile.check.blockSignals(False)
        self._apply_case_type(case_type, check=not self._touched)

    def set_case_type(self, case_type: str) -> None:
        """Mark the type's recommended groups; check them while the user has not chosen."""
        self._case_type = case_type
        self._apply_case_type(case_type, check=not self._touched)
        self.changed.emit()

    def set_editing_enabled(self, enabled: bool) -> None:
        for tile in self.tiles.values():
            tile.check.setEnabled(enabled)

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
