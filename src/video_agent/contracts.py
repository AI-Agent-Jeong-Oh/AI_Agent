from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELED = "canceled"
    FAILED = "failed"


class AnalysisMode(StrEnum):
    SAMPLE = "sample"
    LOCAL = "local"
    EXTERNAL = "external"


class ResultOrigin(StrEnum):
    NONE = "none"
    SAMPLE = "sample"
    TEST_DOUBLE = "test_double"
    REAL_MODEL = "real_model"


class SamplingStrategy(StrEnum):
    """How approved source timestamps are intended to support model reasoning."""

    UNIFORM = "uniform"
    MOTION_PEAK = "motion_peak"
    TEMPORAL_SEQUENCE = "temporal_sequence"


class MotionScanProfile(StrEnum):
    """Local-only motion ranking policy; neither value is an action classifier."""

    GENERAL_PEAK = "general_peak"
    REPEATED_SUBTLE = "repeated_subtle"


class InformationCertainty(StrEnum):
    """How strongly a person or source supports a piece of prior information."""

    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class ClueSource(StrEnum):
    """Origin of prior information; never implies that the video confirmed it."""

    USER_STATEMENT = "user_statement"
    REPORT = "report"
    DOCUMENT = "document"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class AnalysisClue:
    """User-provided context that may be selected for an analysis plan."""

    clue_id: str
    text: str
    source: ClueSource = ClueSource.USER_STATEMENT
    certainty: InformationCertainty = InformationCertainty.ESTIMATED
    use_for_analysis: bool = True

    def __post_init__(self) -> None:
        if not self.clue_id.strip():
            raise ValueError("clue_id is required")
        normalized = " ".join(self.text.split())
        if not normalized:
            raise ValueError("clue text is required")
        if len(normalized) > 1000:
            raise ValueError("clue text cannot exceed 1000 characters")
        object.__setattr__(self, "text", normalized)
        if not isinstance(self.source, ClueSource):
            object.__setattr__(self, "source", ClueSource(self.source))
        if not isinstance(self.certainty, InformationCertainty):
            object.__setattr__(self, "certainty", InformationCertainty(self.certainty))

    def to_dict(self) -> dict[str, Any]:
        return {
            "clue_id": self.clue_id,
            "text": self.text,
            "source": self.source.value,
            "certainty": self.certainty.value,
            "use_for_analysis": self.use_for_analysis,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisClue":
        allowed = {"clue_id", "text", "source", "certainty", "use_for_analysis"}
        if not isinstance(data, dict) or not set(data).issubset(allowed):
            raise ValueError("analysis clue contains unknown fields")
        return cls(
            clue_id=str(data["clue_id"]),
            text=str(data["text"]),
            source=ClueSource(data.get("source", ClueSource.USER_STATEMENT.value)),
            certainty=InformationCertainty(
                data.get("certainty", InformationCertainty.ESTIMATED.value)
            ),
            use_for_analysis=bool(data.get("use_for_analysis", True)),
        )


@dataclass(frozen=True, slots=True)
class CaseVideoLink:
    """Non-path association between a case and a locally selected source video."""

    video_id: str
    camera_location: str = ""
    clock_offset_seconds: float = 0.0
    clock_certainty: InformationCertainty = InformationCertainty.UNKNOWN

    def __post_init__(self) -> None:
        if not self.video_id.strip():
            raise ValueError("case video link requires video_id")
        if len(self.camera_location) > 500:
            raise ValueError("camera location cannot exceed 500 characters")
        if not -86400.0 <= self.clock_offset_seconds <= 86400.0:
            raise ValueError("camera clock offset must stay within one day")
        if not isinstance(self.clock_certainty, InformationCertainty):
            object.__setattr__(
                self,
                "clock_certainty",
                InformationCertainty(self.clock_certainty),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "camera_location": self.camera_location,
            "clock_offset_seconds": self.clock_offset_seconds,
            "clock_certainty": self.clock_certainty.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseVideoLink":
        allowed = {
            "video_id",
            "camera_location",
            "clock_offset_seconds",
            "clock_certainty",
        }
        if not isinstance(data, dict) or not set(data).issubset(allowed):
            raise ValueError("case video link contains unknown fields")
        return cls(
            video_id=str(data["video_id"]),
            camera_location=str(data.get("camera_location", "")),
            clock_offset_seconds=float(data.get("clock_offset_seconds", 0.0)),
            clock_certainty=InformationCertainty(
                data.get("clock_certainty", InformationCertainty.UNKNOWN.value)
            ),
        )


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """Versioned case context stored separately from model observations."""

    case_id: str
    revision: int
    title: str
    case_type: str = ""
    occurrence_time_text: str = ""
    occurrence_time_certainty: InformationCertainty = InformationCertainty.UNKNOWN
    management_notes: str = ""
    clues: tuple[AnalysisClue, ...] = ()
    video_links: tuple[CaseVideoLink, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id is required")
        if self.revision < 1:
            raise ValueError("case revision must be at least 1")
        if not self.title.strip():
            raise ValueError("case title is required")
        if len(self.title) > 300 or len(self.case_type) > 200:
            raise ValueError("case title or type is too long")
        if len(self.occurrence_time_text) > 500 or len(self.management_notes) > 4000:
            raise ValueError("case time or management notes are too long")
        if len(self.clues) > 20:
            raise ValueError("a case can contain at most 20 prior-information clues")
        clue_ids = tuple(item.clue_id for item in self.clues)
        if len(clue_ids) != len(set(clue_ids)):
            raise ValueError("case clue IDs must be unique")
        video_ids = tuple(item.video_id for item in self.video_links)
        if len(video_ids) != len(set(video_ids)):
            raise ValueError("case video IDs must be unique")
        if not isinstance(self.occurrence_time_certainty, InformationCertainty):
            object.__setattr__(
                self,
                "occurrence_time_certainty",
                InformationCertainty(self.occurrence_time_certainty),
            )

    @property
    def analysis_clues(self) -> tuple[AnalysisClue, ...]:
        return tuple(item for item in self.clues if item.use_for_analysis)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "revision": self.revision,
            "title": self.title,
            "case_type": self.case_type,
            "occurrence_time_text": self.occurrence_time_text,
            "occurrence_time_certainty": self.occurrence_time_certainty.value,
            "management_notes": self.management_notes,
            "clues": [item.to_dict() for item in self.clues],
            "video_links": [item.to_dict() for item in self.video_links],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseRecord":
        allowed = {
            "case_id",
            "revision",
            "title",
            "case_type",
            "occurrence_time_text",
            "occurrence_time_certainty",
            "management_notes",
            "clues",
            "video_links",
        }
        if not isinstance(data, dict) or not set(data).issubset(allowed):
            raise ValueError("case record contains unknown fields")
        raw_clues = data.get("clues", [])
        raw_video_links = data.get("video_links", [])
        if not isinstance(raw_clues, list) or not isinstance(raw_video_links, list):
            raise ValueError("case clues and video links must be lists")
        return cls(
            case_id=str(data["case_id"]),
            revision=int(data["revision"]),
            title=str(data["title"]),
            case_type=str(data.get("case_type", "")),
            occurrence_time_text=str(data.get("occurrence_time_text", "")),
            occurrence_time_certainty=InformationCertainty(
                data.get(
                    "occurrence_time_certainty",
                    InformationCertainty.UNKNOWN.value,
                )
            ),
            management_notes=str(data.get("management_notes", "")),
            clues=tuple(AnalysisClue.from_dict(item) for item in raw_clues),
            video_links=tuple(CaseVideoLink.from_dict(item) for item in raw_video_links),
        )


@dataclass(frozen=True, slots=True)
class MotionProposal:
    """A local pixel-change window proposed for a separately approved analysis."""

    proposal_id: str
    video_id: str
    start_sec: float
    end_sec: float
    peak_timestamp_sec: float
    motion_score: float
    recommended_sampling_interval_sec: float

    def __post_init__(self) -> None:
        if not self.proposal_id.strip() or not self.video_id.strip():
            raise ValueError("proposal_id and video_id are required")
        if not 0 <= self.start_sec < self.end_sec:
            raise ValueError("motion proposal range is invalid")
        if not self.start_sec <= self.peak_timestamp_sec <= self.end_sec:
            raise ValueError("motion proposal peak must stay inside its range")
        if self.motion_score < 0:
            raise ValueError("motion score cannot be negative")
        if self.recommended_sampling_interval_sec <= 0:
            raise ValueError("recommended sampling interval must be positive")


@dataclass(frozen=True, slots=True)
class MotionScanResult:
    """Bounded FFmpeg frame-difference result; this is not an AI finding."""

    scan_id: str
    video_id: str
    range_start_sec: float
    range_end_sec: float
    sample_interval_sec: float
    sampled_frame_count: int
    proposals: tuple[MotionProposal, ...]
    profile: MotionScanProfile = MotionScanProfile.GENERAL_PEAK

    def __post_init__(self) -> None:
        if not self.scan_id.strip() or not self.video_id.strip():
            raise ValueError("scan_id and video_id are required")
        if not 0 <= self.range_start_sec < self.range_end_sec:
            raise ValueError("motion scan range is invalid")
        if self.sample_interval_sec <= 0:
            raise ValueError("motion scan interval must be positive")
        if self.sampled_frame_count < 0:
            raise ValueError("sampled frame count cannot be negative")
        if not isinstance(self.profile, MotionScanProfile):
            try:
                object.__setattr__(self, "profile", MotionScanProfile(self.profile))
            except ValueError as exc:
                raise ValueError(f"unsupported motion scan profile: {self.profile}") from exc
        for proposal in self.proposals:
            if proposal.video_id != self.video_id:
                raise ValueError("motion proposal video_id does not match scan video_id")
            if not (
                self.range_start_sec <= proposal.start_sec < proposal.end_sec <= self.range_end_sec
            ):
                raise ValueError("motion proposal exceeds scanned range")


@dataclass(frozen=True, slots=True)
class AnalysisLimits:
    max_runtime_seconds: float = 30.0
    max_reanalysis_attempts: int = 0
    max_frames: int = 12
    max_provider_attempts: int = 1
    max_provider_requests: int = 1
    max_frames_per_request: int = 12

    def __post_init__(self) -> None:
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if self.max_reanalysis_attempts < 0:
            raise ValueError("max_reanalysis_attempts cannot be negative")
        if self.max_frames <= 0:
            raise ValueError("max_frames must be positive")
        if self.max_provider_attempts != 1:
            raise ValueError("automatic provider retries are disabled; max_provider_attempts must be 1")
        if self.max_provider_requests <= 0:
            raise ValueError("max_provider_requests must be positive")
        if self.max_frames_per_request <= 0:
            raise ValueError("max_frames_per_request must be positive")
        if self.max_provider_requests * self.max_frames_per_request < self.max_frames:
            raise ValueError("provider request capacity must cover max_frames")


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
    sampling_strategy: SamplingStrategy = SamplingStrategy.UNIFORM
    observation_guidance: tuple[str, ...] = ()
    transfer_approval_id: str | None = None
    case_id: str | None = None
    case_revision: int | None = None
    analysis_clues: tuple[AnalysisClue, ...] = ()

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
        if not isinstance(self.sampling_strategy, SamplingStrategy):
            try:
                object.__setattr__(
                    self,
                    "sampling_strategy",
                    SamplingStrategy(self.sampling_strategy),
                )
            except ValueError as exc:
                raise ValueError(
                    f"unsupported sampling strategy: {self.sampling_strategy}"
                ) from exc
        if any(not item.strip() for item in self.observation_guidance):
            raise ValueError("observation guidance cannot contain blank items")
        if (self.case_id is None) != (self.case_revision is None):
            raise ValueError("case_id and case_revision must be provided together")
        if self.case_id is not None and not self.case_id.strip():
            raise ValueError("case_id cannot be blank")
        if self.case_revision is not None and self.case_revision < 1:
            raise ValueError("case_revision must be at least 1")
        if len(self.analysis_clues) > 20:
            raise ValueError("analysis request cannot contain more than 20 clues")
        if any(not clue.use_for_analysis for clue in self.analysis_clues):
            raise ValueError("analysis request can contain only selected analysis clues")
        clue_ids = tuple(item.clue_id for item in self.analysis_clues)
        if len(clue_ids) != len(set(clue_ids)):
            raise ValueError("analysis clue IDs must be unique")


@dataclass(frozen=True, slots=True)
class Segment:
    video_id: str
    start_sec: float
    end_sec: float
    summary: str
    evidence: tuple[str, ...]
    uncertainty: str
    source_frame_ids: tuple[str, ...] = ()
    source_timestamps_sec: tuple[float, ...] = ()

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
        if any(timestamp < 0 for timestamp in self.source_timestamps_sec):
            raise ValueError("source timestamps cannot be negative")
        if self.source_timestamps_sec and len(self.source_timestamps_sec) != len(self.source_frame_ids):
            raise ValueError("source timestamps must correspond to source frame IDs")


@dataclass(frozen=True, slots=True)
class ModelExecutionInfo:
    provider_id: str
    context_window_tokens: int | None
    max_output_tokens: int | None
    temperature: float | None
    provider_request_count: int
    completed_request_count: int
    input_frame_count: int
    input_frame_dimensions: tuple[str, ...]
    input_frame_ids: tuple[str, ...]
    input_frame_timestamps_sec: tuple[float, ...]
    provider_image_count: int | None = None
    provider_image_dimensions: tuple[str, ...] = ()
    input_representation: str = "individual_frames"
    provider_total_seconds: float | None = None
    model_load_seconds: float | None = None
    prompt_evaluation_seconds: float | None = None
    generation_seconds: float | None = None
    prompt_token_count: int | None = None
    output_token_count: int | None = None
    prompt_version: str | None = None
    batch_candidate_counts: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id is required")
        if self.provider_request_count <= 0:
            raise ValueError("provider_request_count must be positive")
        if not 0 <= self.completed_request_count <= self.provider_request_count:
            raise ValueError("completed_request_count is outside provider request count")
        if self.input_frame_count < 0:
            raise ValueError("input_frame_count cannot be negative")
        if len(self.input_frame_dimensions) != self.input_frame_count:
            raise ValueError("input frame dimensions must correspond to input frames")
        if self.input_frame_ids and len(self.input_frame_ids) != self.input_frame_count:
            raise ValueError("input frame IDs must correspond to input frames")
        if self.input_frame_timestamps_sec and len(self.input_frame_timestamps_sec) != self.input_frame_count:
            raise ValueError("input frame timestamps must correspond to input frames")
        if any(timestamp < 0 for timestamp in self.input_frame_timestamps_sec):
            raise ValueError("input frame timestamps cannot be negative")
        if self.provider_image_count is None:
            object.__setattr__(self, "provider_image_count", self.input_frame_count)
        if not self.provider_image_dimensions:
            object.__setattr__(
                self,
                "provider_image_dimensions",
                self.input_frame_dimensions,
            )
        if self.provider_image_count < 0:
            raise ValueError("provider image count cannot be negative")
        if len(self.provider_image_dimensions) != self.provider_image_count:
            raise ValueError("provider image dimensions must correspond to provider images")
        if self.input_representation not in {
            "individual_frames",
            "temporal_storyboard",
            "fine_detail_overview_center_crop",
        }:
            raise ValueError("unsupported input representation")
        if self.prompt_version is not None and not self.prompt_version.strip():
            raise ValueError("prompt_version cannot be blank")
        if (self.batch_candidate_counts or self.prompt_version is not None) and (
            len(self.batch_candidate_counts) != self.completed_request_count
            or any(count < 0 or count > 5 for count in self.batch_candidate_counts)
        ):
            raise ValueError(
                "batch candidate counts must correspond to completed requests and stay within 0..5"
            )


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
    origin: ResultOrigin = ResultOrigin.NONE
    purpose: str | None = None
    range_start_sec: float | None = None
    range_end_sec: float | None = None
    sample_timestamps_sec: tuple[float, ...] = ()
    elapsed_seconds: float | None = None
    requested_frame_count: int = 0
    analyzed_frame_count: int = 0
    model_execution: ModelExecutionInfo | None = None
    sampling_strategy: SamplingStrategy = SamplingStrategy.UNIFORM
    observation_guidance: tuple[str, ...] = ()
    case_id: str | None = None
    case_revision: int | None = None
    analysis_clues: tuple[AnalysisClue, ...] = ()

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id is required")
        if not self.video_id.strip():
            raise ValueError("video_id is required")
        if self.progress is not None and not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100 or None")
        if self.status in {JobStatus.FAILED, JobStatus.PARTIAL} and not self.error:
            raise ValueError("failed and partial jobs require an error message")
        if self.status not in {JobStatus.FAILED, JobStatus.PARTIAL} and self.error is not None:
            raise ValueError("only failed and partial jobs can contain an error")
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
        if self.status in {JobStatus.COMPLETED, JobStatus.PARTIAL}:
            if self.is_sample != (self.origin is ResultOrigin.SAMPLE):
                raise ValueError("completed sample mode must use sample origin and vice versa")
            if not self.is_sample and self.origin is ResultOrigin.NONE:
                raise ValueError("actual completed results require a model result origin")
        elif self.origin is not ResultOrigin.NONE:
            raise ValueError("non-result jobs must use none origin")
        if self.elapsed_seconds is not None and self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds cannot be negative")
        if self.requested_frame_count < 0 or self.analyzed_frame_count < 0:
            raise ValueError("frame counts cannot be negative")
        if self.analyzed_frame_count > self.requested_frame_count:
            raise ValueError("analyzed frames cannot exceed requested frames")
        if self.range_start_sec is not None and self.range_end_sec is not None:
            if not 0 <= self.range_start_sec < self.range_end_sec:
                raise ValueError("stored analysis range is invalid")
        if not isinstance(self.sampling_strategy, SamplingStrategy):
            try:
                object.__setattr__(
                    self,
                    "sampling_strategy",
                    SamplingStrategy(self.sampling_strategy),
                )
            except ValueError as exc:
                raise ValueError(
                    f"unsupported sampling strategy: {self.sampling_strategy}"
                ) from exc
        if any(not item.strip() for item in self.observation_guidance):
            raise ValueError("observation guidance cannot contain blank items")
        if (self.case_id is None) != (self.case_revision is None):
            raise ValueError("case_id and case_revision must be provided together")
        if self.case_revision is not None and self.case_revision < 1:
            raise ValueError("case_revision must be at least 1")
        if any(not clue.use_for_analysis for clue in self.analysis_clues):
            raise ValueError("stored analysis context can contain only selected clues")

    def validate_for_duration(self, duration_seconds: float) -> None:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        for segment in self.segments:
            if segment.video_id != self.video_id:
                raise ValueError("segment video_id does not match job video_id")
            if segment.end_sec > duration_seconds + 1e-6:
                raise ValueError("segment exceeds video duration")
            if any(timestamp > duration_seconds + 1e-6 for timestamp in segment.source_timestamps_sec):
                raise ValueError("segment evidence timestamp exceeds video duration")
            if any(
                not segment.start_sec <= timestamp <= segment.end_sec
                for timestamp in segment.source_timestamps_sec
            ):
                raise ValueError("segment review range does not include evidence timestamp")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["mode"] = self.mode.value
        data["origin"] = self.origin.value
        data["sampling_strategy"] = self.sampling_strategy.value
        data["analysis_clues"] = [item.to_dict() for item in self.analysis_clues]
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
                source_timestamps_sec=tuple(float(value) for value in item.get("source_timestamps_sec", [])),
            )
            for item in raw_segments
        )
        raw_execution = data.get("model_execution")
        model_execution = (
            ModelExecutionInfo(
                provider_id=raw_execution["provider_id"],
                context_window_tokens=raw_execution.get("context_window_tokens"),
                max_output_tokens=raw_execution.get("max_output_tokens"),
                temperature=raw_execution.get("temperature"),
                provider_request_count=int(raw_execution["provider_request_count"]),
                completed_request_count=int(raw_execution["completed_request_count"]),
                input_frame_count=int(raw_execution["input_frame_count"]),
                input_frame_dimensions=tuple(raw_execution.get("input_frame_dimensions", [])),
                input_frame_ids=tuple(raw_execution.get("input_frame_ids", [])),
                input_frame_timestamps_sec=tuple(
                    float(value) for value in raw_execution.get("input_frame_timestamps_sec", [])
                ),
                provider_image_count=int(
                    raw_execution.get(
                        "provider_image_count",
                        raw_execution.get("input_frame_count", 0),
                    )
                ),
                provider_image_dimensions=tuple(
                    raw_execution.get(
                        "provider_image_dimensions",
                        raw_execution.get("input_frame_dimensions", []),
                    )
                ),
                input_representation=raw_execution.get(
                    "input_representation",
                    "individual_frames",
                ),
                provider_total_seconds=raw_execution.get("provider_total_seconds"),
                model_load_seconds=raw_execution.get("model_load_seconds"),
                prompt_evaluation_seconds=raw_execution.get("prompt_evaluation_seconds"),
                generation_seconds=raw_execution.get("generation_seconds"),
                prompt_token_count=raw_execution.get("prompt_token_count"),
                output_token_count=raw_execution.get("output_token_count"),
                prompt_version=raw_execution.get("prompt_version"),
                batch_candidate_counts=tuple(
                    int(value) for value in raw_execution.get("batch_candidate_counts", [])
                ),
            )
            if isinstance(raw_execution, dict)
            else None
        )
        status = JobStatus(data["status"])
        default_origin = "sample" if status is JobStatus.COMPLETED and bool(data.get("is_sample", True)) else "none"
        stored_origin = data.get("origin", default_origin)
        if status not in {JobStatus.COMPLETED, JobStatus.PARTIAL}:
            stored_origin = "none"
        return cls(
            job_id=data["job_id"],
            status=status,
            progress=data.get("progress"),
            video_id=data["video_id"],
            segments=segments,
            error=data.get("error"),
            plan_id=data.get("plan_id"),
            mode=AnalysisMode(data.get("mode", "sample")),
            model_id=data.get("model_id"),
            is_sample=bool(data.get("is_sample", True)),
            origin=ResultOrigin(stored_origin),
            purpose=data.get("purpose"),
            range_start_sec=data.get("range_start_sec"),
            range_end_sec=data.get("range_end_sec"),
            sample_timestamps_sec=tuple(float(value) for value in data.get("sample_timestamps_sec", [])),
            elapsed_seconds=data.get("elapsed_seconds"),
            requested_frame_count=int(data.get("requested_frame_count", 0)),
            analyzed_frame_count=int(data.get("analyzed_frame_count", 0)),
            model_execution=model_execution,
            sampling_strategy=SamplingStrategy(data.get("sampling_strategy", "uniform")),
            observation_guidance=tuple(data.get("observation_guidance", [])),
            case_id=data.get("case_id"),
            case_revision=(
                int(data["case_revision"]) if data.get("case_revision") is not None else None
            ),
            analysis_clues=tuple(
                AnalysisClue.from_dict(item) for item in data.get("analysis_clues", [])
            ),
        )
