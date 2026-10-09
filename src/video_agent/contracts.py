from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


class AnalysisMode(StrEnum):
    SAMPLE = "sample"
    LOCAL = "local"
    EXTERNAL = "external"


class ResultOrigin(StrEnum):
    SAMPLE = "sample"
    TEST_DOUBLE = "test_double"
    REAL_MODEL = "real_model"


@dataclass(frozen=True, slots=True)
class AnalysisLimits:
    max_runtime_seconds: float = 30.0
    max_reanalysis_attempts: int = 0
    max_frames: int = 12
    max_provider_attempts: int = 1

    def __post_init__(self) -> None:
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if self.max_reanalysis_attempts < 0:
            raise ValueError("max_reanalysis_attempts cannot be negative")
        if self.max_frames <= 0:
            raise ValueError("max_frames must be positive")
        if self.max_provider_attempts != 1:
            raise ValueError("automatic provider retries are disabled; max_provider_attempts must be 1")


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    job_id: str
    plan_id: str
    approval_id: str
    video_id: str
    video_path: Path
    purpose: str
    duration_seconds: float
    mode: AnalysisMode = AnalysisMode.SAMPLE
    limits: AnalysisLimits = field(default_factory=AnalysisLimits)
    model_id: str | None = None
    range_start_sec: float = 0.0
    range_end_sec: float | None = None
    sample_timestamps_sec: tuple[float, ...] = ()
    transfer_approval_id: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id is required")
        if not self.video_id.strip():
            raise ValueError("video_id is required")
        if not self.purpose.strip():
            raise ValueError("purpose is required")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if not isinstance(self.mode, AnalysisMode):
            try:
                object.__setattr__(self, "mode", AnalysisMode(self.mode))
            except ValueError as exc:
                raise ValueError(f"unsupported analysis mode: {self.mode}") from exc
        if not self.plan_id.strip() or not self.approval_id.strip():
            raise ValueError("plan_id and approval_id are required")
        range_end = self.duration_seconds if self.range_end_sec is None else self.range_end_sec
        object.__setattr__(self, "range_end_sec", range_end)
        if not 0 <= self.range_start_sec < range_end <= self.duration_seconds + 1e-6:
            raise ValueError("analysis range must stay inside the video duration")
        if len(self.sample_timestamps_sec) > self.limits.max_frames:
            raise ValueError("sample timestamps exceed max_frames")
        if any(not self.range_start_sec <= item < range_end for item in self.sample_timestamps_sec):
            raise ValueError("sample timestamp is outside the approved range")
        if self.mode is not AnalysisMode.SAMPLE and not (self.model_id and self.model_id.strip()):
            raise ValueError("real analysis modes require an explicit model_id")
        if self.mode is AnalysisMode.EXTERNAL and not self.transfer_approval_id:
            raise ValueError("external mode requires separate transfer approval")


@dataclass(frozen=True, slots=True)
class Segment:
    video_id: str
    start_sec: float
    end_sec: float
    summary: str
    evidence: tuple[str, ...]
    uncertainty: str
    source_frame_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.video_id.strip():
            raise ValueError("video_id is required")
        if self.start_sec < 0:
            raise ValueError("start_sec cannot be negative")
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        if not self.summary.strip():
            raise ValueError("summary is required")
        if not self.evidence:
            raise ValueError("at least one evidence item is required")
        if not self.uncertainty.strip():
            raise ValueError("uncertainty is required")


@dataclass(frozen=True, slots=True)
class AnalysisJob:
    job_id: str
    status: JobStatus
    progress: int | None
    video_id: str
    segments: tuple[Segment, ...] = ()
    error: str | None = None
    plan_id: str | None = None
    mode: AnalysisMode = AnalysisMode.SAMPLE
    model_id: str | None = None
    is_sample: bool = True
    origin: ResultOrigin = ResultOrigin.SAMPLE

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id is required")
        if not self.video_id.strip():
            raise ValueError("video_id is required")
        if self.progress is not None and not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100 or None")
        if self.status is JobStatus.FAILED and not self.error:
            raise ValueError("failed jobs require an error message")
        if self.status is not JobStatus.FAILED and self.error is not None:
            raise ValueError("only failed jobs can contain an error")
        if not isinstance(self.mode, AnalysisMode):
            try:
                object.__setattr__(self, "mode", AnalysisMode(self.mode))
            except ValueError as exc:
                raise ValueError(f"unsupported analysis mode: {self.mode}") from exc
        if self.is_sample != (self.mode is AnalysisMode.SAMPLE):
            raise ValueError("is_sample must match analysis mode")
        if not self.is_sample and not self.model_id:
            raise ValueError("real analysis results require model_id")
        if not isinstance(self.origin, ResultOrigin):
            try:
                object.__setattr__(self, "origin", ResultOrigin(self.origin))
            except ValueError as exc:
                raise ValueError(f"unsupported result origin: {self.origin}") from exc
        if self.is_sample != (self.origin is ResultOrigin.SAMPLE):
            raise ValueError("sample mode must use sample origin and vice versa")

    def validate_for_duration(self, duration_seconds: float) -> None:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        for segment in self.segments:
            if segment.video_id != self.video_id:
                raise ValueError("segment video_id does not match job video_id")
            if segment.end_sec > duration_seconds + 1e-6:
                raise ValueError("segment exceeds video duration")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["mode"] = self.mode.value
        data["origin"] = self.origin.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisJob":
        raw_segments = data.get("segments", [])
        if not isinstance(raw_segments, list):
            raise ValueError("segments must be a list")
        segments = tuple(
            Segment(
                video_id=item["video_id"],
                start_sec=float(item["start_sec"]),
                end_sec=float(item["end_sec"]),
                summary=item["summary"],
                evidence=tuple(item["evidence"]),
                uncertainty=item["uncertainty"],
                source_frame_ids=tuple(item.get("source_frame_ids", [])),
            )
            for item in raw_segments
        )
        return cls(
            job_id=data["job_id"],
            status=JobStatus(data["status"]),
            progress=data.get("progress"),
            video_id=data["video_id"],
            segments=segments,
            error=data.get("error"),
            plan_id=data.get("plan_id"),
            mode=AnalysisMode(data.get("mode", "sample")),
            model_id=data.get("model_id"),
            is_sample=bool(data.get("is_sample", True)),
            origin=ResultOrigin(data.get("origin", "sample")),
        )
