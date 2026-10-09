from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import Protocol

from video_agent.contracts import AnalysisJob, AnalysisRequest

ProgressCallback = Callable[[int | None, str], None]


class Analyzer(Protocol):
    """Replaceable interface shared by sample, local, and external analyzers."""

    def analyze(
        self,
        request: AnalysisRequest,
        cancel_event: Event,
        on_progress: ProgressCallback,
    ) -> AnalysisJob: ...
