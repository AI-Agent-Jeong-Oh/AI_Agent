"""One plain-language choice of how to analyse: fast or accurate (on this computer) or an
external AI service.

The demonstration (not a real analysis) is for development and tests only: it is listed
when VIDEO_AGENT_ENABLE_SAMPLE is set, so staff never get example results by mistake.

Users pick "빠른 분석" or "정확한 분석" instead of a mode plus a model ID; the model behind
each local choice stays in the item data (and its tooltip, for staff). Cloud models are
not offered, since they would send frames off this computer.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox

from video_agent.contracts import AnalysisMode

SAMPLE_LABEL = "시연용 (실제 분석 아님)"
EXTERNAL_LABEL = "외부 AI 분석 (영상 일부를 외부로 보냄)"
UNLISTED_LABEL = "AI 분석 (확인되지 않은 프로그램)"


def sample_enabled() -> bool:
    """The demonstration choice is offered only when a developer turns it on."""
    return os.environ.get("VIDEO_AGENT_ENABLE_SAMPLE", "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Choice:
    mode: AnalysisMode
    model_id: str = ""


class AnalysisChoice(QComboBox):
    choice_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName("분석 방식")
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(10)
        self.currentIndexChanged.connect(lambda _index: self._emit())
        self.set_local_models((), "")

    def set_local_models(self, models: Sequence[tuple[str, str]], missing_text: str) -> None:
        """(model ID, label) pairs for this computer, then external (and the demonstration
        when enabled for development)."""
        keep = self.choice() if self.count() else None
        self.blockSignals(True)
        self.clear()
        for model_id, label in models:
            self._add(label, Choice(AnalysisMode.LOCAL, model_id), tooltip=model_id)
        if not models and missing_text:
            self._add(missing_text, Choice(AnalysisMode.LOCAL, ""))
        if sample_enabled():
            self._add(SAMPLE_LABEL, Choice(AnalysisMode.SAMPLE))
        self._add(EXTERNAL_LABEL, Choice(AnalysisMode.EXTERNAL))
        self.blockSignals(False)
        if keep is not None:
            self.select(keep.mode, keep.model_id)

    def choice(self) -> Choice:
        value = self.currentData()
        return value if isinstance(value, Choice) else Choice(AnalysisMode.LOCAL)

    def mode(self) -> AnalysisMode:
        return self.choice().mode

    def model_id(self) -> str:
        return self.choice().model_id

    def select(self, mode: AnalysisMode, model_id: str = "") -> None:
        """Pick a choice; an unlisted local model still shows (the plan check explains)."""
        model_id = model_id.strip()
        index = -1
        for row in range(self.count()):
            item = self.itemData(row)
            if isinstance(item, Choice) and item.mode is mode:
                if mode is not AnalysisMode.LOCAL or not model_id or item.model_id == model_id:
                    index = row
                    break
        if index < 0 and mode is AnalysisMode.LOCAL and model_id:
            index = self._local_count()
            # A placeholder row ("no program found") gives way to the named model.
            placeholder = self.itemData(0)
            if isinstance(placeholder, Choice) and placeholder.mode is mode and not placeholder.model_id:
                self.blockSignals(True)
                self.removeItem(0)
                self.blockSignals(False)
                index -= 1
            self.blockSignals(True)
            self.insertItem(index, UNLISTED_LABEL, Choice(AnalysisMode.LOCAL, model_id))
            self.setItemData(index, model_id, Qt.ItemDataRole.ToolTipRole)
            self.blockSignals(False)
        self.blockSignals(True)
        self.setCurrentIndex(max(0, index))
        self.blockSignals(False)
        self._emit()

    def _local_count(self) -> int:
        return sum(
            1
            for row in range(self.count())
            if isinstance(item := self.itemData(row), Choice) and item.mode is AnalysisMode.LOCAL
        )

    def _add(self, label: str, choice: Choice, *, tooltip: str = "") -> None:
        self.addItem(label, choice)
        if tooltip:
            self.setItemData(self.count() - 1, tooltip, Qt.ItemDataRole.ToolTipRole)

    def _emit(self) -> None:
        if not self.signalsBlocked():
            self.choice_changed.emit()
