from __future__ import annotations

import time
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
        started = time.monotonic()
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
                origin=ResultOrigin.NONE,
                purpose=self._request.purpose,
                range_start_sec=self._request.range_start_sec,
                range_end_sec=self._request.range_end_sec,
                sample_timestamps_sec=self._request.sample_timestamps_sec,
                elapsed_seconds=time.monotonic() - started,
                requested_frame_count=len(self._request.sample_timestamps_sec),
                sampling_strategy=self._request.sampling_strategy,
                observation_guidance=self._request.observation_guidance,
                case_id=self._request.case_id,
                case_revision=self._request.case_revision,
                analysis_clues=self._request.analysis_clues,
            )
        self.result_ready.emit(result)
