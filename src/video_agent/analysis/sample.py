from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event

from video_agent.analysis.base import ProgressCallback
from video_agent.contracts import AnalysisJob, AnalysisRequest, JobStatus, ResultOrigin, Segment
from video_agent.video.files import validate_video_path


class SampleAnalyzer:
    """Deterministic demo analyzer that never reads or interprets video frames."""

    def __init__(
        self,
        stage_delay_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._stage_delay_seconds = max(0.0, stage_delay_seconds)
        self._sleep = sleep

    def analyze(
        self,
        request: AnalysisRequest,
        cancel_event: Event,
        on_progress: ProgressCallback,
    ) -> AnalysisJob:
        validate_video_path(request.video_path)
        started = time.monotonic()
        stages = (
            (20, "영상 정보 확인"),
            (40, "샘플 구간 계산"),
            (60, "샘플 설명 구성"),
            (80, "결과 범위 검증"),
        )
        last_progress = 0
        on_progress(last_progress, "샘플 분석 준비")

        for progress, message in stages:
            if cancel_event.is_set():
                return self._canceled(request, last_progress, started)
            if time.monotonic() - started > request.limits.max_runtime_seconds:
                raise TimeoutError("승인된 최대 분석 시간을 초과했습니다.")
            self._sleep(self._stage_delay_seconds)
            if cancel_event.is_set():
                return self._canceled(request, last_progress, started)
            if time.monotonic() - started > request.limits.max_runtime_seconds:
                raise TimeoutError("승인된 최대 분석 시간을 초과했습니다.")
            last_progress = progress
            on_progress(last_progress, message)

        if cancel_event.is_set():
            return self._canceled(request, last_progress, started)

        segments = self._build_segments(
            request.video_id,
            request.duration_seconds,
            request.range_start_sec,
            request.range_end_sec,
        )
        result = AnalysisJob(
            job_id=request.job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            video_id=request.video_id,
            segments=segments,
            plan_id=request.plan_id,
            origin=ResultOrigin.SAMPLE,
            purpose=request.purpose,
            range_start_sec=request.range_start_sec,
            range_end_sec=request.range_end_sec,
            elapsed_seconds=time.monotonic() - started,
            sampling_strategy=request.sampling_strategy,
            observation_guidance=request.observation_guidance,
            case_id=request.case_id,
            case_revision=request.case_revision,
            analysis_clues=request.analysis_clues,
        )
        result.validate_for_duration(request.duration_seconds)
        on_progress(100, "샘플 결과 준비 완료")
        return result

    @staticmethod
    def _canceled(request: AnalysisRequest, progress: int, started: float) -> AnalysisJob:
        return AnalysisJob(
            job_id=request.job_id,
            status=JobStatus.CANCELED,
            progress=progress,
            video_id=request.video_id,
            plan_id=request.plan_id,
            origin=ResultOrigin.NONE,
            purpose=request.purpose,
            range_start_sec=request.range_start_sec,
            range_end_sec=request.range_end_sec,
            elapsed_seconds=time.monotonic() - started,
            sampling_strategy=request.sampling_strategy,
            observation_guidance=request.observation_guidance,
            case_id=request.case_id,
            case_revision=request.case_revision,
            analysis_clues=request.analysis_clues,
        )

    @staticmethod
    def _build_segments(
        video_id: str,
        duration: float,
        range_start_sec: float = 0.0,
        range_end_sec: float | None = None,
    ) -> tuple[Segment, ...]:
        if duration <= 0:
            raise ValueError("영상 길이는 0보다 커야 합니다.")
        range_end = duration if range_end_sec is None else range_end_sec
        if not 0 <= range_start_sec < range_end <= duration + 1e-6:
            raise ValueError("샘플 분석 범위가 영상 길이를 벗어났습니다.")

        span = range_end - range_start_sec
        ranges = ((0.10, 0.24), (0.55, 0.72)) if span >= 2.0 else ((0.0, 1.0),)
        segments: list[Segment] = []
        for index, (start_ratio, end_ratio) in enumerate(ranges, start=1):
            start = min(range_end, max(range_start_sec, range_start_sec + span * start_ratio))
            end = min(range_end, max(start + min(0.1, span), range_start_sec + span * end_ratio))
            if end <= start:
                continue
            segments.append(
                Segment(
                    video_id=video_id,
                    start_sec=round(start, 3),
                    end_sec=round(end, 3),
                    summary=f"샘플 중요 구간 후보 {index} — 실제 장면 해석 결과가 아닙니다.",
                    evidence=(
                        f"샘플 규칙: 전체 길이의 {start_ratio:.0%}~{end_ratio:.0%} 위치",
                    ),
                    uncertainty="프레임을 분석하지 않았으므로 장면 내용과 중요도는 확인되지 않았습니다.",
                )
            )
        return tuple(segments)
