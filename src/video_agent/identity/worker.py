from __future__ import annotations

from threading import Event

from PySide6.QtCore import QThread, Signal

from video_agent.identity.comparison import PersonCandidateInput, PersonComparisonAnalyzer
from video_agent.video.process import OperationCanceled


class PersonComparisonWorker(QThread):
    """Run one bounded comparison without blocking the desktop UI."""

    progress_changed = Signal(object, str)
    evidence_ready = Signal(str, object)
    report_ready = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        analyzer: PersonComparisonAnalyzer,
        case_id: str,
        candidates: tuple[PersonCandidateInput, ...],
    ) -> None:
        super().__init__()
        self._analyzer = analyzer
        self._case_id = case_id
        self._candidates = candidates
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            report = self._analyzer.compare(
                self._case_id,
                self._candidates,
                self._cancel_event,
                self.progress_changed.emit,
                self.evidence_ready.emit,
            )
        except OperationCanceled:
            self.error.emit("인물 비교가 취소되었습니다.")
            return
        except Exception as exc:
            self.error.emit(str(exc) or exc.__class__.__name__)
            return
        self.report_ready.emit(report)
