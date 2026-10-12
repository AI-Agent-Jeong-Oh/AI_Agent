"""Small confirm and notice windows in the app's own look, instead of Qt's message boxes.

Qt's boxes add a large "?" or "!" picture and the system's button order; these show a bold
title, a short sentence, an optional list of what will happen, and the app's buttons on
the right ([취소] grey, the action navy). An error title carries a small red line mark.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from video_agent.ui import theme


class MessageDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        *,
        details: Sequence[str] = (),
        confirm_text: str = "확인",
        cancel_text: str | None = None,
        error: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("messageDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        if error:
            mark = QLabel()
            mark.setPixmap(theme.icon("triangle-alert", theme.DANGER).pixmap(18, 18))
            heading.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("messageTitle")
        heading.addWidget(self.title_label, 1)
        layout.addLayout(heading)

        self.text_label = QLabel(text)
        self.text_label.setObjectName("messageText")
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.text_label)

        if details:
            box = QWidget()
            box.setObjectName("messageDetails")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(12, 8, 12, 8)
            box_layout.setSpacing(4)
            for line in details:
                label = QLabel(line)
                label.setObjectName("messageDetail")
                label.setWordWrap(True)
                box_layout.addWidget(label)
            layout.addWidget(box)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.cancel_button: QPushButton | None = None
        if cancel_text is not None:
            self.cancel_button = QPushButton(cancel_text)
            self.cancel_button.clicked.connect(self.reject)
            buttons.addWidget(self.cancel_button)
        self.confirm_button = QPushButton(confirm_text)
        theme.mark_primary(self.confirm_button)
        self.confirm_button.setDefault(True)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)
        layout.addSpacing(6)
        layout.addLayout(buttons)


def confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    confirm_text: str,
    cancel_text: str = "취소",
    details: Sequence[str] = (),
) -> bool:
    """Ask before an action; True only when the action button was pressed."""
    dialog = MessageDialog(
        parent,
        title,
        text,
        details=details,
        confirm_text=confirm_text,
        cancel_text=cancel_text,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted


def notify(parent: QWidget | None, title: str, text: str, *, error: bool = False) -> None:
    """Tell something that needs no choice (with [확인] only)."""
    MessageDialog(parent, title, text, error=error).exec()
