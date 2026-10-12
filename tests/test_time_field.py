from __future__ import annotations

import unittest

from PySide6.QtGui import QValidator
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


if __name__ == "__main__":
    unittest.main()
