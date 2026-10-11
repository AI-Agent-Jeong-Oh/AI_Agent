"""Case types and the situation groups to check first, from the team demo page.

A case type only suggests which groups to check first and a starting purpose. Checked
groups set the order of review; the model is still asked to report other situations, and
no group is a finding about an offence.
"""

from __future__ import annotations

from video_agent.contracts import FocusSituation

CASE_TYPES = ("실종", "폭행", "아동학대")

SITUATION_LABELS: dict[FocusSituation, str] = {
    FocusSituation.INTERACTION: "인물 간 상호작용",
    FocusSituation.MOVEMENT: "이동·동선",
    FocusSituation.POSTURE: "자세·움직임 변화",
    FocusSituation.STAY: "체류·이탈",
    FocusSituation.COMPANY: "동행·분리",
    FocusSituation.ENVIRONMENT: "주변 객체·환경 변화",
}

# What each group covers, shown as examples and given to the model as what to look for.
SITUATION_PATTERNS: dict[FocusSituation, tuple[str, ...]] = {
    FocusSituation.INTERACTION: ("신체 접촉", "밀착", "거리 변화", "붙잡음", "충돌"),
    FocusSituation.MOVEMENT: ("화면 진입·퇴장", "방향 전환", "빠른 이동", "같은 경로 반복"),
    FocusSituation.POSTURE: (
        "넘어짐·주저앉음",
        "몸을 숙이거나 움츠림",
        "팔·상체의 빠른 움직임",
        "얼굴·머리를 가림",
        "뒤로 물러남",
    ),
    FocusSituation.STAY: ("한곳에 오래 머묾", "무리와 떨어져 있음", "자리·화면 이탈", "주변을 맴돎"),
    FocusSituation.COMPANY: ("함께 이동", "손을 잡거나 이끌려 이동", "일행에서 분리", "뒤따라감"),
    FocusSituation.ENVIRONMENT: (
        "출입문 여닫힘·통과",
        "물건 이동·쓰러짐",
        "차량 출입",
        "조명·화면 변화",
    ),
}

RECOMMENDED: dict[str, tuple[FocusSituation, ...]] = {
    "실종": (FocusSituation.MOVEMENT, FocusSituation.STAY),
    "폭행": (FocusSituation.INTERACTION, FocusSituation.POSTURE),
    "아동학대": (FocusSituation.INTERACTION, FocusSituation.COMPANY, FocusSituation.STAY),
}

DEFAULT_PURPOSES: dict[str, str] = {
    "실종": "실종 대상자의 마지막 확인 위치와 이동 경로 파악",
    "폭행": "폭행 의심 행위가 발생한 시점과 전후 상황 확인",
    "아동학대": "학대 의심 행위가 발생한 구간을 특정하고 반복 여부 확인",
}


def recommended_for(case_type: str) -> tuple[FocusSituation, ...]:
    return RECOMMENDED.get(case_type.strip(), ())


def focus_guidance(
    focus: tuple[FocusSituation, ...],
    hints: tuple[str, ...],
) -> tuple[str, ...]:
    """Observation-guidance lines for the plan and the model prompt."""
    lines = []
    if focus:
        groups = "; ".join(
            f"{SITUATION_LABELS[item]}({', '.join(SITUATION_PATTERNS[item])})" for item in focus
        )
        lines.append(
            "우선 확인 상황(확인 순서에만 반영하며 선택하지 않은 상황도 관찰하고, "
            f"보이는 움직임만 기술하며 범죄 여부는 판단하지 않음): {groups}"
        )
    if hints:
        lines.append(
            "사용자가 입력한 추가 탐색 힌트(영상 관찰 사실이 아님): " + ", ".join(hints)
        )
    return tuple(lines)
