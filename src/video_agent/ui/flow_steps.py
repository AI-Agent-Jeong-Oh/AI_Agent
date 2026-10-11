from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from video_agent.ui import theme


class FlowSteps(QWidget):
    """One line of steps: done steps get a check, the current one is bold blue.

    Icons are drawn, not typed as symbols, and each state also differs in weight, so the
    line does not rely on color alone.
    """

    def __init__(self, labels: Sequence[str]) -> None:
        super().__init__()
        self._labels = tuple(labels)
        self._current: int | None = None
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 0, 0)
        row.setSpacing(4)
        self._marks: list[QLabel] = []
        self._names: list[QLabel] = []
        for index, label in enumerate(self._labels):
            if index:
                arrow = QLabel()
                arrow.setPixmap(theme.icon("chevron-right", theme.GRAY_40).pixmap(14, 14))
                row.addWidget(arrow)
            mark = QLabel()
            name = QLabel(label)
            name.setObjectName("flowStep")
            row.addWidget(mark)
            row.addWidget(name)
            self._marks.append(mark)
            self._names.append(name)
        row.addStretch(1)
        self.setAccessibleName("진행 순서")

    def set_state(self, done: Sequence[bool], current: int | None) -> None:
        self._current = current
        for index, (mark, name) in enumerate(zip(self._marks, self._names)):
            if index == current:
                mark.clear()
                mark.setVisible(False)
                name.setStyleSheet(f"color:{theme.PRIMARY}; font-weight:700;")
            elif done[index]:
                mark.setPixmap(theme.icon("check", theme.SUCCESS).pixmap(14, 14))
                mark.setVisible(True)
                name.setStyleSheet(f"color:{theme.TEXT_SUB};")
            else:
                mark.clear()
                mark.setVisible(False)
                name.setStyleSheet(f"color:{theme.TEXT_SUB};")
        self.setAccessibleDescription(
            f"지금 할 일: {self._labels[current]}" if current is not None else "진행 순서"
        )

    def current_label(self) -> str | None:
        return self._labels[self._current] if self._current is not None else None
