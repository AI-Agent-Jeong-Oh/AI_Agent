"""Time-of-video entry in minutes and seconds ("1:30", "1:30.5"), as people read a player.

Plain seconds ("90") are accepted too and shown back as "1:30". The value stays in
seconds, so the rest of the app is unchanged.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent, QValidator
from PySide6.QtWidgets import QDoubleSpinBox

_PARTIAL = re.compile(r"^\d{0,3}(:\d{0,2}){0,2}(\.\d{0,3})?$")


def format_time(seconds: float) -> str:
    """"0:30", "1:30.5", "1:02:03" (hours only when needed)."""
    millis = round(max(0.0, seconds) * 1000)
    whole, fraction = divmod(millis, 1000)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    if fraction:
        text += f".{fraction:03d}".rstrip("0")
    return text


def parse_time(text: str) -> float | None:
    """Seconds from "m:ss", "h:mm:ss" or plain seconds; None when it is not a time."""
    text = text.strip()
    if not text or not _PARTIAL.match(text) or text.endswith((":", ".")):
        return None
    parts = text.split(":")
    try:
        seconds = float(parts[-1])
        minutes = int(parts[-2]) if len(parts) >= 2 else 0
        hours = int(parts[-3]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if len(parts) >= 2 and seconds >= 60:
        return None
    if len(parts) == 3 and minutes >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


class TimeSpinBox(QDoubleSpinBox):
    """Typing replaces the time shown: a click selects it all, and "영상 끝" gives way."""

    def __init__(self) -> None:
        super().__init__()
        self.setDecimals(3)
        self.setSingleStep(1.0)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        # A click (which would place the cursor inside "0:30" or after "영상 끝")
        # selects the whole time, so what is typed next replaces it.
        if watched is self.lineEdit() and event.type() == QMouseEvent.Type.MouseButtonRelease:
            if not self.lineEdit().hasSelectedText():
                QTimer.singleShot(0, self.selectAll)
        return super().eventFilter(watched, event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt API
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        special = self.specialValueText()
        typed = event.text()
        if special and self.lineEdit().text() == special and typed and typed[0].isdigit():
            self.lineEdit().clear()
        super().keyPressEvent(event)

    def fixup(self, text: str) -> str:
        """A time past the end becomes the end; anything else unreadable keeps the old value."""
        value = parse_time(text)
        if value is not None and value > self.maximum():
            return self.textFromValue(self.maximum())
        return text

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt API
        return format_time(value)

    def valueFromText(self, text: str) -> float:  # noqa: N802 - Qt API
        if self.specialValueText() and text.strip() == self.specialValueText():
            return self.minimum()
        value = parse_time(text)
        return self.value() if value is None else value

    def validate(self, text: str, pos: int):  # noqa: D401 - Qt API
        stripped = text.strip()
        if self.specialValueText() and stripped == self.specialValueText():
            return QValidator.State.Acceptable, text, pos
        if not _PARTIAL.match(stripped):
            return QValidator.State.Invalid, text, pos
        value = parse_time(stripped)
        if value is None or not self.minimum() <= value <= self.maximum():
            return QValidator.State.Intermediate, text, pos
        return QValidator.State.Acceptable, text, pos
