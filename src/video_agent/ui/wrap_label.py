from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel


class WrapLabel(QLabel):
    """Word-wrapped label whose minimum height is its wrapped height at the current width.

    Qt sizes a scroll area's page from minimum sizes, which count a wrapped label as one
    line, so the rows below it were squeezed and overlapped once text got larger.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setWordWrap(True)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().minimumSizeHint()
        if self.width() > 0:
            hint.setHeight(max(hint.height(), self.heightForWidth(self.width())))
        return hint

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()
