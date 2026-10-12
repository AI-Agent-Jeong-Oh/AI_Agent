"""Numbered, foldable part of the step ② settings card ("1 찾을 대상", "2 확인 범위", ...).

Only one part is open at a time; clicking a header opens it (or folds it). What was
chosen in a folded part is not repeated beside its title; it is in the header's
tooltip and accessible description.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from video_agent.ui import theme


class _Header(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StepSection(QWidget):
    open_requested = Signal()

    def __init__(self, number: int, title: str) -> None:
        super().__init__()
        self._open = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.header = _Header()
        self.header.setObjectName("stepSectionHeader")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setAccessibleName(f"{number}단계 {title}")
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.number_label = QLabel(str(number))
        self.number_label.setObjectName("stepNumber")
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("subsectionTitle")
        self.chevron = QLabel()
        row.addWidget(self.number_label)
        row.addWidget(self.title_label)
        self._extra = QHBoxLayout()
        self._extra.setSpacing(4)
        row.addLayout(self._extra)
        row.addStretch(1)
        row.addWidget(self.chevron)
        layout.addWidget(self.header)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        layout.addWidget(self.body, 1)
        self.header.clicked.connect(self.open_requested.emit)
        self._refresh()

    def add_header_widget(self, widget: QWidget) -> None:
        """A small mark beside the title (e.g. an info mark)."""
        self._extra.addWidget(widget)

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool) -> None:
        self._open = open_
        self._refresh()

    def set_summary(self, text: str) -> None:
        self._summary = text
        self.header.setToolTip(text)
        self._refresh()

    def summary(self) -> str:
        return getattr(self, "_summary", "")

    def _refresh(self) -> None:
        self.body.setVisible(self._open)
        summary = self.summary()
        self.chevron.setPixmap(
            theme.icon("chevron-down" if self._open else "chevron-right").pixmap(16, 16)
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding if self._open else QSizePolicy.Policy.Fixed,
        )
        self.header.setAccessibleDescription("열림" if self._open else f"접힘 · {summary}")
