from __future__ import annotations

from threading import Event

from PySide6.QtCore import QThread, Signal

from video_agent.analysis.base import Analyzer
from video_agent.contracts import AnalysisJob, AnalysisRequest, JobStatus, ResultOrigin


class AnalysisWorker(QThread):
    progress_changed = Signal(object, str)
    result_ready = Signal(object)

    def __init__(self, analyzer: Analyzer, request: AnalysisRequest) -> None:
        super().__init__()
        self._analyzer = analyzer
        self._request = request
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = self._analyzer.analyze(
                self._request,
                self._cancel_event,
                self.progress_changed.emit,
            )
        except Exception as exc:  # Boundary between background work and the UI.
            result = AnalysisJob(
                job_id=self._request.job_id,
                status=JobStatus.FAILED,
                progress=None,
                video_id=self._request.video_id,
                error=str(exc) or exc.__class__.__name__,
                plan_id=self._request.plan_id,
                mode=self._request.mode,
                model_id=self._request.model_id,
                is_sample=self._request.mode.value == "sample",
                origin=(
                    ResultOrigin.SAMPLE
                    if self._request.mode.value == "sample"
                    else ResultOrigin.REAL_MODEL
                ),
            )
        self.result_ready.emit(result)
