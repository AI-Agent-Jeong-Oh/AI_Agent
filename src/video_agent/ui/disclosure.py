from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from video_agent.ui import theme


class DisclosureButton(QPushButton):
    """Chevron toggle for rarely used fields; checked means the fields are shown.

    The owner hides or shows its rows on `toggled`; the button only keeps its chevron and
    accessible state in step, so keyboard and screen reader users get the same switch.
    """

    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.setObjectName("disclosure")
        self.setCheckable(True)
        self.setAccessibleName(title)
        self.toggled.connect(self._update_state)
        self._update_state(False)

    def _update_state(self, checked: bool) -> None:
        theme.set_icon(self, "chevron-down" if checked else "chevron-right", theme.PRIMARY)
        self.setAccessibleDescription("펼쳐짐" if checked else "접힘")
