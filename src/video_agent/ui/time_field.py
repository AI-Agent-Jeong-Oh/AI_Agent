"""Time-of-video entry in minutes and seconds ("1:30", "1:30.5"), as people read a player.

Plain seconds ("90") are accepted too and shown back as "1:30". The value stays in
seconds, so the rest of the app is unchanged.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QValidator
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
    def __init__(self) -> None:
        super().__init__()
        self.setDecimals(3)
        self.setSingleStep(1.0)

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
