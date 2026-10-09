from __future__ import annotations

from pathlib import Path
from typing import Callable

from video_agent.analysis.base import Analyzer
from video_agent.analysis.sample import SampleAnalyzer
from video_agent.analysis.vision import VisionAnalyzer
from video_agent.contracts import AnalysisMode
from video_agent.models.config import config_from_environment
from video_agent.models.provider import HttpVisionProvider


def create_analyzer(
    mode: AnalysisMode,
    model_id: str | None,
    work_dir_factory: Callable[[str], Path],
    *,
    provider_id: str | None = None,
    provider_endpoint: str | None = None,
) -> Analyzer:
    if mode is AnalysisMode.SAMPLE:
        return SampleAnalyzer()
    config = config_from_environment(mode, model_id, provider_id, provider_endpoint)
    provider = HttpVisionProvider(config)
    return VisionAnalyzer(provider, work_dir_factory)
