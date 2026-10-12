from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Event
from typing import Any, Protocol
from uuid import uuid4

from video_agent.contracts import ResultOrigin, Segment
from video_agent.models.provider import ProviderResponse
from video_agent.video.frames import ExtractedFrame, FFmpegFrameExtractor
from video_agent.video.probe import FFprobeVideoProbe
from video_agent.video.process import OperationCanceled


class PersonMatchDecision(StrEnum):
    SIMILAR_CANDIDATE = "similar_candidate"
    DIFFERENT_CANDIDATE = "different_candidate"
    INCONCLUSIVE = "inconclusive"


class ComparisonEvidenceQuality(StrEnum):
    GOOD = "good"
    LIMITED = "limited"
    POOR = "poor"


DECISION_LABELS = {
    PersonMatchDecision.SIMILAR_CANDIDATE: "동일 인물 유사 후보",
    PersonMatchDecision.DIFFERENT_CANDIDATE: "다른 인물 후보",
    PersonMatchDecision.INCONCLUSIVE: "판단 불가",
}

QUALITY_LABELS = {
    ComparisonEvidenceQuality.GOOD: "근거 양호",
    ComparisonEvidenceQuality.LIMITED: "근거 제한적",
    ComparisonEvidenceQuality.POOR: "근거 부족",
}


@dataclass(frozen=True, slots=True)
class PersonCandidateInput:
    video_id: str
    video_name: str
    video_path: Path
    job_id: str
    segment_index: int
    segment: Segment

    def __post_init__(self) -> None:
        if not self.video_id.strip() or not self.video_name.strip() or not self.job_id.strip():
            raise ValueError("person comparison candidate identifiers are required")
        if self.segment_index < 0:
            raise ValueError("segment index cannot be negative")
        if self.segment.video_id != self.video_id:
            raise ValueError("comparison candidate segment belongs to another video")

    @property
    def candidate_id(self) -> str:
        return f"{self.job_id}:{self.segment_index}"

    def to_reference_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "video_id": self.video_id,
            "video_name": self.video_name,
            "job_id": self.job_id,
            "segment_index": self.segment_index,
            "start_sec": self.segment.start_sec,
            "end_sec": self.segment.end_sec,
            "summary": self.segment.summary,
            "source_frame_ids": list(self.segment.source_frame_ids),
            "source_timestamps_sec": list(self.segment.source_timestamps_sec),
        }


@dataclass(frozen=True, slots=True)
class PersonPairComparison:
    left_candidate_id: str
    right_candidate_id: str
    decision: PersonMatchDecision
    observable_similarities: tuple[str, ...]
    observable_differences: tuple[str, ...]
    limitations: tuple[str, ...]
    left_frame_ids: tuple[str, ...]
    right_frame_ids: tuple[str, ...]
    appearance_similarity_score: int
    evidence_quality: ComparisonEvidenceQuality

    def __post_init__(self) -> None:
        if self.left_candidate_id == self.right_candidate_id:
            raise ValueError("a person comparison needs two different candidates")
        if not isinstance(self.decision, PersonMatchDecision):
            object.__setattr__(self, "decision", PersonMatchDecision(self.decision))
        if not isinstance(self.evidence_quality, ComparisonEvidenceQuality):
            object.__setattr__(
                self,
                "evidence_quality",
                ComparisonEvidenceQuality(self.evidence_quality),
            )
        if (
            not isinstance(self.appearance_similarity_score, int)
            or isinstance(self.appearance_similarity_score, bool)
            or not 0 <= self.appearance_similarity_score <= 100
        ):
            raise ValueError("appearance similarity score must be between 0 and 100")
        if not self.limitations:
            raise ValueError("person comparison limitations are required")
        text = " ".join(
            (*self.observable_similarities, *self.observable_differences, *self.limitations)
        ).casefold()
        forbidden = ("범죄자 확정", "범인 확정", "동일범 확정", "criminal confirmed")
        if any(item in text for item in forbidden):
            raise ValueError("person comparison cannot conclude criminal identity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_candidate_id": self.left_candidate_id,
            "right_candidate_id": self.right_candidate_id,
            "decision": self.decision.value,
            "observable_similarities": list(self.observable_similarities),
            "observable_differences": list(self.observable_differences),
            "limitations": list(self.limitations),
            "left_frame_ids": list(self.left_frame_ids),
            "right_frame_ids": list(self.right_frame_ids),
            "appearance_similarity_score": self.appearance_similarity_score,
            "evidence_quality": self.evidence_quality.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonPairComparison":
        allowed = {
            "left_candidate_id",
            "right_candidate_id",
            "decision",
            "observable_similarities",
            "observable_differences",
            "limitations",
            "left_frame_ids",
            "right_frame_ids",
            "appearance_similarity_score",
            "evidence_quality",
        }
        if not isinstance(data, dict) or set(data) != allowed:
            raise ValueError("person comparison pair fields are invalid")
        return cls(
            left_candidate_id=str(data["left_candidate_id"]),
            right_candidate_id=str(data["right_candidate_id"]),
            decision=PersonMatchDecision(str(data["decision"])),
            observable_similarities=_strings(data["observable_similarities"]),
            observable_differences=_strings(data["observable_differences"]),
            limitations=_strings(data["limitations"]),
            left_frame_ids=_strings(data["left_frame_ids"]),
            right_frame_ids=_strings(data["right_frame_ids"]),
            appearance_similarity_score=data["appearance_similarity_score"],
            evidence_quality=ComparisonEvidenceQuality(str(data["evidence_quality"])),
        )


@dataclass(frozen=True, slots=True)
class PersonComparisonReport:
    report_id: str
    case_id: str
    model_id: str
    origin: ResultOrigin
    candidates: tuple[dict[str, Any], ...]
    comparisons: tuple[PersonPairComparison, ...]
    elapsed_seconds: float
    attempted_pair_count: int
    completed_pair_count: int
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.report_id.strip() or not self.case_id.strip() or not self.model_id.strip():
            raise ValueError("comparison report identifiers and model are required")
        if not isinstance(self.origin, ResultOrigin):
            object.__setattr__(self, "origin", ResultOrigin(self.origin))
        if self.elapsed_seconds < 0:
            raise ValueError("comparison elapsed time cannot be negative")
        if not 0 <= self.completed_pair_count <= self.attempted_pair_count:
            raise ValueError("comparison pair counts are invalid")
        ids = tuple(str(item.get("candidate_id", "")) for item in self.candidates)
        if len(ids) < 2 or any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("comparison report candidate IDs must be unique")
        for candidate in self.candidates:
            _validate_candidate_reference(candidate)
        if self.completed_pair_count != len(self.comparisons):
            raise ValueError("completed comparison count must match comparison records")
        max_pairs = len(ids) * (len(ids) - 1) // 2
        if self.attempted_pair_count > max_pairs:
            raise ValueError("attempted comparison count exceeds available pairs")
        if any(
            pair.left_candidate_id not in ids or pair.right_candidate_id not in ids
            for pair in self.comparisons
        ):
            raise ValueError("comparison pair references an unknown candidate")
        if self.comparisons and self.origin is ResultOrigin.NONE:
            raise ValueError("completed comparisons require a result origin")
        if not self.comparisons and self.origin is not ResultOrigin.NONE:
            raise ValueError("empty comparisons must use none origin")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "case_id": self.case_id,
            "model_id": self.model_id,
            "origin": self.origin.value,
            "candidates": list(self.candidates),
            "comparisons": [item.to_dict() for item in self.comparisons],
            "elapsed_seconds": self.elapsed_seconds,
            "attempted_pair_count": self.attempted_pair_count,
            "completed_pair_count": self.completed_pair_count,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonComparisonReport":
        allowed = {
            "report_id",
            "case_id",
            "model_id",
            "origin",
            "candidates",
            "comparisons",
            "elapsed_seconds",
            "attempted_pair_count",
            "completed_pair_count",
            "error",
        }
        if not isinstance(data, dict) or set(data) != allowed:
            raise ValueError("person comparison report fields are invalid")
        candidates = data["candidates"]
        comparisons = data["comparisons"]
        if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
            raise ValueError("comparison candidates must be objects")
        if not isinstance(comparisons, list):
            raise ValueError("comparison pairs must be a list")
        return cls(
            report_id=str(data["report_id"]),
            case_id=str(data["case_id"]),
            model_id=str(data["model_id"]),
            origin=ResultOrigin(str(data["origin"])),
            candidates=tuple(dict(item) for item in candidates),
            comparisons=tuple(PersonPairComparison.from_dict(item) for item in comparisons),
            elapsed_seconds=float(data["elapsed_seconds"]),
            attempted_pair_count=int(data["attempted_pair_count"]),
            completed_pair_count=int(data["completed_pair_count"]),
            error=None if data["error"] is None else str(data["error"]),
        )


class ComparisonProvider(Protocol):
    config: Any

    def analyze(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
        *,
        timeout_seconds: float,
        cancel_event: Event,
        response_format: dict[str, Any] | None = None,
    ) -> ProviderResponse: ...


class PersonComparisonAnalyzer:
    """Screen cross-video evidence frames for human-reviewable appearance candidates.

    This is deliberately not a face database search, person tracker, or criminal-identity
    decision. It compares one user-selected event candidate per video and always exposes
    the source frames and limitations.
    """

    def __init__(
        self,
        provider: ComparisonProvider,
        work_dir_factory,
        *,
        probe: FFprobeVideoProbe | None = None,
        extractor: FFmpegFrameExtractor | None = None,
        pair_timeout_seconds: float = 120.0,
        max_total_seconds: float = 1800.0,
    ) -> None:
        if pair_timeout_seconds <= 0 or max_total_seconds <= 0:
            raise ValueError("person comparison time limits must be positive")
        self._provider = provider
        self._work_dir_factory = work_dir_factory
        self._probe = probe or FFprobeVideoProbe()
        self._extractor = extractor or FFmpegFrameExtractor()
        self._pair_timeout_seconds = pair_timeout_seconds
        self._max_total_seconds = max_total_seconds

    def compare(
        self,
        case_id: str,
        candidates: tuple[PersonCandidateInput, ...],
        cancel_event: Event,
        on_progress,
        on_evidence=None,
    ) -> PersonComparisonReport:
        if len(candidates) < 2:
            raise ValueError("서로 다른 영상의 후보를 두 개 이상 선택해 주세요.")
        video_ids = tuple(item.video_id for item in candidates)
        if len(video_ids) != len(set(video_ids)):
            raise ValueError("한 영상에서는 비교할 후보를 하나만 선택해 주세요.")
        report_id = f"person-compare-{uuid4().hex[:12]}"
        work_dir = self._work_dir_factory(report_id)
        started = time.monotonic()
        frames_by_candidate: dict[str, tuple[ExtractedFrame, ...]] = {}
        for index, candidate in enumerate(candidates, start=1):
            if cancel_event.is_set():
                raise OperationCanceled("인물 비교가 취소되었습니다.")
            remaining = self._max_total_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("인물 비교 프레임 준비 시간 한도에 도달했습니다.")
            on_progress(None, f"인물 비교 준비 {index}/{len(candidates)} · {candidate.video_name}")
            metadata = self._probe.probe(candidate.video_path, cancel_event)
            if metadata.video_id != candidate.video_id:
                raise ValueError("등록 이후 비교 대상 영상 파일이 변경되었습니다.")
            timestamps = _representative_timestamps(candidate.segment)
            frames = self._extractor.extract(
                candidate.video_path,
                metadata,
                timestamps,
                work_dir / f"candidate_{index:03d}",
                max_frames=2,
                max_width=1280,
                cancel_event=cancel_event,
                total_timeout_seconds=min(60.0, remaining),
            )
            frames_by_candidate[candidate.candidate_id] = frames
            if on_evidence is not None:
                on_evidence(candidate.candidate_id, frames)

        pairs = tuple(
            (left, right)
            for left_index, left in enumerate(candidates)
            for right in candidates[left_index + 1 :]
        )
        comparisons: list[PersonPairComparison] = []
        errors: list[str] = []
        test_flags: list[bool] = []
        attempted = 0
        for pair_index, (left, right) in enumerate(pairs, start=1):
            if cancel_event.is_set():
                raise OperationCanceled("인물 비교가 취소되었습니다.")
            remaining = self._max_total_seconds - (time.monotonic() - started)
            if remaining <= 0:
                errors.append("전체 인물 비교 시간 한도에 도달했습니다.")
                break
            attempted += 1
            on_progress(
                None,
                f"인물 후보 대조 {pair_index}/{len(pairs)} · {left.video_name} ↔ {right.video_name}",
            )
            left_frames = frames_by_candidate[left.candidate_id]
            right_frames = frames_by_candidate[right.candidate_id]
            try:
                response = self._provider.analyze(
                    _comparison_prompt(left, right, left_frames, right_frames),
                    (*left_frames, *right_frames),
                    timeout_seconds=min(self._pair_timeout_seconds, remaining),
                    cancel_event=cancel_event,
                    response_format=person_comparison_schema(),
                )
                comparisons.append(
                    parse_person_comparison(response, left, right, left_frames, right_frames)
                )
                test_flags.append(response.is_test_double)
            except OperationCanceled:
                raise
            except Exception as exc:
                errors.append(
                    f"{left.video_name} ↔ {right.video_name}: {str(exc) or exc.__class__.__name__}"
                )
        if comparisons:
            origin = (
                ResultOrigin.TEST_DOUBLE if all(test_flags) else ResultOrigin.REAL_MODEL
            )
        else:
            origin = ResultOrigin.NONE
        error = "\n".join(errors) or None
        return PersonComparisonReport(
            report_id=report_id,
            case_id=case_id,
            model_id=self._provider.config.model_id,
            origin=origin,
            candidates=tuple(item.to_reference_dict() for item in candidates),
            comparisons=tuple(comparisons),
            elapsed_seconds=time.monotonic() - started,
            attempted_pair_count=attempted,
            completed_pair_count=len(comparisons),
            error=error,
        )


def person_comparison_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": [item.value for item in PersonMatchDecision],
            },
            "observable_similarities": {"type": "array", "items": {"type": "string"}},
            "observable_differences": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "appearance_similarity_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
            },
            "evidence_quality": {
                "type": "string",
                "enum": [item.value for item in ComparisonEvidenceQuality],
            },
        },
        "required": [
            "decision",
            "observable_similarities",
            "observable_differences",
            "limitations",
            "appearance_similarity_score",
            "evidence_quality",
        ],
        "additionalProperties": False,
    }


def parse_person_comparison(
    response: ProviderResponse,
    left: PersonCandidateInput,
    right: PersonCandidateInput,
    left_frames: tuple[ExtractedFrame, ...],
    right_frames: tuple[ExtractedFrame, ...],
) -> PersonPairComparison:
    try:
        data = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise ValueError("인물 비교 모델이 유효한 JSON을 반환하지 않았습니다.") from exc
    required = {
        "decision",
        "observable_similarities",
        "observable_differences",
        "limitations",
        "appearance_similarity_score",
        "evidence_quality",
    }
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError("인물 비교 모델 응답 필드가 계약과 다릅니다.")
    score = data["appearance_similarity_score"]
    if not isinstance(score, int) or isinstance(score, bool):
        raise ValueError("인물 비교 외형 유사도 참고값은 0~100 정수여야 합니다.")
    return PersonPairComparison(
        left_candidate_id=left.candidate_id,
        right_candidate_id=right.candidate_id,
        decision=PersonMatchDecision(str(data["decision"])),
        observable_similarities=_strings(data["observable_similarities"]),
        observable_differences=_strings(data["observable_differences"]),
        limitations=_strings(data["limitations"]),
        left_frame_ids=tuple(item.frame_id for item in left_frames),
        right_frame_ids=tuple(item.frame_id for item in right_frames),
        appearance_similarity_score=score,
        evidence_quality=ComparisonEvidenceQuality(str(data["evidence_quality"])),
    )


def _comparison_prompt(
    left: PersonCandidateInput,
    right: PersonCandidateInput,
    left_frames: tuple[ExtractedFrame, ...],
    right_frames: tuple[ExtractedFrame, ...],
) -> str:
    left_lines = ", ".join(
        f"{item.frame_id} ({item.source_timestamp_sec:.3f}초)" for item in left_frames
    )
    right_lines = ", ".join(
        f"{item.frame_id} ({item.source_timestamp_sec:.3f}초)" for item in right_frames
    )
    return (
        "서로 다른 두 CCTV 영상의 사용자가 선택한 사건 후보 프레임을 비교합니다. "
        "앞쪽 이미지는 A 영상, 뒤쪽 이미지는 B 영상입니다.\n"
        f"A 프레임: {left_lines}\nB 프레임: {right_lines}\n"
        "목표는 이름이나 신원을 식별하는 것이 아니라, 두 후보 장면의 중심 인물이 같은 사람일 "
        "가능성을 사람이 검토할 수 있게 선별하는 것입니다. 범죄 여부, 범인, 의도, 법적 책임을 "
        "판단하거나 단정하지 마세요. 인종·민족·성별 같은 민감한 속성을 추정하지 마세요. "
        "얼굴을 생체 식별하지 말고 복장 색·무늬, 신발, 가방·소지품과 화면에서 보이는 대략적인 "
        "키·체형 비율만 비교하세요. 여러 사람이 있어 중심 인물이 불명확하거나, 가림·각도·조명·해상도·시간차로 "
        "비교가 어렵거나, 복장만 비슷하면 inconclusive를 선택하세요. appearance_similarity_score는 "
        "화면에서 관찰되는 외형의 유사 정도를 0~100으로 나타내는 검토 참고값이며 신원 일치 확률이 "
        "아닙니다. 가림·화질·각도 문제가 크면 점수를 낮추고 evidence_quality를 poor 또는 limited로 "
        "표시하세요. similar_candidate는 확정이 아니며 독립적인 사람이 원본을 확인해야 합니다. "
        "화면에서 직접 확인한 유사점과 차이점, 한계를 한국어로 JSON에 적으세요."
    )


def _representative_timestamps(segment: Segment) -> tuple[float, ...]:
    source = tuple(sorted(set(float(item) for item in segment.source_timestamps_sec)))
    if len(source) >= 2:
        return (source[0], source[-1])
    if source:
        return source
    midpoint = segment.start_sec + (segment.end_sec - segment.start_sec) / 2
    return (round(midpoint, 6),)


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("person comparison text fields must be string arrays")
    cleaned = tuple(" ".join(item.split()) for item in value if item.strip())
    if len(cleaned) != len(value):
        raise ValueError("person comparison text cannot be empty")
    return cleaned


def _validate_candidate_reference(candidate: dict[str, Any]) -> None:
    fields = {
        "candidate_id",
        "video_id",
        "video_name",
        "job_id",
        "segment_index",
        "start_sec",
        "end_sec",
        "summary",
        "source_frame_ids",
        "source_timestamps_sec",
    }
    if not isinstance(candidate, dict) or set(candidate) != fields:
        raise ValueError("comparison candidate reference fields are invalid")
    for field in ("candidate_id", "video_id", "video_name", "job_id", "summary"):
        if not isinstance(candidate[field], str) or not candidate[field].strip():
            raise ValueError("comparison candidate reference text is invalid")
    if not isinstance(candidate["segment_index"], int) or candidate["segment_index"] < 0:
        raise ValueError("comparison candidate segment index is invalid")
    start = float(candidate["start_sec"])
    end = float(candidate["end_sec"])
    if not 0 <= start < end:
        raise ValueError("comparison candidate range is invalid")
    _strings(candidate["source_frame_ids"])
    timestamps = candidate["source_timestamps_sec"]
    if not isinstance(timestamps, list) or any(
        not isinstance(item, (int, float)) or float(item) < 0 for item in timestamps
    ):
        raise ValueError("comparison candidate timestamps are invalid")
    if len(candidate["source_frame_ids"]) != len(timestamps):
        raise ValueError("comparison candidate frame references do not match timestamps")
