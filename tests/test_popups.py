from __future__ import annotations

import unittest

from PySide6.QtCore import QDate, QDateTime, QPoint, QTime, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from video_agent.ui.case_panel import CaseContextPanel
from video_agent.ui.datetime_field import DateTimeField, parse_date_time
from video_agent.ui.popover import InfoPopover, info_mark


class PopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        for widget in self.app.topLevelWidgets():
            if isinstance(widget, InfoPopover):
                widget.close()

    def test_info_mark_opens_a_titled_help_box(self) -> None:
        mark = info_mark("첫 문장입니다. 두 번째 문장입니다.", "사건 정보 안내")
        mark.show()
        mark.click()
        popover = next(
            widget for widget in self.app.topLevelWidgets()
            if isinstance(widget, InfoPopover) and widget.isVisible()
        )
        texts = [label.text() for label in popover.findChildren(QLabel) if label.text()]
        self.assertEqual(texts, ["사건 정보 안내", "첫 문장입니다. 두 번째 문장입니다."])
        self.assertEqual(mark.toolTip(), "")  # the box replaces the plain tooltip
        mark.close()

    def test_date_field_reads_and_fills_text_from_the_calendar(self) -> None:
        self.assertEqual(
            parse_date_time("2026-10-10 20:00 전후"),
            QDateTime(QDate(2026, 10, 10), QTime(20, 0)),
        )
        self.assertEqual(parse_date_time("2026-10-10").date(), QDate(2026, 10, 10))
        self.assertIsNone(parse_date_time("어제 저녁"))

        field = DateTimeField()
        field.setText("2026-10-10 20:00")
        picker = field.open_picker()
        self.assertEqual(picker.calendar.selectedDate(), QDate(2026, 10, 10))
        self.assertEqual(picker.time_edit.time(), QTime(20, 0))
        picker.calendar.setSelectedDate(QDate(2026, 10, 9))
        picker.time_edit.setTime(QTime(14, 30))
        picker._apply()
        self.assertEqual(field.text(), "2026-10-09 14:30")

    def test_clicking_the_date_field_opens_the_calendar_and_delete_clears_it(self) -> None:
        field = DateTimeField()
        field.resize(240, 40)
        field.show()
        self.assertTrue(field.isReadOnly())
        QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        self.assertTrue(field._picker is not None and field._picker.isVisible())
        field._picker._apply()
        self.assertRegex(field.text(), r"^\d{4}-\d{2}-\d{2} \d{2}:00$")
        QTest.keyClick(field, Qt.Key.Key_Delete)
        self.assertEqual(field.text(), "")
        field.close()

    def test_case_occurrence_time_has_the_calendar_field(self) -> None:
        panel = CaseContextPanel()
        self.assertIsInstance(panel.occurrence_edit, DateTimeField)
        panel.close()


if __name__ == "__main__":
    unittest.main()
