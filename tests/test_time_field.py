from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from video_agent.ui.time_field import TimeSpinBox, format_time, parse_time


class TimeFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_format_and_parse_minutes_and_seconds(self) -> None:
        self.assertEqual([format_time(v) for v in (0, 30, 90, 90.5, 3723)], ["0:00", "0:30", "1:30", "1:30.5", "1:02:03"])
        self.assertEqual(parse_time("1:30"), 90)
        self.assertEqual(parse_time("90"), 90)
        self.assertEqual(parse_time("1:30.5"), 90.5)
        self.assertEqual(parse_time("1:02:03"), 3723)
        for bad in ("", "1:", "1:75", "a", "1:30:"):
            self.assertIsNone(parse_time(bad), bad)

    def test_spin_box_reads_and_shows_minutes(self) -> None:
        spin = TimeSpinBox()
        spin.setRange(0, 120)
        spin.lineEdit().setText("1:30")
        spin.interpretText()
        self.assertEqual(spin.value(), 90)
        self.assertEqual(spin.text(), "1:30")
        self.assertEqual(spin.validate("2:30", 0)[0], QValidator.State.Intermediate)  # past the end
        self.assertEqual(spin.validate("1:3", 0)[0], QValidator.State.Acceptable)
        self.assertEqual(spin.validate("x", 0)[0], QValidator.State.Invalid)
        spin.setSpecialValueText("영상 끝")
        spin.setValue(0)
        self.assertEqual(spin.text(), "영상 끝")


    def test_typing_after_a_click_replaces_the_time(self) -> None:
        end = TimeSpinBox()
        end.setRange(0, 120)
        end.setSpecialValueText("영상 끝")
        end.setValue(0)
        end.show()
        self.app.processEvents()
        # Clicking "영상 끝" and typing a time gives that time (it used to be refused).
        QTest.mouseClick(end.lineEdit(), Qt.MouseButton.LeftButton)
        self.app.processEvents()
        QTest.keyClicks(end.lineEdit(), "1:00")
        end.interpretText()
        self.assertEqual(end.value(), 60)
        # Clicking "1:00" and typing replaces it rather than appending.
        QTest.mouseClick(end.lineEdit(), Qt.MouseButton.LeftButton)
        self.app.processEvents()
        QTest.keyClicks(end.lineEdit(), "45")
        end.interpretText()
        self.assertEqual(end.value(), 45)
        # A time past the end becomes the end when the field is left.
        QTest.mouseClick(end.lineEdit(), Qt.MouseButton.LeftButton)
        self.app.processEvents()
        QTest.keyClicks(end.lineEdit(), "5:00")
        end.clearFocus()
        self.app.processEvents()
        self.assertEqual(end.value(), 120)
        # Arrow keys still step from "영상 끝".
        end.setValue(0)
        end.setFocus()
        QTest.keyClick(end, Qt.Key.Key_Up)
        self.assertEqual(end.value(), 1)
        end.close()


if __name__ == "__main__":
    unittest.main()
