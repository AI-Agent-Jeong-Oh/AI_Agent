"""Date and time field that opens a calendar when clicked, like the demo's date inputs.

The field shows "YYYY-MM-DD HH:MM" picked from the calendar. Clicking it (or pressing
Space, Enter or Down) opens the calendar; Delete clears it. What is stored is still the
text, so the case format does not change and older free-text values still show.
"""

from __future__ import annotations

from PySide6.QtCore import QDateTime, QLocale, QTime, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from video_agent.ui import theme
from video_agent.ui.popover import place_below

TEXT_FORMAT = "yyyy-MM-dd HH:mm"
_KOREAN = QLocale(QLocale.Language.Korean, QLocale.Country.SouthKorea)


def parse_date_time(text: str) -> QDateTime | None:
    """The date (and time) at the start of the text, if it has one."""
    text = text.strip()
    for pattern, length in (("yyyy-MM-dd HH:mm", 16), ("yyyy-MM-dd", 10)):
        value = QDateTime.fromString(text[:length], pattern)
        if value.isValid():
            return value
    return None


class DateTimePopup(QFrame):
    """Opaque popup (plain bordered panel) with a calendar, a time box and actions."""

    picked = Signal(QDateTime)
    cleared = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("datePopover")
        initial = QDateTime.currentDateTime()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.calendar = QCalendarWidget()
        self.calendar.setObjectName("datePicker")
        self.calendar.setLocale(_KOREAN)
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Sunday)
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.calendar.setSelectedDate(initial.date())
        # Korean calendars: Sunday red, Saturday blue, the rest plain.
        for day, color in (
            (Qt.DayOfWeek.Sunday, theme.DANGER),
            (Qt.DayOfWeek.Saturday, theme.PRIMARY),
        ):
            text_format = QTextCharFormat()
            text_format.setForeground(QColor(color))
            self.calendar.setWeekdayTextFormat(day, text_format)
        for name, icon in (("qt_calendar_prevmonth", "chevron-left"), ("qt_calendar_nextmonth", "chevron-right")):
            button = self.calendar.findChild(QToolButton, name)
            if button is not None:
                button.setIcon(theme.icon(icon))
                button.setAccessibleName("이전 달" if icon == "chevron-left" else "다음 달")
        layout.addWidget(self.calendar)

        time_row = QHBoxLayout()
        time_label = QLabel("시각")
        time_label.setObjectName("fieldLabel")
        self.time_edit = QTimeEdit(initial.time())
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setAccessibleName("시각")
        theme.polish_spin_box(self.time_edit)
        self.time_edit.setMinimumWidth(self.time_edit.fontMetrics().horizontalAdvance("00:00") + 56)
        time_row.addWidget(time_label)
        time_row.addWidget(self.time_edit)
        time_row.addStretch(1)
        layout.addLayout(time_row)

        actions = QHBoxLayout()
        clear = QPushButton("지우기")
        clear.setToolTip("발생 시각을 비웁니다")
        cancel = QPushButton("취소")
        apply = QPushButton("적용")
        for button in (clear, cancel, apply):
            theme.mark_compact(button)
        theme.mark_primary(apply)
        actions.addWidget(clear)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(apply)
        layout.addLayout(actions)

        clear.clicked.connect(self._clear)
        cancel.clicked.connect(self.close)
        apply.clicked.connect(self._apply)
        self.calendar.activated.connect(lambda _date: self._apply())
        self.setAccessibleName("날짜와 시각 고르기")

    def set_value(self, value: QDateTime) -> None:
        self.calendar.setSelectedDate(value.date())
        self.time_edit.setTime(value.time())

    def show_below(self, anchor: QWidget) -> None:
        self.move(place_below(self, anchor))
        self.show()
        self.calendar.setFocus()

    def _apply(self) -> None:
        self.picked.emit(QDateTime(self.calendar.selectedDate(), self.time_edit.time()))
        self.close()

    def _clear(self) -> None:
        self.cleared.emit()
        self.close()


class DateTimeField(QLineEdit):
    """Read-only field that opens DateTimePopup when clicked; a calendar icon marks it."""

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._calendar_action = self.addAction(
            theme.icon("calendar"), QLineEdit.ActionPosition.TrailingPosition
        )
        self._calendar_action.setToolTip("달력에서 고르기")
        self._calendar_action.triggered.connect(self.open_picker)
        self._picker: DateTimePopup | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.open_picker()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
            self.open_picker()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.clear()
            return
        super().keyPressEvent(event)

    def open_picker(self) -> DateTimePopup:
        """Open the calendar at the field's date and time, or at this hour today."""
        initial = parse_date_time(self.text())
        if initial is None:
            now = QDateTime.currentDateTime()
            initial = QDateTime(now.date(), QTime(now.time().hour(), 0))
        if self._picker is None:
            self._picker = DateTimePopup(self)
            self._picker.picked.connect(lambda value: self.setText(value.toString(TEXT_FORMAT)))
            self._picker.cleared.connect(self.clear)
        self._picker.set_value(initial)
        self._picker.show_below(self)
        return self._picker
