"""Numbered, foldable part of the step ② plan card ("1 무엇을 찾을지", "2 볼 구간", ...).

Only one part is open at a time; a closed part shows a one-line summary of what was
chosen, and clicking its header opens it again (to change it).
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
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("subsectionAside")
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.chevron = QLabel()
        row.addWidget(self.number_label)
        row.addWidget(self.title_label)
        self._extra = QHBoxLayout()
        self._extra.setSpacing(4)
        row.addLayout(self._extra)
        row.addWidget(self.summary_label, 1)
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
        self.summary_label.setToolTip(text)
        self._refresh()

    def _refresh(self) -> None:
        self.body.setVisible(self._open)
        summary = getattr(self, "_summary", "")
        # The summary is for a closed part; an open one shows its contents instead.
        self.summary_label.setText("" if self._open else summary)
        self.chevron.setPixmap(
            theme.icon("chevron-down" if self._open else "chevron-right").pixmap(16, 16)
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding if self._open else QSizePolicy.Policy.Fixed,
        )
        self.header.setAccessibleDescription("열림" if self._open else f"접힘 · {summary}")
