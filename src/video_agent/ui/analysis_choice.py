"""Visible choices for local, demonstration, and external video analysis.

The demonstration is available only in development. Local model IDs stay in the
choice data; the selected ID is shown separately in the analysis settings.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QPushButton, QSizePolicy, QWidget

from video_agent.contracts import AnalysisMode

SAMPLE_LABEL = "시연용 (실제 분석 아님)"
EXTERNAL_LABEL = "외부 AI 분석 (영상 일부를 외부로 보냄)"
UNLISTED_LABEL = "AI 분석 (확인되지 않은 프로그램)"


def sample_enabled() -> bool:
    return os.environ.get("VIDEO_AGENT_ENABLE_SAMPLE", "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Choice:
    mode: AnalysisMode
    model_id: str = ""


class AnalysisChoice(QWidget):
    choice_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName("분석 방식")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        self._labels: list[str] = []
        self._choices: list[Choice] = []
        self._buttons: list[QPushButton] = []
        self._current_index = -1
        self.set_local_models((), "")

    def set_local_models(self, models: Sequence[tuple[str, str]], missing_text: str) -> None:
        keep = self.choice() if self._choices else None
        labels = [label for _model_id, label in models]
        choices = [Choice(AnalysisMode.LOCAL, model_id) for model_id, _label in models]
        if not models and missing_text:
            labels.append(missing_text)
            choices.append(Choice(AnalysisMode.LOCAL))
        if sample_enabled():
            labels.append(SAMPLE_LABEL)
            choices.append(Choice(AnalysisMode.SAMPLE))
        labels.append(EXTERNAL_LABEL)
        choices.append(Choice(AnalysisMode.EXTERNAL))
        self._replace_choices(labels, choices)
        if keep is not None:
            self.select(keep.mode, keep.model_id)

    def _replace_choices(self, labels: list[str], choices: list[Choice]) -> None:
        blocked = self.blockSignals(True)
        for button in self._buttons:
            self._grid.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._labels = labels
        self._choices = choices
        self._buttons = []
        self._current_index = -1
        for index, (label, choice) in enumerate(zip(labels, choices, strict=True)):
            button = QPushButton(self._display_label(label))
            button.setObjectName("analysisOption")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setAccessibleName(label)
            if choice.model_id:
                button.setAccessibleDescription(f"사용 모델 {choice.model_id}")
            button.clicked.connect(lambda _checked=False, row=index: self.setCurrentIndex(row))
            span = 2 if len(labels) % 2 and index == len(labels) - 1 else 1
            self._grid.addWidget(button, index // 2, index % 2, 1, span)
            self._buttons.append(button)
        if self._buttons:
            self.setCurrentIndex(0)
        self.blockSignals(blocked)

    @staticmethod
    def _display_label(label: str) -> str:
        return {
            SAMPLE_LABEL: "시연용\n실제 분석 아님",
            EXTERNAL_LABEL: "외부 AI 분석\n영상 일부를 외부로 보냄",
            UNLISTED_LABEL: "AI 분석\n확인되지 않은 프로그램",
            "AI 분석 (설치된 프로그램 없음)": "AI 분석\n설치된 프로그램 없음",
        }.get(label, label)

    def count(self) -> int:
        return len(self._choices)

    def itemText(self, index: int) -> str:  # noqa: N802 - Qt-style integration method
        return self._labels[index] if 0 <= index < self.count() else ""

    def itemData(self, index: int) -> Choice | None:  # noqa: N802
        return self._choices[index] if 0 <= index < self.count() else None

    def currentIndex(self) -> int:  # noqa: N802
        return self._current_index

    def currentText(self) -> str:  # noqa: N802
        return self.itemText(self._current_index)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < self.count():
            return
        changed = index != self._current_index
        self._current_index = index
        for row, button in enumerate(self._buttons):
            button.setChecked(row == index)
        if changed:
            self._emit()

    def choice(self) -> Choice:
        return self.itemData(self._current_index) or Choice(AnalysisMode.LOCAL)

    def mode(self) -> AnalysisMode:
        return self.choice().mode

    def model_id(self) -> str:
        return self.choice().model_id

    def select(self, mode: AnalysisMode, model_id: str = "") -> None:
        """Select an option; retain an unavailable saved local model as a visible tile."""
        model_id = model_id.strip()
        for index, choice in enumerate(self._choices):
            if choice.mode is mode and (mode is not AnalysisMode.LOCAL or not model_id or choice.model_id == model_id):
                self.setCurrentIndex(index)
                return
        if mode is AnalysisMode.LOCAL and model_id:
            labels = list(self._labels)
            choices = list(self._choices)
            if choices and choices[0] == Choice(AnalysisMode.LOCAL, ""):
                labels.pop(0)
                choices.pop(0)
            index = sum(choice.mode is AnalysisMode.LOCAL for choice in choices)
            labels.insert(index, UNLISTED_LABEL)
            choices.insert(index, Choice(AnalysisMode.LOCAL, model_id))
            self._replace_choices(labels, choices)
            self.setCurrentIndex(index)
            if index == 0:
                self._emit()
            return
        if self._choices:
            self.setCurrentIndex(0)

    def _emit(self) -> None:
        if not self.signalsBlocked():
            self.choice_changed.emit()
