"""How each review judgement is marked, shared by the candidate list and the playback bar.

Colours follow the team demo page: an unreviewed AI candidate is an outline (dashed blue),
"관련 있음" solid blue, "추가 확인" amber and "제외" grey. The words are always shown or
named beside the mark, so status is never told by colour alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from video_agent.contracts import ReviewJudgement
from video_agent.ui import theme


@dataclass(frozen=True, slots=True)
class MarkStyle:
    fill: str
    border: str
    dashed: bool = False


JUDGEMENT_MARKS = {
    ReviewJudgement.UNREVIEWED: MarkStyle(theme.SURFACE, theme.PRIMARY_FILL, dashed=True),
    ReviewJudgement.RELEVANT: MarkStyle(theme.PRIMARY_FILL, theme.PRIMARY_FILL),
    ReviewJudgement.NEEDS_CHECK: MarkStyle(theme.WARNING_DOT, theme.WARNING_DOT),
    ReviewJudgement.EXCLUDED: MarkStyle(theme.GRAY_30, theme.GRAY_40),
}


def paint_mark(painter: QPainter, rect: QRectF, style: MarkStyle, radius: float = 2.0) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(style.border), 1.0)
    if style.dashed:
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([2.0, 1.5])
    painter.setPen(pen)
    painter.setBrush(QColor(style.fill))
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.restore()
