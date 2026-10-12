from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from video_agent.agent.planning import AgentSession, AnalysisIntent, AnalysisPlan, PlanningError
from video_agent.analysis.evidence import fine_detail_requirement, temporal_motion_requirement
from video_agent.analysis.factory import create_analyzer
from video_agent.contracts import (
    AnalysisClue,
    AnalysisJob,
    AnalysisLimits,
    AnalysisMode,
    FocusSituation,
    JobStatus,
    MotionProposal,
    MotionScanProfile,
    MotionScanResult,
    ResultOrigin,
    SamplingStrategy,
)
from video_agent.video.motion import (
    FFmpegMotionScanner,
    build_motion_sample_timestamps,
    build_temporal_sample_timestamps,
)
from video_agent.video.probe import FFprobeVideoProbe, VideoMetadata
from video_agent.video.process import OperationCanceled


@dataclass(frozen=True, slots=True)
class BatchVideoInput:
    """One linked local video selected for a case-level batch."""

    video_id: str
    path: Path
    display_name: str

    def __post_init__(self) -> None:
        if not self.video_id.strip() or not self.display_name.strip():
            raise ValueError("batch video ID and display name are required")


@dataclass(frozen=True, slots=True)
class BatchPreparationFailure:
    video: BatchVideoInput
    error: str


@dataclass(frozen=True, slots=True)
class PreparedBatchVideo:
    """A per-video plan that has been prepared but not approved or executed."""

    video: BatchVideoInput
    metadata: VideoMetadata
    motion_result: MotionScanResult
    selected_proposals: tuple[MotionProposal, ...]
    session: AgentSession

    @property
    def plan(self) -> AnalysisPlan:
        if self.session.plan is None:
            raise RuntimeError("prepared batch video has no plan")
        return self.session.plan


@dataclass(frozen=True, slots=True)
class BatchPreparation:
    prepared: tuple[PreparedBatchVideo, ...]
    failures: tuple[BatchPreparationFailure, ...]


class BatchPreparationWorker(QThread):
    """Probe and motion-scan videos sequentially before any model call or approval."""

    progress_changed = Signal(object, str)
    preparation_ready = Signal(object)

    def __init__(
        self,
        videos: tuple[BatchVideoInput, ...],
        *,
        purpose: str,
        mode: AnalysisMode,
        model_id: str | None,
        provider_id: str | None,
        provider_endpoint: str | None,
        case_id: str,
        case_revision: int,
        analysis_clues: tuple[AnalysisClue, ...],
        focus_situations: tuple[FocusSituation, ...],
        limits: AnalysisLimits,
        probe: FFprobeVideoProbe | None = None,
        scanner: FFmpegMotionScanner | None = None,
    ) -> None:
        super().__init__()
        if not videos:
            raise ValueError("at least one batch video is required")
        if mode is AnalysisMode.EXTERNAL:
            raise ValueError("외부 일괄 분석은 영상별 전송 승인이 필요합니다.")
        if not purpose.strip():
            raise ValueError("batch analysis purpose is required")
        self._videos = videos
        self._purpose = purpose.strip()
        self._mode = AnalysisMode(mode)
        self._model_id = model_id
        self._provider_id = provider_id
        self._provider_endpoint = provider_endpoint
        self._case_id = case_id
        self._case_revision = case_revision
        self._analysis_clues = analysis_clues
        self._focus_situations = focus_situations
        self._limits = limits
        self._probe = probe or FFprobeVideoProbe()
        self._scanner = scanner or FFmpegMotionScanner()
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        prepared: list[PreparedBatchVideo] = []
        failures: list[BatchPreparationFailure] = []
        total = len(self._videos)
        for index, video in enumerate(self._videos, start=1):
            if self._cancel_event.is_set():
                break
            self.progress_changed.emit(
                None,
                f"일괄 계획 준비 {index}/{total} · {video.display_name} 영상 정보 확인",
            )
            try:
                prepared.append(self._prepare_one(video, index, total))
            except OperationCanceled:
                break
            except Exception as exc:  # Background boundary: keep other videos available.
                failures.append(
                    BatchPreparationFailure(video, str(exc) or exc.__class__.__name__)
                )
        self.preparation_ready.emit(BatchPreparation(tuple(prepared), tuple(failures)))

    def _prepare_one(
        self,
        video: BatchVideoInput,
        index: int,
        total: int,
    ) -> PreparedBatchVideo:
        metadata = self._probe.probe(video.path, self._cancel_event)
        if metadata.video_id != video.video_id:
            raise ValueError("등록 이후 영상 파일이 변경되었습니다.")
        temporal = temporal_motion_requirement(self._purpose)
        fine_detail = fine_detail_requirement(self._purpose)
        profile = (
            MotionScanProfile.REPEATED_SUBTLE
            if temporal.required and not fine_detail.required
            else MotionScanProfile.GENERAL_PEAK
        )
        self.progress_changed.emit(
            None,
            f"일괄 계획 준비 {index}/{total} · {video.display_name} 움직임 구간 탐색",
        )
        motion = self._scanner.scan(
            f"batch-motion-{uuid4().hex[:12]}",
            video.path,
            metadata,
            0.0,
            metadata.duration_seconds,
            self._cancel_event,
            profile,
        )
        selected = motion.proposals[:3]
        if selected:
            if profile is MotionScanProfile.REPEATED_SUBTLE:
                timestamps = build_temporal_sample_timestamps(
                    selected,
                    max_frames=self._limits.max_frames,
                )
                strategy = SamplingStrategy.TEMPORAL_SEQUENCE
            else:
                timestamps = build_motion_sample_timestamps(
                    selected,
                    max_frames=self._limits.max_frames,
                )
                strategy = SamplingStrategy.MOTION_PEAK
            range_start = min(item.start_sec for item in selected)
            range_end = max(item.end_sec for item in selected)
        else:
            timestamps = ()
            strategy = None
            range_start = 0.0
            range_end = metadata.duration_seconds

        intent = AnalysisIntent(
            purpose=self._purpose,
            mode=self._mode,
            model_id=self._model_id,
            range_start_sec=range_start,
            range_end_sec=range_end,
            sample_timestamps_sec=timestamps,
            provider_id=self._provider_id,
            provider_endpoint=self._provider_endpoint,
            sampling_strategy=strategy,
            case_id=self._case_id,
            case_revision=self._case_revision,
            analysis_clues=self._analysis_clues,
            focus_situations=self._focus_situations,
        )
        session = AgentSession()
        session.update_context(metadata, intent)
        questions = session.questions()
        if questions:
            prompts = ", ".join(item.prompt for item in questions)
            raise PlanningError(f"분석 목적을 더 구체적으로 입력해 주세요: {prompts}")
        session.prepare_plan(self._limits)
        return PreparedBatchVideo(video, metadata, motion, selected, session)


class BatchExecutionWorker(QThread):
    """Execute approved per-video requests sequentially under bounded analyzers."""

    progress_changed = Signal(object, str)
    result_ready = Signal(object)

    def __init__(
        self,
        prepared: tuple[PreparedBatchVideo, ...],
        work_dir_factory,
        *,
        vision_capability_confirmed: bool,
        analyzer_factory=create_analyzer,
    ) -> None:
        super().__init__()
        if not prepared:
            raise ValueError("at least one prepared batch video is required")
        self._prepared = prepared
        self._work_dir_factory = work_dir_factory
        self._vision_capability_confirmed = vision_capability_confirmed
        self._analyzer_factory = analyzer_factory
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        total = len(self._prepared)
        for index, item in enumerate(self._prepared, start=1):
            if self._cancel_event.is_set():
                break
            plan = item.plan
            try:
                item.session.approve_analysis()
                request = item.session.create_request(
                    str(item.video.path),
                    f"batch-{plan.mode.value}-{uuid4().hex[:12]}",
                )
            except Exception as exc:
                self.result_ready.emit(_failed_plan_job(item, exc))
                continue
            started = time.monotonic()
            try:
                analyzer = self._analyzer_factory(
                    plan.mode,
                    plan.model_id,
                    self._work_dir_factory,
                    provider_id=plan.provider_id,
                    provider_endpoint=plan.provider_endpoint,
                    vision_capability_confirmed=(
                        self._vision_capability_confirmed
                        if plan.mode is AnalysisMode.LOCAL
                        else None
                    ),
                )
                def progress(value: int | None, message: str) -> None:
                    del value
                    self.progress_changed.emit(
                        None,
                        f"일괄 분석 {index}/{total} · {item.video.display_name} · {message}",
                    )

                result = analyzer.analyze(request, self._cancel_event, progress)
            except Exception as exc:
                result = _failed_job(request, exc, time.monotonic() - started)
            item.session.accept_result(
                result.job_id,
                failed=result.status is JobStatus.FAILED,
                canceled=result.status is JobStatus.CANCELED,
                partial=result.status is JobStatus.PARTIAL,
            )
            self.result_ready.emit(result)
            if result.status is JobStatus.CANCELED or self._cancel_event.is_set():
                break


def _failed_job(request, error: Exception, elapsed: float) -> AnalysisJob:
    return AnalysisJob(
        job_id=request.job_id,
        plan_id=request.plan_id,
        status=JobStatus.FAILED,
        progress=None,
        video_id=request.video_id,
        mode=request.mode,
        model_id=request.model_id,
        is_sample=request.mode is AnalysisMode.SAMPLE,
        origin=ResultOrigin.NONE,
        error=str(error) or error.__class__.__name__,
        purpose=request.purpose,
        range_start_sec=request.range_start_sec,
        range_end_sec=request.range_end_sec,
        sample_timestamps_sec=request.sample_timestamps_sec,
        elapsed_seconds=elapsed,
        requested_frame_count=len(request.sample_timestamps_sec),
        sampling_strategy=request.sampling_strategy,
        observation_guidance=request.observation_guidance,
        case_id=request.case_id,
        case_revision=request.case_revision,
        analysis_clues=request.analysis_clues,
    )


def _failed_plan_job(item: PreparedBatchVideo, error: Exception) -> AnalysisJob:
    plan = item.plan
    return AnalysisJob(
        job_id=f"batch-plan-failed-{uuid4().hex[:12]}",
        plan_id=plan.plan_id,
        status=JobStatus.FAILED,
        progress=None,
        video_id=plan.video_id,
        mode=plan.mode,
        model_id=plan.model_id,
        is_sample=plan.mode is AnalysisMode.SAMPLE,
        origin=ResultOrigin.NONE,
        error=str(error) or error.__class__.__name__,
        purpose=plan.purpose,
        range_start_sec=plan.range_start_sec,
        range_end_sec=plan.range_end_sec,
        sample_timestamps_sec=plan.sample_timestamps_sec,
        requested_frame_count=len(plan.sample_timestamps_sec),
        sampling_strategy=plan.sampling_strategy,
        observation_guidance=plan.observation_guidance,
        case_id=plan.case_id,
        case_revision=plan.case_revision,
        analysis_clues=plan.analysis_clues,
    )
