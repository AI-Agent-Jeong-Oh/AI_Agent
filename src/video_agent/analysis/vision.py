from __future__ import annotations

import json
import re
import time
from pathlib import Path
from threading import Event
from typing import Any, Callable

from video_agent.analysis.base import ProgressCallback
from video_agent.contracts import (
    AnalysisJob,
    AnalysisMode,
    AnalysisRequest,
    JobStatus,
    ResultOrigin,
    Segment,
)
from video_agent.models.provider import ProviderResponse, VisionProvider
from video_agent.video.frames import ExtractedFrame, FFmpegFrameExtractor
from video_agent.video.probe import FFprobeVideoProbe
from video_agent.video.process import OperationCanceled


class ModelResponseError(ValueError):
    pass


class VisionAnalyzer:
    def __init__(
        self,
        provider: VisionProvider,
        work_dir_factory: Callable[[str], Path],
        *,
        probe: FFprobeVideoProbe | None = None,
        extractor: FFmpegFrameExtractor | None = None,
    ) -> None:
        if provider.config.mode is AnalysisMode.SAMPLE:
            raise ValueError("VisionAnalyzer cannot use sample mode")
        self._provider = provider
        self._work_dir_factory = work_dir_factory
        self._probe = probe or FFprobeVideoProbe()
        self._extractor = extractor or FFmpegFrameExtractor()

    def analyze(
        self,
        request: AnalysisRequest,
        cancel_event: Event,
        on_progress: ProgressCallback,
    ) -> AnalysisJob:
        if request.mode is AnalysisMode.SAMPLE:
            raise ValueError("VisionAnalyzer requires local or external mode")
        if request.mode is not self._provider.config.mode:
            raise ValueError("request mode and provider mode do not match; fallback is forbidden")
        if request.model_id != self._provider.config.model_id:
            raise ValueError("request model_id does not match configured provider")

        started = time.monotonic()
        try:
            on_progress(None, "영상 메타데이터 확인 중")
            metadata = self._probe.probe(request.video_path, cancel_event)
            if metadata.video_id != request.video_id:
                raise ValueError("선택 이후 영상 파일이 변경되었습니다.")
            if abs(metadata.duration_seconds - request.duration_seconds) > 0.1:
                raise ValueError("승인 이후 영상 길이가 변경되었습니다.")
            self._check_deadline(started, request)

            on_progress(None, "승인된 시각의 프레임 추출 중")
            frames = self._extractor.extract(
                request.video_path,
                metadata,
                request.sample_timestamps_sec,
                self._work_dir_factory(request.job_id),
                max_frames=request.limits.max_frames,
                cancel_event=cancel_event,
                total_timeout_seconds=(
                    request.limits.max_runtime_seconds - (time.monotonic() - started)
                ),
            )
            self._check_deadline(started, request)
            if cancel_event.is_set():
                raise OperationCanceled("분석이 취소되었습니다.")

            prompt = build_prompt(request, frames)
            on_progress(None, "영상 모델 응답 대기 중 · 진행률 알 수 없음")
            remaining = request.limits.max_runtime_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("승인된 최대 분석 시간을 초과했습니다.")
            response = self._provider.analyze(
                prompt,
                frames,
                timeout_seconds=remaining,
                cancel_event=cancel_event,
            )
            self._check_deadline(started, request)
            on_progress(None, "모델 응답 형식 및 구간 검증 중")
            segments = parse_model_response(response, request, frames)
            result = AnalysisJob(
                job_id=request.job_id,
                plan_id=request.plan_id,
                status=JobStatus.COMPLETED,
                progress=100,
                video_id=request.video_id,
                mode=request.mode,
                model_id=request.model_id,
                is_sample=False,
                origin=(ResultOrigin.TEST_DOUBLE if response.is_test_double else ResultOrigin.REAL_MODEL),
                segments=segments,
            )
            result.validate_for_duration(request.duration_seconds)
            completion_message = (
                "테스트용 가짜 공급자 응답 검증 완료"
                if response.is_test_double
                else "실제 모델 응답 검증 완료"
            )
            on_progress(100, completion_message)
            return result
        except OperationCanceled:
            return AnalysisJob(
                job_id=request.job_id,
                plan_id=request.plan_id,
                status=JobStatus.CANCELED,
                progress=None,
                video_id=request.video_id,
                mode=request.mode,
                model_id=request.model_id,
                is_sample=False,
                origin=ResultOrigin.REAL_MODEL,
            )

    @staticmethod
    def _check_deadline(started: float, request: AnalysisRequest) -> None:
        if time.monotonic() - started > request.limits.max_runtime_seconds:
            raise TimeoutError("승인된 최대 분석 시간을 초과했습니다.")


def build_prompt(request: AnalysisRequest, frames: tuple[ExtractedFrame, ...]) -> str:
    frame_lines = "\n".join(
        f"- {frame.frame_id}: 원본 {frame.source_timestamp_sec:.3f}초"
        for frame in frames
    )
    return (
        "당신은 영상에서 사용자가 직접 확인할 후보 구간을 제안합니다.\n"
        f"분석 목적: {request.purpose}\n"
        f"승인 범위: {request.range_start_sec:.3f}~{request.range_end_sec:.3f}초\n"
        "입력 프레임과 원본 시각:\n"
        f"{frame_lines}\n"
        "관찰한 시각 정보만 사용하고 보이지 않는 의도·신원·범죄 성립을 확정하지 마세요. "
        "각 후보에 관찰 요약, evidence, 실제 source_frame_ids, 불확실성을 넣으세요. "
        "구간은 승인 범위 안에서 start_sec < end_sec여야 합니다. JSON 객체만 반환하세요."
    )


def parse_model_response(
    response: ProviderResponse,
    request: AnalysisRequest,
    frames: tuple[ExtractedFrame, ...],
) -> tuple[Segment, ...]:
    try:
        payload = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise ModelResponseError("모델 응답이 유효한 JSON이 아닙니다.") from exc
    if not isinstance(payload, dict) or set(payload) != {"segments"}:
        raise ModelResponseError("모델 응답 최상위 형식은 segments만 가진 객체여야 합니다.")
    raw_segments = payload["segments"]
    if not isinstance(raw_segments, list) or len(raw_segments) > 20:
        raise ModelResponseError("segments는 최대 20개의 배열이어야 합니다.")

    known_frame_ids = {frame.frame_id for frame in frames}
    segments: list[Segment] = []
    for index, item in enumerate(raw_segments):
        segments.append(_parse_segment(item, index, request, known_frame_ids))
    return tuple(segments)


def _parse_segment(
    item: Any,
    index: int,
    request: AnalysisRequest,
    known_frame_ids: set[str],
) -> Segment:
    required = {
        "start_sec",
        "end_sec",
        "summary",
        "evidence",
        "source_frame_ids",
        "uncertainty",
    }
    if not isinstance(item, dict) or set(item) != required:
        raise ModelResponseError(f"segments[{index}] 필드가 계약과 다릅니다.")
    start = _number(item["start_sec"], f"segments[{index}].start_sec")
    end = _number(item["end_sec"], f"segments[{index}].end_sec")
    if not request.range_start_sec <= start < end <= (request.range_end_sec or 0) + 1e-6:
        raise ModelResponseError(f"segments[{index}] 구간이 승인 범위를 벗어났습니다.")
    summary = _text(item["summary"], f"segments[{index}].summary")
    uncertainty = _text(item["uncertainty"], f"segments[{index}].uncertainty")
    _reject_unsafe_certainty(summary)
    evidence = _text_list(item["evidence"], f"segments[{index}].evidence")
    source_frame_ids = _text_list(item["source_frame_ids"], f"segments[{index}].source_frame_ids")
    if not source_frame_ids or any(frame_id not in known_frame_ids for frame_id in source_frame_ids):
        raise ModelResponseError(f"segments[{index}]가 입력에 없는 프레임 ID를 근거로 사용했습니다.")
    return Segment(
        video_id=request.video_id,
        start_sec=start,
        end_sec=end,
        summary=summary,
        evidence=evidence,
        uncertainty=uncertainty,
        source_frame_ids=source_frame_ids,
    )


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelResponseError(f"{name}은 숫자여야 합니다.")
    return float(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelResponseError(f"{name}은 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _text_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ModelResponseError(f"{name}은 비어 있지 않은 문자열 배열이어야 합니다.")
    return tuple(_text(item, name) for item in value)


def _reject_unsafe_certainty(summary: str) -> None:
    patterns = (
        r"범죄(가|임이)\s*(확실|분명)",
        r"범죄자(이다|입니다)",
        r"고의(가|임이)\s*(확실|분명)",
        r"\bis guilty\b",
        r"\bcriminal intent is certain\b",
    )
    if any(re.search(pattern, summary, flags=re.IGNORECASE) for pattern in patterns):
        raise ModelResponseError("사람의 의도나 범죄 성립을 확정하는 표현이 포함되어 있습니다.")
