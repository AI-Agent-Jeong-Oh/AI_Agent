"""Short, readable rows for one AI candidate ("요약", "확인 범위", "근거", ...).

Times are shown as minutes:seconds like the candidate list and the player ("00:05"), not
as "5.005초"; the exact values stay in the saved result and in the view's tooltip. The
engine appends its time check ("앱 시간 검증: ...") to the uncertainty text; it is shown
as its own short "시간 보정" row without changing the saved data.
"""

from __future__ import annotations

import re

from video_agent.analysis.evidence import (
    duration_requirement,
    observation_span_seconds,
    temporal_motion_requirement,
)
from video_agent.contracts import Segment
from video_agent.review.wording import tidy_seconds
from video_agent.video.files import format_milliseconds, seconds_to_milliseconds

_TIME_NOTE = " 앱 시간 검증: "
_REPLACED = re.compile(
    r"모델 제시 확인 범위 ([\d.]+)~([\d.]+)초가 근거 프레임을 포함하지 않아 "
    r"근거 시각 기준 ([\d.]+)~([\d.]+)초로 대체했습니다\.?"
)
_ROUNDED = "모델에 표시된 0.001초 단위 시각과"


def clock(seconds: float) -> str:
    """Whole seconds as on the player and the candidate list ("00:05", "01:02:03")."""
    return format_milliseconds(seconds_to_milliseconds(max(0.0, seconds)))


def split_time_note(uncertainty: str) -> tuple[str, str]:
    """(uncertainty, short time-correction note) from the engine's combined text."""
    text, found, note = uncertainty.partition(_TIME_NOTE.strip())
    if not found:
        return uncertainty.strip(), ""
    note = note.strip()
    replaced = _REPLACED.search(note)
    if replaced:
        start, end = float(replaced.group(1)), float(replaced.group(2))
        note = f"AI가 제시한 범위({clock(start)}–{clock(end)})에 근거 사진이 빠져 근거 시각으로 바꿈"
    elif _ROUNDED in note:
        note = "근거 사진 시각에 맞춰 범위를 조금 넓힘"
    return text.strip(), note


def _gap_text(timestamps: tuple[float, ...]) -> str:
    if len(timestamps) < 2:
        return f"{len(timestamps)}장"
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    if max(gaps) - min(gaps) <= 1.0:
        return f"{len(timestamps)}장, {round(sum(gaps) / len(gaps))}초 간격"
    return f"{len(timestamps)}장"


def segment_rows(
    segment: Segment, purpose: str | None, preroll_seconds: float
) -> list[tuple[str, str]]:
    timestamps = tuple(sorted(set(segment.source_timestamps_sec)))
    playback = max(0.0, segment.start_sec - preroll_seconds)
    rows = [
        ("요약", tidy_seconds(segment.summary)),
        (
            "확인 범위",
            f"{clock(segment.start_sec)}–{clock(segment.end_sec)} ({clock(playback)}부터 재생)",
        ),
    ]
    evidence = [tidy_seconds(line.strip()) for line in segment.evidence if line.strip()]
    if evidence:
        rows.append(
            ("근거", evidence[0] if len(evidence) == 1 else "\n".join(f"• {e}" for e in evidence))
        )
    if timestamps:
        rows.append(
            ("관찰 시각", " · ".join(clock(t) for t in timestamps) + f" ({_gap_text(timestamps)})")
        )

    checks: list[str] = []
    span = observation_span_seconds(timestamps)
    duration = duration_requirement(purpose or "")
    if duration.required:
        if not timestamps:
            checks.append("관찰 시각이 없어 '오래 머무름'을 판단할 수 없음")
        elif span is None:
            checks.append("관찰 시각이 하나뿐이라 '오래 머무름'을 판단할 수 없음")
        else:
            line = f"첫·마지막 사진 사이 {round(span)}초"
            if duration.threshold_seconds is not None:
                if span + 1e-6 >= duration.threshold_seconds:
                    line += (
                        f"로 기준 {duration.threshold_seconds:g}초 이상, "
                        "중간에도 계속 있었는지는 원본에서 확인"
                    )
                else:
                    line += f"로 기준 {duration.threshold_seconds:g}초에 못 미침"
            rows.append(("머문 시간", line))
    temporal = temporal_motion_requirement(purpose or "")
    if temporal.required:
        if not timestamps:
            checks.append("관찰 시각이 없어 변화 순서를 확인할 수 없음")
        elif span is None:
            checks.append("관찰 시각이 하나뿐이라 반복·방향 변화를 확인할 수 없음")
        else:
            checks.append("중간 동작과 실제 반복 횟수는 원본에서 확인")
    if checks:
        rows.append(("확인 필요", "\n".join(checks)))

    uncertainty, note = split_time_note(segment.uncertainty)
    if uncertainty:
        rows.append(("불확실성", tidy_seconds(uncertainty)))
    if note:
        rows.append(("시간 보정", note))
    return rows


def precise_text(segment: Segment) -> str:
    """Exact times for staff (tooltip): the saved values, to the millisecond."""
    observed = ", ".join(f"{t:.3f}" for t in segment.source_timestamps_sec)
    text = f"정확한 시각 · 확인 범위 {segment.start_sec:.3f}–{segment.end_sec:.3f}초"
    if observed:
        text += f" · 관찰 {observed}초"
    return text
