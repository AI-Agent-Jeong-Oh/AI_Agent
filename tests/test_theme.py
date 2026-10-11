from __future__ import annotations

import re
import unittest

from PySide6.QtWidgets import QApplication

from video_agent.ui import theme


def luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class KrdsThemeTests(unittest.TestCase):
    """KRDS contrast rules: text 4.5:1 (WCAG AA), input borders and controls 3:1."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_text_colors_meet_aa_on_page_and_cards(self) -> None:
        for text in (theme.TEXT, theme.TEXT_SUB, theme.PRIMARY):
            for background in (theme.SURFACE, theme.PAGE, theme.PRIMARY_SOFT):
                with self.subTest(text=text, background=background):
                    self.assertGreaterEqual(contrast(text, background), 4.5)
        # The muted step passes on white only, so it is used on white cards only.
        self.assertGreaterEqual(contrast(theme.TEXT_MUTED, theme.SURFACE), 4.5)
        for status in (theme.SUCCESS, theme.DANGER, theme.WARNING_TEXT):
            self.assertGreaterEqual(contrast(status, theme.SURFACE), 4.5)

    def test_buttons_and_review_card_text_meet_aa(self) -> None:
        self.assertGreaterEqual(contrast(theme.GRAY_0, theme.PRIMARY_FILL), 4.5)
        self.assertGreaterEqual(contrast(theme.GRAY_0, theme.PRIMARY_60), 4.5)
        self.assertGreaterEqual(contrast(theme.GRAY_50, theme.GRAY_10), 3.0)  # disabled label
        self.assertGreaterEqual(contrast(theme.GRAY_0, theme.NAVY), 4.5)
        self.assertGreaterEqual(contrast(theme.GRAY_20, theme.NAVY), 4.5)

    def test_input_borders_and_check_marks_meet_3_to_1(self) -> None:
        self.assertGreaterEqual(contrast(theme.INPUT_BORDER, theme.SURFACE), 3.0)
        self.assertGreaterEqual(contrast(theme.PRIMARY_FILL, theme.SURFACE), 3.0)
        self.assertGreaterEqual(contrast(theme.WARNING_DOT, theme.SURFACE), 3.0)

    def test_every_banner_is_readable(self) -> None:
        for kind in ("sample", "warning", "test_double", "real_model", "local", "running",
                     "failed", "neutral"):
            style = theme.banner_style(kind)
            background = re.search(r"background:(#[0-9a-f]{6})", style).group(1)
            color = re.search(r"(?<!-)color:(#[0-9a-f]{6})", style).group(1)
            with self.subTest(kind=kind):
                self.assertGreaterEqual(contrast(color, background), 4.5)

    def test_bundled_typeface_and_body_size(self) -> None:
        self.assertEqual(theme.load_fonts(), theme.FONT_FAMILY)
        self.assertTrue((theme.FONTS_DIR / "LICENSE-PretendardGOV.txt").is_file())
        self.assertGreaterEqual(theme.BODY_PX, 16)
        sheet = theme.build_stylesheet(lambda value: value)
        self.assertIn('font-family: "Pretendard GOV"', sheet)
        self.assertIn("font-size: 16px", sheet)


if __name__ == "__main__":
    unittest.main()
