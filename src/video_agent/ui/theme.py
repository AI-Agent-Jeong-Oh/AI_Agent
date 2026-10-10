"""Shared colors and Qt style sheet for the desktop UI.

The palette follows the team demo page (white cards, one blue accent, gray text steps).
Result-origin banners keep their own colors so sample, test and real-model output stay
visibly distinct.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QPushButton, QWidget

ASSETS_DIR = Path(__file__).with_name("assets")
FONT_FAMILY = "Apple SD Gothic Neo"

PRIMARY = "#0b50d0"
PRIMARY_DARK = "#083891"
PRIMARY_SOFT = "#eef4fe"
PRIMARY_DISABLED = "#b1cefb"
TEXT = "#1e2124"
TEXT_SUB = "#464c53"
TEXT_MUTED = "#6d7882"
TEXT_FAINT = "#8a949e"
BORDER = "#d0d5dd"
LINE = "#e4e7ec"
PAGE = "#f4f5f6"
SURFACE = "#ffffff"
SURFACE_ALT = "#f9fafb"
VIDEO_BACKGROUND = "#1b2026"
NAVY = "#14284b"

SUCCESS = "#1f6b3a"
DANGER = "#bd2c0f"
WARNING_TEXT = "#8a4b00"

_BANNERS = {
    "sample": "background:#fff0d7;color:#813f00;border:1px solid #e6a654;",
    "test_double": "background:#f5e9ff;color:#5d2b7a;border:1px solid #b98bd0;",
    "real_model": "background:#e4f4eb;color:#125d35;border:1px solid #75b991;",
    "local": "background:#eef4fe;color:#083891;border:1px solid #b1cefb;",
    "running": "background:#fff6d8;color:#6e5200;border:1px solid #d6b858;",
    "failed": "background:#fdefec;color:#9f240c;border:1px solid #f1b5a7;",
    "neutral": "background:#f2f4f7;color:#464c53;border:1px solid #d0d5dd;",
}


def banner_style(kind: str) -> str:
    """Colors for an origin or state banner; unknown kinds fall back to neutral."""
    return _BANNERS.get(kind, _BANNERS["neutral"])


def status_color(state: str) -> str:
    return {"ok": SUCCESS, "error": DANGER, "idle": TEXT_MUTED}.get(state, TEXT_MUTED)


def set_variant(widget: QWidget, variant: str) -> None:
    """Pick a named look from the style sheet (primary, chip, judgement, ...)."""
    widget.setProperty("variant", variant)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def mark_primary(button: QPushButton) -> None:
    """Show the main action of a section as a filled blue button."""
    set_variant(button, "primary")


def set_segment(button: QPushButton, position: int, count: int) -> None:
    """Join buttons into one segmented control; only the outer corners are rounded."""
    side = "first" if position == 0 else "last" if position == count - 1 else "middle"
    button.setProperty("segment", side)
    button.style().unpolish(button)
    button.style().polish(button)


def build_stylesheet(scaled: Callable[[int], int], font_point_size: float) -> str:
    """Style sheet for one zoom level.

    The text size is part of the sheet because Qt stops passing the window font down to
    widgets that a style sheet styles; without it the zoom would leave body text unchanged.
    """
    s = scaled
    groove = s(4)
    knob = s(12)
    if (knob + 4 - groove) % 2:
        knob += 1
    knob_margin = (knob + 4 - groove) // 2
    down = (ASSETS_DIR / "chevron-down.svg").as_posix()
    up = (ASSETS_DIR / "chevron-up.svg").as_posix()
    return f"""
    QWidget {{ font-family: "{FONT_FAMILY}"; font-size: {font_point_size:.1f}pt; color: {TEXT}; }}
    QMainWindow, QDialog {{ background: {PAGE}; }}
    QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport,
    QScrollArea > QWidget#qt_scrollarea_viewport > QWidget {{ background: transparent; }}
    QSplitter::handle {{ background: transparent; }}

    QWidget#appHeader {{ background: {SURFACE}; border-bottom: 1px solid {LINE}; }}
    QLabel#organization {{ font-size: {s(16)}px; font-weight: 700; color: {TEXT}; }}
    QLabel#title {{ font-size: {s(14)}px; color: {TEXT_SUB}; }}
    QLabel#headerHint {{ color: {TEXT_MUTED}; }}
    QFrame#headerDivider {{ background: {BORDER}; border: none;
                            min-height: {s(14)}px; max-height: {s(14)}px; }}
    QWidget#stepBar {{ background: {SURFACE}; border-bottom: 1px solid {LINE}; }}
    QLabel#placeholder {{ color: {TEXT_MUTED}; border: 1px dashed {BORDER};
                          border-radius: {s(6)}px; padding: {s(7)}px; background: {SURFACE}; }}
    QLabel#modeBanner {{ border-radius: {s(6)}px; padding: {s(7)}px; font-weight: 700; }}

    QGroupBox {{ font-weight: 700; color: {TEXT}; background: {SURFACE};
                 border: 1px solid {LINE}; border-radius: {s(8)}px;
                 margin-top: 0; padding-top: {s(23)}px; }}
    QGroupBox::title {{ subcontrol-origin: border; subcontrol-position: top left;
                        left: {s(12)}px; top: {s(7)}px; padding: 0; color: {TEXT}; }}

    QPushButton {{ background: {SURFACE}; color: {TEXT_SUB}; border: 1px solid {BORDER};
                   border-radius: {s(6)}px; padding: {s(5)}px {s(14)}px; }}
    QPushButton:hover {{ background: {SURFACE_ALT}; border-color: #98a2b3; }}
    QPushButton:pressed {{ background: #eaecf0; }}
    QPushButton:checked {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; border-color: {PRIMARY}; }}
    QPushButton:disabled {{ background: {SURFACE_ALT}; color: #b1b8be; border-color: {LINE}; }}
    QPushButton[variant="primary"] {{ background: {PRIMARY}; color: #ffffff;
                                      border-color: {PRIMARY}; font-weight: 600; }}
    QPushButton[variant="primary"]:hover {{ background: {PRIMARY_DARK}; border-color: {PRIMARY_DARK}; }}
    QPushButton[variant="primary"]:disabled {{ background: {PRIMARY_DISABLED};
                                               border-color: {PRIMARY_DISABLED}; color: #ffffff; }}
    QPushButton#stepButton {{ border: none; background: transparent; color: {TEXT_SUB};
                              padding: {s(2)}px {s(8)}px {s(6)}px; }}
    QPushButton#stepButton:hover {{ color: {TEXT}; background: transparent; }}
    QPushButton#stepButton:checked {{ color: {PRIMARY}; font-weight: 700; background: transparent; }}

    QLabel#fieldLabel {{ color: {TEXT_SUB}; }}
    QTabBar#cameraTabs::tab {{ background: transparent; color: {TEXT_SUB}; border: none;
                               border-bottom: 2px solid transparent;
                               padding: {s(4)}px {s(10)}px; margin-right: {s(2)}px; }}
    QTabBar#cameraTabs::tab:selected {{ color: {PRIMARY}; border-bottom-color: {PRIMARY};
                                        font-weight: 600; }}
    QTabBar#cameraTabs::tab:hover:!selected {{ color: {TEXT}; }}
    QPushButton#overviewToggle {{ padding: {s(3)}px {s(9)}px; color: {TEXT_SUB}; }}
    QPushButton#overviewToggle:checked {{ background: {PRIMARY_SOFT}; color: {PRIMARY};
                                          border-color: {PRIMARY}; }}
    QLabel#infoMark {{ color: {PRIMARY}; padding: 0 {s(2)}px; }}
    QPushButton[variant="chip"] {{ padding: {s(3)}px {s(9)}px; color: {TEXT_SUB}; }}
    QPushButton[variant="chip"]:checked {{ background: {PRIMARY_SOFT}; color: {PRIMARY};
                                           border-color: {PRIMARY}; font-weight: 600; }}
    QPushButton[segment] {{ border-radius: 0; padding: {s(4)}px {s(12)}px; }}
    QPushButton[segment="middle"], QPushButton[segment="last"] {{ border-left: none; }}
    QPushButton[segment="first"] {{ border-top-left-radius: {s(6)}px;
                                    border-bottom-left-radius: {s(6)}px; }}
    QPushButton[segment="last"] {{ border-top-right-radius: {s(6)}px;
                                   border-bottom-right-radius: {s(6)}px; }}
    QPushButton[segment]:checked {{ background: {PRIMARY_SOFT}; color: {PRIMARY};
                                    font-weight: 600; }}

    QFrame#reviewCard {{ background: {SURFACE}; border: 1px solid {NAVY};
                         border-radius: {s(8)}px; }}
    QWidget#reviewCardHeader {{ background: {NAVY}; border-top-left-radius: {s(7)}px;
                                border-top-right-radius: {s(7)}px; }}
    QLabel#reviewCardTitle {{ color: #ffffff; font-weight: 700; }}
    QLabel#reviewCardNote {{ color: #b4bcc8; }}
    QPushButton#bookmarkButton {{ background: transparent; color: #ffffff;
                                  border: 1px solid #8a95a1; padding: {s(3)}px {s(10)}px; }}
    QPushButton#bookmarkButton:hover {{ border-color: #ffffff; }}
    QPushButton#bookmarkButton:checked {{ background: #ffffff; color: {NAVY};
                                          border-color: #ffffff; font-weight: 600; }}
    QPushButton#bookmarkButton:disabled {{ color: #8a95a1; border-color: #4a5a78; }}
    QRadioButton[variant="judgement"], QRadioButton[variant="judgementQuiet"] {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {s(6)}px;
        padding: {s(4)}px {s(8)}px; }}
    QRadioButton[variant="judgementQuiet"] {{ background: {SURFACE_ALT}; color: {TEXT_MUTED}; }}
    QRadioButton[variant="judgement"]:checked, QRadioButton[variant="judgementQuiet"]:checked {{
        background: {PRIMARY_SOFT}; border-color: {PRIMARY}; color: {PRIMARY}; font-weight: 600; }}
    QRadioButton[variant="judgement"]:disabled, QRadioButton[variant="judgementQuiet"]:disabled {{
        color: {TEXT_FAINT}; border-color: {LINE}; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
        background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: {s(6)}px; padding: {s(2)}px {s(8)}px;
        selection-background-color: #c7d7f5; selection-color: {TEXT}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
    QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {PRIMARY}; }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled,
    QDoubleSpinBox:disabled, QSpinBox:disabled {{ background: {PAGE}; color: {TEXT_FAINT}; }}
    QComboBox::drop-down {{ border: none; width: {s(20)}px; subcontrol-origin: padding;
                            subcontrol-position: center right; }}
    QComboBox::down-arrow {{ image: url("{down}"); width: {s(12)}px; height: {s(12)}px; }}
    QDoubleSpinBox::up-button, QSpinBox::up-button {{ subcontrol-origin: border;
        subcontrol-position: top right; width: {s(18)}px; border: none; }}
    QDoubleSpinBox::down-button, QSpinBox::down-button {{ subcontrol-origin: border;
        subcontrol-position: bottom right; width: {s(18)}px; border: none; }}
    QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{ image: url("{up}");
        width: {s(10)}px; height: {s(10)}px; }}
    QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{ image: url("{down}");
        width: {s(10)}px; height: {s(10)}px; }}
    QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER};
                                   selection-background-color: {PRIMARY_SOFT};
                                   selection-color: {TEXT}; }}

    QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {s(6)}px;
                   padding: {s(1)}px; outline: 0; }}
    QListWidget::item {{ padding: {s(3)}px {s(6)}px; border-bottom: 1px solid {LINE}; }}
    QListWidget::item:selected {{ background: {PRIMARY_SOFT}; color: {TEXT};
                                  border-left: {s(3)}px solid {PRIMARY}; }}
    QListWidget::item:hover:!selected {{ background: {SURFACE_ALT}; }}

    QProgressBar {{ background: {PAGE}; border: 1px solid {BORDER}; border-radius: {s(6)}px;
                    text-align: center; color: {TEXT_SUB}; }}
    QProgressBar::chunk {{ background: {PRIMARY}; border-radius: {s(5)}px; }}

    QScrollBar:vertical {{ background: transparent; width: {s(10)}px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: {s(10)}px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: #cdd1d5; border-radius: {s(4)}px;
                                   min-height: {s(28)}px; margin: {s(1)}px; }}
    QScrollBar::handle:horizontal {{ background: #cdd1d5; border-radius: {s(4)}px;
                                     min-width: {s(28)}px; margin: {s(1)}px; }}
    QScrollBar::handle:hover {{ background: #98a2b3; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QSlider::groove:horizontal {{ height: {groove}px; background: {LINE};
                                  border-radius: {groove // 2}px; }}
    QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: {groove // 2}px; }}
    QSlider::handle:horizontal {{ background: {SURFACE}; border: 2px solid {PRIMARY};
                                  width: {knob}px; margin: -{knob_margin}px 0;
                                  border-radius: {(knob + 4) // 2}px; }}

    QStatusBar {{ background: {SURFACE}; color: transparent; border-top: 1px solid {LINE};
                  min-height: {s(46)}px; }}
    QLabel#statusMessage {{ color: {TEXT_SUB}; padding-left: {s(16)}px; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{ background: {TEXT}; color: #ffffff; border: none; padding: {s(5)}px; }}
    """
