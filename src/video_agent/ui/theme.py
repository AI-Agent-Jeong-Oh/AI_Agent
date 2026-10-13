"""Shared colors, type scale and Qt style sheet for the desktop UI.

Values follow KRDS (대한민국 정부 디자인시스템): the light-mode color tokens and the
type, spacing, size and radius tokens of the official HTML component kit (krds-uiux
1.1.0), with the team demo page for layout. Body text is 16px, KRDS's minimum, so the
dense review screens still fit; key content uses KRDS body-medium (17px).

Result-origin banners use the KRDS system colors and always carry text, so sample, test
and real-model output never depend on color alone.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable, Sequence
from html import escape
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFormLayout,
    QPushButton,
    QStyledItemDelegate,
    QWidget,
)

ASSETS_DIR = Path(__file__).with_name("assets")
FONTS_DIR = ASSETS_DIR / "fonts"
# Lucide line icons (ISC, LICENSE-lucide.txt), the same style as the team demo page.
ICONS_DIR = ASSETS_DIR / "icons"
_ICON_SIZES = (16, 20, 24, 32, 40, 48)
# KRDS standard typeface (SIL OFL 1.1, bundled unmodified as the official subset build).
FONT_FAMILY = "Pretendard GOV"
FALLBACK_FONT_FAMILY = "Apple SD Gothic Neo"
_FONT_FILES = (
    "PretendardGOV-Regular.subset.woff2",
    "PretendardGOV-Medium.subset.woff2",
    "PretendardGOV-Bold.subset.woff2",
)

# KRDS type scale in px at 100% (weights 400 / 500 / 700 only).
BODY_PX = 16  # KRDS minimum body size
CONTENT_PX = 17  # body-medium: clue sentences and other key content
LABEL_PX = 15  # label-small: field labels and secondary information
CAPTION_PX = 13  # label-xsmall: only for counts and hints that must stay compact
HEADING_PX = 17  # heading-xsmall: card titles
SUBHEADING_PX = 16  # bold body: sections inside a card

# KRDS light-mode color tokens.
GRAY_0 = "#ffffff"
GRAY_5 = "#f4f5f6"
GRAY_10 = "#e6e8ea"
GRAY_20 = "#cdd1d5"
GRAY_30 = "#b1b8be"
GRAY_40 = "#8a949e"
GRAY_50 = "#6d7882"
GRAY_60 = "#58616a"
GRAY_70 = "#464c53"
GRAY_80 = "#33363d"
GRAY_90 = "#1e2124"
PRIMARY_5 = "#ecf2fe"
PRIMARY_10 = "#d8e5fd"
PRIMARY_20 = "#b1cefb"
PRIMARY_50 = "#256ef4"
PRIMARY_60 = "#0b50d0"
PRIMARY_70 = "#083891"
SECONDARY_5 = "#eef2f7"
SECONDARY_10 = "#d6e0eb"
SECONDARY_20 = "#bacbde"
SECONDARY_50 = "#346fb2"
SECONDARY_60 = "#1c589c"
SECONDARY_70 = "#063a74"
SECONDARY_80 = "#052b57"
SECONDARY_90 = "#031f3f"

# Roles used by the screens.
# Selection and emphasis use the KRDS secondary (navy) scale, the police colour of the
# header, step numbers and box lines, instead of the bright primary blue.
PRIMARY = SECONDARY_70  # selected text, tabs, links
PRIMARY_FILL = SECONDARY_80  # main button, check boxes, radio buttons, checked tiles
PRIMARY_HOVER = SECONDARY_70
PRIMARY_DARK = SECONDARY_90
PRIMARY_SOFT = SECONDARY_5  # selected rows, checked tiles and chips
PRIMARY_DISABLED = SECONDARY_20
PRIMARY_SELECTION = SECONDARY_10  # text selection in input fields
TEXT = GRAY_90  # text-basic
TEXT_BOLDER = "#131416"  # gray-95, text-bolder: table header
TEXT_SUB = GRAY_70  # text-subtle
TEXT_MUTED = GRAY_50  # lowest text step: 4.5:1 on white only, never on gray or tinted rows
TEXT_FAINT = GRAY_40  # text-disabled only
INPUT_BORDER = GRAY_50  # KRDS input border, 3:1 against white
BORDER = GRAY_20  # border-gray-light: cards, lists
LINE = GRAY_10  # dividers inside cards
PAGE = GRAY_5  # surface-gray-subtler
SURFACE = GRAY_0
SURFACE_ALT = GRAY_5
VIDEO_BACKGROUND = "#1b2026"
NAVY = SECONDARY_80  # police navy, KRDS secondary-80
# Boxes are thin grey frames (radius 4px) with a title row over a divider, as in work
# programs; table headers are grey (gray-5, bold). Navy is kept for actions and choices.
BOX_ACCENT = BORDER
TABLE_HEAD = GRAY_5
TABLE_HEAD_LINE = BORDER

SUCCESS = "#267337"  # success-60
DANGER = "#bd2c0f"  # danger-60
WARNING_TEXT = "#8a5c00"  # warning-60
WARNING_DOT = "#c78500"  # warning-40, status mark beside "추정"

# KRDS system colors: surface 5, text 60, border 10. Every banner also has words.
_BANNERS = {
    "sample": "background:#fff3db;color:#8a5c00;border:1px solid #ffe0a3;",
    "warning": "background:#fff3db;color:#8a5c00;border:1px solid #ffe0a3;",
    "test_double": "background:#eef2f7;color:#052b57;border:1px solid #d6e0eb;",
    "real_model": "background:#eaf6ec;color:#267337;border:1px solid #d8eedd;",
    "local": "background:#eef2f7;color:#052b57;border:1px solid #d6e0eb;",
    "running": "background:#e7f4fe;color:#096ab3;border:1px solid #d3ebfd;",
    "failed": "background:#fdefec;color:#bd2c0f;border:1px solid #fcdfd9;",
    "neutral": "background:#f4f5f6;color:#464c53;border:1px solid #e6e8ea;",
}

# Compact status line above the steps (analysis mode, run and result state): KRDS status
# text 60 with a 50 dot, no background, always with words.
_STATUS = {
    "sample": ("#8a5c00", "#c78500"),
    "warning": ("#8a5c00", "#c78500"),
    "test_double": ("#052b57", "#346fb2"),
    "real_model": ("#267337", "#228738"),
    "local": (SECONDARY_80, SECONDARY_50),
    "running": ("#096ab3", "#0b78cb"),
    "failed": ("#bd2c0f", "#de3412"),
    "neutral": (GRAY_70, GRAY_50),
}

_fonts_loaded: bool | None = None


def load_fonts() -> str:
    """Register the bundled typeface once; returns the family the style sheet uses."""
    global _fonts_loaded
    if _fonts_loaded is None:
        ids = [QFontDatabase.addApplicationFont(str(FONTS_DIR / name)) for name in _FONT_FILES]
        _fonts_loaded = all(font_id >= 0 for font_id in ids)
    return FONT_FAMILY if _fonts_loaded else FALLBACK_FONT_FAMILY


def banner_style(kind: str) -> str:
    """Colors for an origin or state banner; unknown kinds fall back to neutral."""
    return _BANNERS.get(kind, _BANNERS["neutral"])


def status_line_colors(kind: str) -> tuple[str, str]:
    """(text, dot) colors for the status line; unknown kinds fall back to neutral."""
    return _STATUS.get(kind, _STATUS["neutral"])


def status_color(state: str) -> str:
    return {"ok": SUCCESS, "error": DANGER, "idle": TEXT_MUTED}.get(state, TEXT_MUTED)


def set_variant(widget: QWidget, variant: str) -> None:
    """Pick a named look from the style sheet (primary, chip, judgement, ...)."""
    widget.setProperty("variant", variant)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    if isinstance(widget, QPushButton):
        _apply_icon(widget)


@lru_cache(maxsize=None)
def icon(name: str, color: str = "") -> QIcon:
    """A bundled line icon drawn in a KRDS color, lighter when its button is disabled."""
    source = (ICONS_DIR / f"{name}.svg").read_text(encoding="utf-8")
    result = QIcon()
    for mode, tint in ((QIcon.Mode.Normal, color or TEXT_SUB), (QIcon.Mode.Disabled, GRAY_40)):
        renderer = QSvgRenderer(QByteArray(source.replace("currentColor", tint).encode()))
        for size in _ICON_SIZES:
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter, QRectF(0, 0, size, size))
            painter.end()
            result.addPixmap(pixmap, mode)
    return result


def _release_qt_objects() -> None:
    """At exit, free cached icons and windows while the application still exists.

    Python may otherwise drop them after QApplication during shutdown, which crashes.
    """
    icon.cache_clear()
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


atexit.register(_release_qt_objects)


def set_icon(button: QPushButton, name: str, color: str = "") -> None:
    """Give a button an icon that turns white when the button becomes the blue main action."""
    button.setProperty("iconName", name)
    button.setProperty("iconColor", color)
    _apply_icon(button)


def _apply_icon(button: QPushButton) -> None:
    name = button.property("iconName")
    if not name:
        return
    if button.property("variant") == "primary":
        color = GRAY_0
    elif button.isCheckable() and button.isChecked():
        color = PRIMARY
    else:
        color = button.property("iconColor") or TEXT_SUB
    button.setIcon(icon(name, color))


def dot_pixmap(color: str, diameter: int) -> QPixmap:
    """Small filled circle for status lines (drawn, so it does not depend on a font glyph)."""
    pixmap = QPixmap(diameter, diameter)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, diameter, diameter)
    painter.end()
    return pixmap


def mark_primary(button: QPushButton) -> None:
    """Show the main action of a section as a filled blue button."""
    set_variant(button, "primary")


def set_primary(button: QPushButton, primary: bool) -> None:
    """Blue only for the one action to take next; the other buttons stay plain."""
    if (button.property("variant") == "primary") != primary:
        set_variant(button, "primary" if primary else "")


class _OptionFitter(QObject):
    """Keeps a combo's open list as wide as its longest option (items or zoom change)."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self._combo = combo
        combo.installEventFilter(self)
        combo.model().rowsInserted.connect(self.fit)
        combo.model().modelReset.connect(self.fit)
        self.fit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.FontChange:
            self.fit()
        return False

    def fit(self, *_args: object) -> None:
        combo = self._combo
        widest = max(
            (combo.fontMetrics().horizontalAdvance(combo.itemText(i)) for i in range(combo.count())),
            default=0,
        )
        # Item padding (12px each side), list padding and frame, and room for a scroll bar.
        combo.view().setMinimumWidth(widest + 2 * 12 + 2 * 4 + 2 + 16)


def polish_combo(combo: QComboBox) -> None:
    """Use a styled row delegate so the drop-down list follows the style sheet on macOS too.

    The open list is made at least as wide as its longest option, so no option is cut even
    when the closed box is narrow.
    """
    combo.setItemDelegate(QStyledItemDelegate(combo))
    combo.view().setMouseTracking(True)
    combo.view().setTextElideMode(Qt.TextElideMode.ElideNone)
    _OptionFitter(combo)


def polish_spin_box(spin: QAbstractSpinBox) -> None:
    """Inset the number like other fields (macOS draws spin box text at the very edge)."""
    spin.lineEdit().setTextMargins(8, 0, 0, 0)


def polish_form(form: QFormLayout) -> None:
    """Same form layout on every platform: left labels, fields filling the row.

    macOS keeps fields at their preferred width and right-aligns labels by default, which
    made the case name and date fields short and cut their example text.
    """
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)


def label_value_table(rows: Sequence[tuple[str, str]]) -> str:
    """Rich-text grid of label and value cells (the KRDS form-table look) for read-only views."""
    cells = "".join(
        f'<tr><td width="26%" bgcolor="{GRAY_5}" style="color:{TEXT_SUB};">{escape(label)}</td>'
        f'<td bgcolor="{SURFACE}" style="color:{TEXT};">'
        f'{escape(value).replace(chr(10), "<br>")}</td></tr>'
        for label, value in rows
    )
    return f'<table width="100%" cellspacing="1" cellpadding="6" bgcolor="{LINE}">{cells}</table>'


def mark_compact(widget: QWidget) -> None:
    """KRDS xsmall (32px) for secondary actions in dense areas; keeps its variant."""
    widget.setProperty("density", "compact")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_segment(button: QPushButton, position: int, count: int) -> None:
    """Join buttons into one segmented control; only the outer corners are rounded."""
    side = "first" if position == 0 else "last" if position == count - 1 else "middle"
    button.setProperty("segment", side)
    button.style().unpolish(button)
    button.style().polish(button)


def build_stylesheet(scaled: Callable[[int], int]) -> str:
    """Style sheet for one zoom level.

    Sizes are in px like KRDS. The text size is part of the sheet because Qt stops
    passing the window font down to widgets that a style sheet styles.
    Heights: inputs and buttons 40px (KRDS small), compact buttons 32px (xsmall).
    """
    s = scaled
    family = load_fonts()
    groove = s(4)
    knob = s(16)
    if (knob + 4 - groove) % 2:
        knob += 1
    knob_margin = (knob + 4 - groove) // 2
    down = (ASSETS_DIR / "chevron-down.svg").as_posix()
    up = (ASSETS_DIR / "chevron-up.svg").as_posix()
    check = (ASSETS_DIR / "check.svg").as_posix()
    # Content heights so that border + padding + content = 40px / 32px.
    field_pad = s(8)
    field_min = s(40) - 2 * field_pad - 2
    compact_pad = s(4)
    compact_min = s(32) - 2 * compact_pad - 2
    return f"""
    QWidget {{ font-family: "{family}", "{FALLBACK_FONT_FAMILY}"; font-size: {s(BODY_PX)}px;
               color: {TEXT}; }}
    QMainWindow, QDialog {{ background: {PAGE}; }}
    QDialog#startupDialog {{ background: {NAVY}; }}
    QFrame#startupPanel {{
        background: qradialgradient(cx:0.5, cy:0.48, radius:0.9,
            fx:0.5, fy:0.42, stop:0 {SECONDARY_60}, stop:0.58 {NAVY}, stop:1 {SECONDARY_90});
        border: 1px solid {SECONDARY_50}; border-radius: {s(12)}px;
    }}
    QLabel#startupOrganization {{ color: {GRAY_0}; font-size: {s(19)}px; font-weight: 700; }}
    QLabel#startupTitle {{ color: {SECONDARY_10}; font-size: {s(CONTENT_PX)}px; }}
    QLabel#startupMascots {{ background: transparent; }}
    QLabel#startupStatus {{ color: {GRAY_0}; font-size: {s(LABEL_PX)}px; }}
    QProgressBar#startupProgress {{ background: {SECONDARY_70}; border: none;
        border-radius: {s(4)}px; min-height: {s(8)}px; max-height: {s(8)}px; }}
    QProgressBar#startupProgress::chunk {{ background: {SECONDARY_10}; border-radius: {s(4)}px; }}
    QDialog#loginDialog {{ background: {PAGE}; }}
    QFrame#loginCard {{ background: {SURFACE}; border: 1px solid {BORDER};
        border-radius: {s(10)}px; }}
    QLabel#loginOrganization {{ color: {TEXT}; font-size: {s(18)}px; font-weight: 700; }}
    QLabel#loginAppName {{ color: {TEXT_SUB}; font-size: {s(LABEL_PX)}px; }}
    QLabel#loginTitle {{ color: {TEXT}; font-size: {s(22)}px; font-weight: 700; }}
    QLabel#loginIntro {{ color: {TEXT_SUB}; }}
    QLabel#loginError {{ color: {DANGER}; background: #fdefec; border: 1px solid #fcdfd9;
        border-radius: {s(5)}px; padding: {s(8)}px; }}
    QLabel#loginScope {{ color: {TEXT_SUB}; background: {GRAY_5}; border-radius: {s(5)}px;
        padding: {s(10)}px; font-size: {s(LABEL_PX)}px; }}
    QDialog#messageDialog {{ background: {SURFACE}; }}
    /* Report draft: centred title between double rules and a ruled header table. */
    QLabel#reportTitle {{ font-size: {s(20)}px; font-weight: 700; color: {TEXT};
        border-top: 3px double {GRAY_70}; border-bottom: 3px double {GRAY_70}; padding: {s(6)}px 0; }}
    QWidget#reportMeta {{ background: {SURFACE}; border: 1px solid {GRAY_40}; }}
    QLabel#reportMetaHead {{ background: {GRAY_5}; border: 1px solid {GRAY_20}; font-weight: 700;
        font-size: {s(LABEL_PX)}px; padding: {s(4)}px {s(8)}px; min-width: {s(64)}px; }}
    QLabel#reportMetaCell {{ background: {SURFACE}; border: 1px solid {GRAY_20};
        font-size: {s(LABEL_PX)}px; padding: {s(4)}px {s(8)}px; }}
    QLineEdit#reportAuthor {{ border: 1px solid {GRAY_20}; border-radius: 0; min-height: 0;
        padding: {s(4)}px {s(8)}px; font-size: {s(LABEL_PX)}px; }}
    QTextEdit#plainTable {{ border: none; background: transparent; padding: 0; }}
    QLabel#messageTitle {{ font-size: {s(HEADING_PX)}px; font-weight: 700; color: {TEXT}; }}
    QLabel#messageText {{ color: {TEXT}; }}
    QWidget#messageDetails {{ background: {GRAY_5}; border-radius: {s(6)}px; }}
    QLabel#messageDetail {{ font-size: {s(LABEL_PX)}px; color: {TEXT_SUB}; }}
    QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport,
    QScrollArea > QWidget#qt_scrollarea_viewport > QWidget {{ background: transparent; }}
    QSplitter::handle {{ background: transparent; }}

    QWidget#appHeader {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
    QLabel#organization {{ font-size: {s(19)}px; font-weight: 700; color: {TEXT}; }}
    QLabel#title {{ font-size: {s(CONTENT_PX)}px; color: {TEXT_SUB}; }}
    QLabel#headerHint {{ font-size: {s(LABEL_PX)}px; color: {TEXT_SUB}; }}
    QLabel#headerCase {{ font-size: {s(LABEL_PX)}px; font-weight: 700; color: {TEXT}; }}
    QLabel#headerUser {{ font-size: {s(LABEL_PX)}px; font-weight: 700; color: {PRIMARY}; }}
    QLabel#statusAttention {{ color: {WARNING_TEXT}; padding-left: {s(16)}px; }}
    QFrame#headerDivider {{ background: {BORDER}; border: none;
                            min-height: {s(16)}px; max-height: {s(16)}px; }}
    QWidget#stepBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
    QLabel#placeholder {{ color: {TEXT_SUB}; border: 1px dashed {GRAY_30};
                          border-radius: {s(2)}px; padding: {s(16)}px; background: {SURFACE}; }}
    QFrame#runBar {{ background: {SURFACE}; border: 1px solid {BORDER};
                     border-radius: {s(4)}px; }}
    QLabel#emptyState {{ color: {TEXT_SUB}; padding: {s(8)}px 0; }}
    QLabel#modeBanner {{ background: transparent; border: none; padding: 0;
                         font-size: {s(LABEL_PX)}px; font-weight: 700; }}
    QLabel#modeDot {{ background: transparent; font-size: {s(10)}px; }}

    QGroupBox {{ font-size: {s(HEADING_PX)}px; font-weight: 700; color: {TEXT};
                 background: {SURFACE}; border: 1px solid {BORDER};
                 border-radius: {s(4)}px;
                 margin-top: 0; padding: {s(40)}px {s(4)}px {s(4)}px {s(4)}px; }}
    QGroupBox::title {{ subcontrol-origin: border; subcontrol-position: top left;
                        left: {s(16)}px; top: {s(12)}px; padding: 0; color: {TEXT}; }}

    QPushButton {{ icon-size: {s(16)}px; }}
    /* Action buttons are flat grey with bold words, like the bottom [사건 저장]/[이전]. */
    QPushButton {{ background: {GRAY_10}; color: {TEXT}; border: 1px solid {GRAY_10};
                   border-radius: {s(6)}px; padding: {compact_pad}px {s(12)}px;
                   min-height: {compact_min}px; font-size: {s(LABEL_PX)}px; font-weight: 700; }}
    QPushButton:hover {{ background: {GRAY_20}; border-color: {GRAY_20}; }}
    QPushButton:pressed {{ background: {GRAY_30}; border-color: {GRAY_30}; }}
    QPushButton:checked {{ background: {PRIMARY_SOFT}; color: {PRIMARY};
                           border-color: {PRIMARY_FILL}; font-weight: 700; }}
    QPushButton:disabled {{ background: {GRAY_10}; color: {GRAY_40}; border-color: {GRAY_10}; }}
    /* Choices and filters (case type, playback range, view menu) keep a white outlined
       look, so they do not read as actions. */
    QPushButton[variant="chip"], QPushButton[segment], QPushButton#viewButton {{
        background: {SURFACE}; border-color: {GRAY_30}; font-weight: 400; }}
    QPushButton[variant="chip"]:hover, QPushButton[segment]:hover, QPushButton#viewButton:hover {{
        background: {GRAY_5}; border-color: {GRAY_50}; }}
    QPushButton[variant="chip"]:checked, QPushButton[segment]:checked {{
        background: {PRIMARY_SOFT}; color: {PRIMARY}; border-color: {PRIMARY_FILL};
        font-weight: 700; }}
    QPushButton[variant="chip"]:disabled, QPushButton[segment]:disabled,
    QPushButton#viewButton:disabled {{ background: {GRAY_5}; border-color: {GRAY_20}; }}
    QPushButton#analysisOption {{ background: {SURFACE}; border: 1px solid {GRAY_30};
        border-radius: {s(8)}px; padding: {s(6)}px {s(10)}px; color: {TEXT};
        min-height: {s(42)}px; font-weight: 400; text-align: center; }}
    QPushButton#analysisOption:hover {{ background: {GRAY_5}; border-color: {PRIMARY_FILL}; }}
    QPushButton#analysisOption:checked {{ background: {PRIMARY_SOFT};
        border: 2px solid {PRIMARY_FILL}; color: {PRIMARY}; font-weight: 700; }}
    QPushButton#analysisOption:disabled {{ background: {GRAY_5};
        border-color: {GRAY_20}; color: {TEXT_FAINT}; }}
    QPushButton[variant="primary"] {{ background: {PRIMARY_FILL}; color: {GRAY_0};
                                      border-color: {PRIMARY_FILL}; font-weight: 700; }}
    QPushButton[variant="primary"]:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
    QPushButton[variant="primary"]:pressed {{ background: {PRIMARY_DARK}; border-color: {PRIMARY_DARK}; }}
    QPushButton[variant="primary"]:disabled {{ background: {GRAY_20}; border-color: {GRAY_30};
                                               color: {GRAY_50}; }}
    QPushButton#stepNav {{ border: none; border-radius: {s(8)}px; background: {GRAY_10};
        color: {TEXT}; font-weight: 700; font-size: {s(BODY_PX)}px;
        padding: {field_pad}px {s(18)}px; min-height: {field_min}px; }}
    QPushButton#stepNav:hover {{ background: {GRAY_20}; }}
    QPushButton#stepNav[variant="primary"] {{ background: {PRIMARY_FILL}; color: {GRAY_0}; }}
    QPushButton#stepNav[variant="primary"]:hover {{ background: {PRIMARY_HOVER}; }}
    QPushButton#stepButton {{ border: none; background: transparent; color: {TEXT_SUB};
                              padding: {s(4)}px {s(4)}px {s(4)}px {s(26)}px; min-height: {s(28)}px;
                              font-size: {s(CONTENT_PX)}px; font-weight: 400; }}
    QPushButton#stepButton[stepState="done"] {{ color: {TEXT}; }}
    QPushButton#stepButton:hover {{ color: {TEXT}; background: transparent; }}
    QPushButton#stepButton:checked {{ color: {PRIMARY}; background: transparent; font-weight: 700; }}
    QPushButton[variant="chip"], QPushButton[segment], QPushButton#overviewToggle,
    QPushButton#zoomButton, QPushButton[density="compact"] {{
        padding: {compact_pad}px {s(12)}px; min-height: {compact_min}px;
        font-size: {s(LABEL_PX)}px; }}

    QLabel#fieldLabel {{ font-size: {s(LABEL_PX)}px; color: {TEXT_SUB}; }}
    QLabel#subsectionTitle {{ font-size: {s(SUBHEADING_PX)}px; font-weight: 700; color: {TEXT}; }}
    QLabel#subsectionAside {{ font-size: {s(LABEL_PX)}px; color: {TEXT_MUTED}; }}
    QLabel#subsectionHelp {{ font-size: {s(LABEL_PX)}px; color: {TEXT_SUB}; }}
    QFrame#sectionDivider {{ background: {LINE}; border: none; min-height: 1px; max-height: 1px; }}
    QTabBar#cameraTabs::tab {{ background: transparent; color: {TEXT_SUB}; border: none;
                               border-bottom: 2px solid transparent;
                               padding: {s(6)}px {s(12)}px; margin-right: {s(4)}px; }}
    QTabBar#cameraTabs::tab:selected {{ color: {PRIMARY}; border-bottom-color: {PRIMARY_FILL};
                                        font-weight: 700; }}
    QTabBar#cameraTabs::tab:hover:!selected {{ color: {TEXT}; }}
    QPushButton#overviewToggle {{ color: {TEXT_SUB}; }}
    QPushButton#infoMark, QPushButton#infoMark:hover {{ border: none; background: transparent;
        color: {PRIMARY}; font-size: {s(LABEL_PX)}px; padding: 0 {s(4)}px; min-height: 0; }}
    QGroupBox[card="true"] {{ padding: 0; }}
    QGroupBox[card="true"]::title {{ color: transparent; padding: 0; }}
    QWidget#cardHeader {{ background: transparent; min-height: {s(40)}px; }}
    QLabel#cardTitle {{ font-size: {s(HEADING_PX)}px; font-weight: 700; color: {TEXT}; }}
    QLabel#cardAside {{ font-size: {s(LABEL_PX)}px; color: {TEXT_SUB}; }}
    QHeaderView::section {{ background: {TABLE_HEAD}; color: {TEXT_BOLDER}; border: none;
        border-bottom: 1px solid {TABLE_HEAD_LINE}; padding: {s(6)}px {s(8)}px;
        font-size: {s(LABEL_PX)}px; font-weight: 700; }}
    QTableWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {s(2)}px;
        selection-background-color: {PRIMARY_SOFT}; selection-color: {TEXT};
        gridline-color: {LINE}; }}
    QTableWidget::item {{ padding: 0 {s(8)}px; border-bottom: 1px solid {LINE}; }}
    QTableWidget#videoList {{ border: none; border-radius: 0;
        background: {SURFACE}; selection-background-color: {PRIMARY_SOFT}; }}
    QTableWidget#videoList::item {{ border: none; padding: 0; }}
    QTableWidget#videoList QHeaderView::section {{ background: {SURFACE}; color: {TEXT_SUB};
        font-weight: 400; border: none; border-bottom: 1px solid {LINE};
        padding: {s(4)}px {s(12)}px {s(6)}px {s(12)}px; font-size: {s(LABEL_PX)}px; }}
    QPushButton#rowAction {{ border: none; background: transparent; min-height: 0;
        padding: {s(6)}px; border-radius: {s(4)}px; }}
    QPushButton#rowAction:hover {{ background: {GRAY_10}; }}
    QPushButton#rowAction:disabled {{ background: transparent; }}
    QPushButton#playerIcon {{ border: none; background: transparent; padding: 0;
        min-width: {s(32)}px; max-width: {s(32)}px; min-height: {s(32)}px; max-height: {s(32)}px;
        border-radius: {s(4)}px; icon-size: {s(20)}px; }}
    QPushButton#playerIcon:hover {{ background: {GRAY_10}; }}
    QPushButton#playerIcon:pressed {{ background: {GRAY_20}; }}
    QPushButton#playerIcon:disabled {{ background: transparent; }}
    QFrame#cardDivider {{ background: {LINE}; border: none; min-height: 1px; max-height: 1px; }}
    QWidget#videoPlaceholder {{
        background: qradialgradient(cx:0.5, cy:0.42, radius:0.75,
            fx:0.5, fy:0.38, stop:0 #26384b, stop:0.56 #1f2a35, stop:1 {VIDEO_BACKGROUND});
        border-radius: {s(4)}px;
    }}
    QLabel#welcomeMascots {{ background: transparent; }}
    QLabel#videoPlaceholderText {{ color: {GRAY_0}; font-size: {s(CONTENT_PX)}px;
        font-weight: 700; background: transparent; }}
    QLabel#videoPlaceholderHint {{ color: {GRAY_30}; font-size: {s(LABEL_PX)}px;
        background: transparent; }}
    QPushButton#disclosure, QPushButton#disclosure:checked, QPushButton#disclosure:hover {{
        border: none; background: transparent; color: {PRIMARY}; font-weight: 400;
        text-align: left; padding: {compact_pad}px 0; min-height: {compact_min}px;
        font-size: {s(LABEL_PX)}px; }}
    QPushButton#disclosure:disabled {{ background: transparent; color: {TEXT_SUB}; }}
    QPushButton[segment] {{ border-radius: 0; }}
    QPushButton[segment="middle"], QPushButton[segment="last"] {{ border-left: none; }}
    QPushButton[segment="first"] {{ border-top-left-radius: {s(6)}px;
                                    border-bottom-left-radius: {s(6)}px; }}
    QPushButton[segment="last"] {{ border-top-right-radius: {s(6)}px;
                                   border-bottom-right-radius: {s(6)}px; }}

    /* Reviewer's record: a light grey panel under the AI result (no border). */
    QFrame#recordPanel {{ background: {GRAY_5}; border: none; border-radius: {s(8)}px; }}
    QLabel#recordTitle {{ font-size: {s(SUBHEADING_PX)}px; font-weight: 700; color: {TEXT}; }}
    QFrame#recordPanel QPushButton[segment] {{ background: {SURFACE}; }}
    QFrame#recordPanel QPushButton[segment]:checked {{ background: {PRIMARY_SOFT}; }}
    /* Judgement: four round buttons with their mark; the chosen one is navy. */
    QRadioButton[variant="pill"] {{ background: {SURFACE}; border: 1px solid {GRAY_20};
        border-radius: {s(16)}px; padding: {s(5)}px {s(14)}px {s(5)}px {s(8)}px;
        font-size: {s(LABEL_PX)}px; spacing: 0; }}
    QRadioButton[variant="pill"]::indicator {{ width: 0; height: 0; border: none;
        background: transparent; image: none; }}
    QRadioButton[variant="pill"]:hover {{ border-color: {GRAY_50}; }}
    QRadioButton[variant="pill"]:checked {{ background: {PRIMARY_FILL}; border-color: {PRIMARY_FILL};
        color: {GRAY_0}; font-weight: 700; }}
    QRadioButton[variant="pill"]:disabled {{ color: {TEXT_FAINT}; border-color: {GRAY_10}; }}
    QTextEdit#memo, QFrame#recordPanel QPlainTextEdit {{ background: {SURFACE}; border-color: {GRAY_20}; }}
    /* Tags: small chips (× removes) and a dashed "+ 태그". */
    QPushButton#tagChip {{ background: {SURFACE}; color: {PRIMARY}; border: 1px solid {SECONDARY_10};
        border-radius: {s(11)}px; padding: {s(2)}px {s(10)}px; min-height: {s(18)}px;
        font-size: {s(CAPTION_PX)}px; font-weight: 400; }}
    QPushButton#tagChip:hover {{ border-color: {PRIMARY_FILL}; }}
    QPushButton#tagAdd {{ background: {SURFACE}; border: 1px solid {GRAY_20};
        border-radius: {s(12)}px; padding: 0; min-width: {s(24)}px; max-width: {s(24)}px;
        min-height: {s(24)}px; max-height: {s(24)}px; icon-size: {s(14)}px; }}
    QPushButton#tagAdd:hover {{ border-color: {PRIMARY_FILL}; background: {SECONDARY_5}; }}
    QLineEdit#tagInput {{ background: {SURFACE}; border: 1px solid {PRIMARY_FILL};
        border-radius: {s(12)}px; padding: 0 {s(10)}px; min-height: {s(22)}px;
        max-height: {s(24)}px; font-size: {s(CAPTION_PX)}px; }}

    QCheckBox, QRadioButton {{ spacing: {s(8)}px; }}
    QCheckBox::indicator, QListView::indicator, QTableView::indicator {{
        width: {s(20)}px; height: {s(20)}px; border: 1px solid {INPUT_BORDER};
        border-radius: {s(4)}px; background: {SURFACE}; }}
    QCheckBox::indicator:checked, QListView::indicator:checked, QTableView::indicator:checked {{
        background: {PRIMARY_FILL}; border-color: {PRIMARY_FILL}; image: url("{check}"); }}
    QCheckBox::indicator:disabled, QListView::indicator:disabled,
    QTableView::indicator:disabled {{
        background: {GRAY_10}; border-color: {GRAY_30}; }}
    QCheckBox::indicator:checked:disabled, QListView::indicator:checked:disabled,
    QTableView::indicator:checked:disabled {{
        background: {GRAY_30}; border-color: {GRAY_30}; }}
    QRadioButton::indicator {{ width: {s(20)}px; height: {s(20)}px;
                               border: 1px solid {INPUT_BORDER}; border-radius: {s(10)}px;
                               background: {SURFACE}; }}
    QRadioButton::indicator:checked {{ border-color: {PRIMARY_FILL};
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
            stop:0 {PRIMARY_FILL}, stop:0.48 {PRIMARY_FILL}, stop:0.56 {SURFACE}, stop:1 {SURFACE}); }}
    QRadioButton::indicator:disabled {{ background: {GRAY_10}; border-color: {GRAY_30}; }}

    QLineEdit, QComboBox, QAbstractSpinBox {{
        background: {SURFACE}; color: {TEXT}; border: 1px solid {INPUT_BORDER};
        border-radius: {s(6)}px; padding: {field_pad}px {s(12)}px; min-height: {field_min}px;
        selection-background-color: {PRIMARY_SELECTION}; selection-color: {TEXT}; }}
    QPlainTextEdit, QTextEdit {{
        background: {SURFACE}; color: {TEXT}; border: 1px solid {INPUT_BORDER};
        border-radius: {s(6)}px; padding: {s(4)}px {s(8)}px;
        selection-background-color: {PRIMARY_SELECTION}; selection-color: {TEXT}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
    QAbstractSpinBox:focus {{ border-color: {PRIMARY_FILL}; }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled,
    QAbstractSpinBox:disabled {{ background: {GRAY_5}; color: {TEXT_FAINT};
                                                  border-color: {GRAY_30}; }}
    QComboBox[density="compact"], QLineEdit[density="compact"] {{ padding: {compact_pad}px {s(12)}px;
        min-height: {compact_min}px; font-size: {s(LABEL_PX)}px; }}
    /* Choice boxes: soft grey frame, rounder corners, room for the chevron, navy when
       open or focused. */
    QComboBox {{ border-color: {GRAY_30}; border-radius: {s(8)}px; padding-right: {s(36)}px; }}
    QComboBox:hover {{ border-color: {GRAY_50}; }}
    QComboBox:on, QComboBox:focus {{ border-color: {PRIMARY_FILL}; }}
    QComboBox:disabled {{ border-color: {GRAY_20}; }}
    QComboBox::drop-down {{ border: none; width: {s(32)}px; subcontrol-origin: padding;
                            subcontrol-position: center right; }}
    QComboBox#speedCombo {{ border: 1px solid {GRAY_30}; border-radius: {s(4)}px;
        padding: {s(1)}px {s(8)}px; min-height: {s(20)}px; font-size: {s(CAPTION_PX)}px;
        color: {TEXT_SUB}; }}
    QComboBox#speedCombo:hover {{ border-color: {GRAY_50}; color: {TEXT}; }}
    QComboBox#speedCombo::drop-down {{ width: 0; }}
    QComboBox#speedCombo::down-arrow {{ image: none; width: 0; height: 0; }}
    QComboBox::down-arrow {{ image: url("{down}"); width: {s(12)}px; height: {s(12)}px; }}
    QAbstractSpinBox::up-button {{ subcontrol-origin: border;
        subcontrol-position: top right; width: {s(20)}px; border: none; }}
    QAbstractSpinBox::down-button {{ subcontrol-origin: border;
        subcontrol-position: bottom right; width: {s(20)}px; border: none; }}
    QAbstractSpinBox::up-arrow {{ image: url("{up}");
        width: {s(10)}px; height: {s(10)}px; }}
    QAbstractSpinBox::down-arrow {{ image: url("{down}");
        width: {s(10)}px; height: {s(10)}px; }}
    QComboBox {{ combobox-popup: 0; }}
    QComboBox QAbstractItemView {{ background: {SURFACE}; color: {TEXT};
        border: 1px solid {GRAY_20}; border-radius: {s(8)}px; padding: {s(4)}px; outline: 0; }}
    QComboBox QAbstractItemView::item {{ min-height: {s(36)}px; padding: 0 {s(12)}px;
        border-radius: {s(4)}px; color: {TEXT}; }}
    QComboBox QAbstractItemView::item:hover {{ background: {GRAY_5}; }}
    QComboBox QAbstractItemView::item:selected {{ background: {PRIMARY_SOFT}; color: {PRIMARY};
        font-weight: 700; }}
    QMenu {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {GRAY_30};
        padding: {s(4)}px; }}
    QMenu::item {{ padding: {s(8)}px {s(24)}px {s(8)}px {s(12)}px; border-radius: {s(4)}px; }}
    QMenu::item:selected {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; }}
    QMenu::item:disabled {{ color: {GRAY_50}; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: {s(4)}px {s(8)}px; }}
    QFrame#situationTile {{ background: transparent; border: none; }}
    QFrame#situationTile[checked="true"] QCheckBox {{ color: {PRIMARY}; font-weight: 700; }}
    QLabel#recommendTag {{ font-size: {s(CAPTION_PX)}px; color: {TEXT_SUB}; }}
    QFrame#datePopover {{ background: {SURFACE}; border: 1px solid {GRAY_30}; }}
    QLabel#popoverTitle {{ font-size: {s(HEADING_PX)}px; font-weight: 700; color: {TEXT}; }}
    QLabel#popoverText {{ font-size: {s(LABEL_PX)}px; color: {TEXT}; }}
    QPushButton#popoverClose {{ border: none; background: transparent; min-height: 0;
        padding: {s(4)}px; }}
    QPushButton#popoverClose:hover {{ background: {GRAY_5}; }}
    QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {SURFACE}; }}
    QCalendarWidget QToolButton {{ color: {TEXT}; background: transparent; border: none;
        font-size: {s(SUBHEADING_PX)}px; font-weight: 700; padding: {s(4)}px {s(8)}px;
        border-radius: {s(4)}px; qproperty-iconSize: {s(18)}px; }}
    QCalendarWidget QToolButton:hover {{ background: {GRAY_5}; }}
    QCalendarWidget QToolButton::menu-indicator {{ image: none; width: 0; }}
    QCalendarWidget QSpinBox {{ border: 1px solid {INPUT_BORDER}; min-height: 0;
        padding: {s(2)}px {s(4)}px; }}
    QCalendarWidget QAbstractItemView {{ background: {SURFACE}; color: {TEXT}; outline: 0;
        selection-background-color: {PRIMARY_FILL}; selection-color: {GRAY_0}; }}
    QCalendarWidget QAbstractItemView:disabled {{ color: {GRAY_40}; }}

    QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {s(2)}px;
                   padding: {s(1)}px; outline: 0; }}
    QListWidget::item {{ padding: {s(8)}px {s(12)}px; border-bottom: 1px solid {LINE}; }}
    QListWidget::item:selected {{ background: {PRIMARY_SOFT}; color: {TEXT};
                                  border-left: {s(4)}px solid {PRIMARY_FILL}; }}
    QListWidget::item:hover:!selected {{ background: {GRAY_5}; }}
    QListWidget#clueList {{ border: none; border-top: 1px solid {BORDER}; border-radius: 0;
        padding: 0; }}
    /* Clue input bar: source (grey cell) · sentence · navy [+ 추가], one rounded box. */
    QFrame#clueBar {{ background: {SURFACE}; border: 1px solid {GRAY_30}; border-radius: {s(8)}px; }}
    QComboBox#clueBarSource {{ background: {GRAY_5}; border: none; border-right: 1px solid {GRAY_20};
        border-top-left-radius: {s(7)}px; border-bottom-left-radius: {s(7)}px;
        border-top-right-radius: 0; border-bottom-right-radius: 0;
        color: {PRIMARY}; font-weight: 700; padding: {s(8)}px {s(10)}px; min-width: {s(84)}px; }}
    QLineEdit#clueBarText {{ background: transparent; border: none; border-radius: 0;
        padding: {s(8)}px {s(12)}px; }}
    QLineEdit#clueBarText:disabled {{ background: transparent; }}
    QPushButton#clueBarAdd {{ background: {PRIMARY_FILL}; color: {GRAY_0}; border: none;
        border-radius: 0; border-top-right-radius: {s(7)}px; border-bottom-right-radius: {s(7)}px;
        padding: 0 {s(14)}px; font-weight: 700; }}
    QPushButton#clueBarAdd:hover {{ background: {PRIMARY_HOVER}; }}
    QPushButton#clueBarAdd:disabled {{ background: {GRAY_20}; color: {GRAY_0}; }}
    QLabel#stepNumber {{ background: {NAVY}; color: {GRAY_0}; border-radius: {s(10)}px;
        min-width: {s(20)}px; max-width: {s(20)}px; min-height: {s(20)}px; max-height: {s(20)}px;
        font-size: {s(CAPTION_PX)}px; font-weight: 700; }}
    QFrame#stepSectionHeader {{ background: transparent; }}
    QPushButton#viewButton {{ padding-right: {s(28)}px; }}
    QPushButton#viewButton::menu-indicator {{ image: url("{down}"); width: {s(12)}px;
        height: {s(12)}px; subcontrol-origin: padding; subcontrol-position: center right;
        right: {s(10)}px; }}
    QListWidget#candidateList {{ padding: 0; }}
    QListWidget#candidateList::item {{ padding: 0; border-bottom: 1px solid {LINE}; }}
    QListWidget#candidateList::item:selected {{ background: {PRIMARY_SOFT}; color: {TEXT};
                                                border-left: none; }}
    QListWidget#clueList::item {{ padding: 0; border-bottom: 1px solid {LINE}; }}
    QListWidget#clueList::item:selected {{ background: {PRIMARY_SOFT}; color: {TEXT};
                                           border-left: none; }}
    QLabel#fieldError {{ color: {DANGER}; font-size: {s(LABEL_PX)}px; }}

    QProgressBar {{ background: {GRAY_10}; border: none; border-radius: {s(4)}px;
                    min-height: {s(8)}px; max-height: {s(8)}px; text-align: center;
                    color: transparent; }}
    QProgressBar::chunk {{ background: {PRIMARY_FILL}; border-radius: {s(4)}px; }}

    QScrollBar:vertical {{ background: transparent; width: {s(12)}px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: {s(12)}px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {GRAY_30}; border-radius: {s(4)}px;
                                   min-height: {s(32)}px; margin: {s(2)}px; }}
    QScrollBar::handle:horizontal {{ background: {GRAY_30}; border-radius: {s(4)}px;
                                     min-width: {s(32)}px; margin: {s(2)}px; }}
    QScrollBar::handle:hover {{ background: {GRAY_50}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QSlider::groove:horizontal {{ height: {groove}px; background: {GRAY_20};
                                  border-radius: {groove // 2}px; }}
    QSlider::sub-page:horizontal {{ background: {PRIMARY_FILL}; border-radius: {groove // 2}px; }}
    QSlider::handle:horizontal {{ background: {SURFACE}; border: 2px solid {PRIMARY_FILL};
                                  width: {knob}px; margin: -{knob_margin}px 0;
                                  border-radius: {(knob + 4) // 2}px; }}

    QStatusBar {{ background: {SURFACE_ALT}; color: transparent; border-top: 1px solid {BORDER};
                  min-height: {s(56)}px; }}
    QLabel#statusMessage {{ color: {TEXT_SUB}; padding-left: {s(24)}px; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{ background: {GRAY_90}; color: {GRAY_0}; border: none; padding: {s(8)}px;
                font-size: {s(LABEL_PX)}px; }}
    """
