from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class Card(QGroupBox):
    """Section card laid out like the demo page: a header line, then the body.

    The header holds the title, a short aside (a count or an info mark) and actions on the
    right, with a divider under it. The group box title is kept (hidden by the style sheet)
    so the section keeps its accessible name; content goes into `body`.
    """

    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.setProperty("card", True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QWidget()
        header.setObjectName("cardHeader")
        self._header_row = QHBoxLayout(header)
        self._header_row.setContentsMargins(16, 8, 12, 8)
        self._header_row.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        self.aside_label = QLabel()
        self.aside_label.setObjectName("cardAside")
        self.aside_label.setVisible(False)
        self._header_row.addWidget(self.title_label)
        self._header_row.addWidget(self.aside_label)
        self._header_row.addStretch(1)
        divider = QFrame()
        divider.setObjectName("cardDivider")
        self.body = QWidget()
        outer.addWidget(header)
        outer.addWidget(divider)
        outer.addWidget(self.body, 1)

    def setTitle(self, title: str) -> None:  # noqa: N802 - Qt API
        super().setTitle(title)
        if hasattr(self, "title_label"):
            self.title_label.setText(title)

    def set_aside(self, text: str) -> None:
        self.aside_label.setText(text)
        self.aside_label.setVisible(bool(text))

    def add_header_widget(self, widget: QWidget, *, after_title: bool = False) -> None:
        """Actions go to the right end; `after_title` puts a mark next to the title."""
        if after_title:
            self._header_row.insertWidget(2, widget)
        else:
            self._header_row.addWidget(widget)
