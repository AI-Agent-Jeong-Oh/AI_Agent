from __future__ import annotations

import re
from dataclasses import dataclass


_DURATION_PURPOSE_TERMS = (
    "오래",
    "장시간",
    "머무",
    "체류",
    "지속",
    "동안",
    "linger",
    "loiter",
    "stay",
    "remain",
    "duration",
    "prolonged",
)
_DURATION_CLAIM_TERMS = _DURATION_PURPOSE_TERMS

_DIRECTION_CHANGE_TERMS = (
    "두리번",
    "둘러보",
    "주변을 살피",
    "주위를 살피",
    "눈치를",
    "고개를 돌",
    "고개 방향",
    "머리 방향",
    "얼굴 방향",
    "몸통 방향",
    "방향 변화",
    "방향이 바뀌",
    "시선 변화",
    "look around",
    "looking around",
    "head turn",
    "gaze shift",
    "scan the surroundings",
)
_REPEATED_CHANGE_TERMS = (
    "반복",
    "여러 번",
    "되풀이",
    "왔다 갔다",
    "좌우로",
    "왕복",
    "repeated",
    "multiple times",
    "back and forth",
)
_TEMPORAL_CHANGE_CLAIM_TERMS = (
    *_DIRECTION_CHANGE_TERMS,
    *_REPEATED_CHANGE_TERMS,
    "방향을 바꾸",
    "방향을 전환",
)

_FINE_DETAIL_TERMS = (
    "손가락",
    "손끝",
    "손으로",
    "손이",
    "버튼",
    "초인종",
    "스위치",
    "단추",
    "누르",
    "접촉",
    "닿",
    "finger",
    "fingertip",
    "button",
    "doorbell",
    "switch",
    "press",
    "contact",
    "touch",
)


@dataclass(frozen=True, slots=True)
class DurationRequirement:
    required: bool
    threshold_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class TemporalMotionRequirement:
    required: bool
    kind: str | None = None
    observation_guidance: str | None = None


@dataclass(frozen=True, slots=True)
class FineDetailRequirement:
    required: bool
    observation_guidance: str | None = None


def duration_requirement(text: str) -> DurationRequirement:
    normalized = " ".join(text.casefold().split())
    return DurationRequirement(
        required=any(term in normalized for term in _DURATION_PURPOSE_TERMS),
        threshold_seconds=_parse_duration_seconds(normalized),
    )


def summary_claims_duration(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(term in normalized for term in _DURATION_CLAIM_TERMS)


def temporal_motion_requirement(text: str) -> TemporalMotionRequirement:
    """Map intent-like temporal wording to an observable comparison instruction."""
    normalized = " ".join(text.casefold().split())
    if any(term in normalized for term in _DIRECTION_CHANGE_TERMS):
        return TemporalMotionRequirement(
            required=True,
            kind="direction_change",
            observation_guidance=(
                "사람의 의도나 심리를 추정하지 말고 머리·얼굴·몸통이 향하는 방향을 "
                "서로 다른 원본 시각에서 비교해 방향 변화가 반복되는지 관찰"
            ),
        )
    if any(term in normalized for term in _REPEATED_CHANGE_TERMS):
        return TemporalMotionRequirement(
            required=True,
            kind="repeated_change",
            observation_guidance=(
                "같은 신체 부위나 물체의 위치·자세 변화가 서로 다른 원본 시각에서 "
                "반복되는지 시간순으로 비교"
            ),
        )
    return TemporalMotionRequirement(required=False)


def fine_detail_requirement(text: str) -> FineDetailRequirement:
    """Identify purposes where downscaling can hide a small contact target."""
    normalized = " ".join(text.casefold().split())
    if any(term in normalized for term in _FINE_DETAIL_TERMS):
        return FineDetailRequirement(
            required=True,
            observation_guidance=(
                "손·손가락 같은 작은 신체 부위와 버튼·물체의 상대 위치를 원본 해상도에 "
                "가까운 개별 프레임에서 확인하고, 접촉 순간이 불명확하면 후보와 한계를 함께 표시"
            ),
        )
    return FineDetailRequirement(required=False)


def summary_claims_temporal_change(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(term in normalized for term in _TEMPORAL_CHANGE_CLAIM_TERMS)


def observation_span_seconds(timestamps: tuple[float, ...]) -> float | None:
    unique = tuple(sorted(set(timestamps)))
    if len(unique) < 2:
        return None
    return unique[-1] - unique[0]


def _parse_duration_seconds(text: str) -> float | None:
    units = (
        (r"(\d+(?:\.\d+)?)\s*(?:시간|hours?|hrs?)", 3600.0),
        (r"(\d+(?:\.\d+)?)\s*(?:분|minutes?|mins?)", 60.0),
        (r"(\d+(?:\.\d+)?)\s*(?:초|seconds?|secs?)", 1.0),
    )
    total = 0.0
    matched = False
    for pattern, multiplier in units:
        for raw_value in re.findall(pattern, text, flags=re.IGNORECASE):
            total += float(raw_value) * multiplier
            matched = True
    return total if matched and total > 0 else None
