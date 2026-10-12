"""Rule-based result report ("CCTV 영상 검토 결과 보고") for handing a reviewed analysis on.

No model is called. Every sentence the app writes comes from the fixed templates below, in
the short official style (□ ○ - ※, sentences ending in ~함/~임); reviewer memos are
quoted as the reviewer's own words. The templates never state that a crime happened or
what a person intended, and prior information is never presented as something the video
confirmed. One video and a whole case use the same report; times stay relative to each
video's own start.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath

from video_agent.agent.situations import SITUATION_LABELS
from video_agent.contracts import (
    AnalysisClue,
    AnalysisJob,
    AnalysisMode,
    CaseRecord,
    CaseVideoLink,
    ClueSource,
    InformationCertainty,
    JobStatus,
    ResultOrigin,
    ReviewJudgement,
    ReviewSession,
)
from video_agent.knowledge.case_rules import CaseRules
from video_agent.knowledge.common_rules import CommonRules
from video_agent.review.wording import tidy_seconds
from video_agent.video.files import format_milliseconds, seconds_to_milliseconds


REPORT_TITLE = "CCTV 영상 검토 결과 보고"
JUDGEMENT_LABELS = {
    ReviewJudgement.UNREVIEWED: "미검토",
    ReviewJudgement.RELEVANT: "관련 있음",
    ReviewJudgement.NEEDS_CHECK: "추가 확인",
    ReviewJudgement.EXCLUDED: "제외",
}
_CERTAINTY_LABELS = {
    InformationCertainty.CONFIRMED: "확인",
    InformationCertainty.ESTIMATED: "추정",
    InformationCertainty.UNKNOWN: "미확인",
}
_SOURCE_LABELS = {
    ClueSource.USER_STATEMENT: "사용자 진술",
    ClueSource.REPORT: "신고·보고",
    ClueSource.DOCUMENT: "문서",
    ClueSource.OTHER: "기타",
}

LAW_DISCLAIMER = "법 적용 여부는 수사관이 판단하며, 이 보고는 판단하지 않음"
_COMMON_GROUP = "영상 증거 처리 공통"
_SAMPLING_NOTE = (
    "AI 후보는 일정 간격의 사진만 보고 고른 것으로, 후보가 없는 구간에 해당 장면이 "
    "없다는 뜻이 아님"
)
_NO_JUDGEMENT_NOTE = "영상만으로 범죄 성립이나 사람의 의도를 판단하지 않음"
_TIMELINE_NOTE = "시각은 각 영상의 시작 기준이며, 카메라 사이의 시간 순서는 비교하지 않음"


@dataclass(frozen=True, slots=True)
class SummarySection:
    key: str
    title: str
    body: str
    editable: bool = True
    # Pre-escaped HTML for fixed sections (tables, links); plain `body` stays authoritative.
    html_body: str | None = None


@dataclass(frozen=True, slots=True)
class ResultSummary:
    job_id: str
    origin: ResultOrigin
    # Only for demonstration and test results (shown above the title); empty for real ones.
    origin_notice: str
    generated_at: str
    warnings: tuple[str, ...]
    sections: tuple[SummarySection, ...]
    # Set only for a case-wide report that covers several cameras; job_id is then empty.
    case_id: str | None = None
    camera_count: int = 0
    title: str = REPORT_TITLE
    # The header table: 사건명, 사건 유형, 작성일, 검토 대상 (작성자 is filled in the window).
    meta: tuple[tuple[str, str], ...] = ()

    def section(self, key: str) -> SummarySection:
        return next(item for item in self.sections if item.key == key)

    @property
    def document_id(self) -> str:
        return f"사건 ID {self.case_id}" if self.case_id is not None else f"결과 ID {self.job_id}"

    @property
    def file_stem(self) -> str:
        return f"사건_{self.case_id}" if self.case_id is not None else self.job_id


@dataclass(frozen=True, slots=True)
class CameraSummaryInput:
    """One case video for the report; job is None when it has no saved result."""

    video_id: str
    camera: str
    file_name: str | None
    link: CaseVideoLink | None = None
    job: AnalysisJob | None = None
    session: ReviewSession | None = None
    unsaved_count: int = 0
    review_load_error: str | None = None


def build_summary(
    job: AnalysisJob,
    session: ReviewSession | None,
    case: CaseRecord | None,
    *,
    video_file_name: str | None,
    unsaved_count: int = 0,
    case_load_error: str | None = None,
    common_rules: CommonRules | None = None,
    common_rules_error: str | None = None,
    case_rules: CaseRules | None = None,
    generated_at: datetime | None = None,
) -> ResultSummary:
    """The report for one video, from saved data only (unsaved drafts are excluded)."""
    camera = CameraSummaryInput(
        video_id=job.video_id,
        camera=PurePath(video_file_name).stem if video_file_name else "영상",
        file_name=video_file_name,
        job=job,
        session=session,
        unsaved_count=unsaved_count,
    )
    return _build_report(
        case,
        [camera],
        case_id=None,
        case_load_error=case_load_error,
        common_rules=common_rules,
        common_rules_error=common_rules_error,
        case_rules=case_rules,
        generated_at=generated_at,
    )


def build_case_summary(
    case: CaseRecord | None,
    cameras: Sequence[CameraSummaryInput],
    *,
    case_id: str,
    case_load_error: str | None = None,
    common_rules: CommonRules | None = None,
    common_rules_error: str | None = None,
    case_rules: CaseRules | None = None,
    generated_at: datetime | None = None,
) -> ResultSummary:
    """The report for every camera of a case (cameras are never put on one timeline)."""
    return _build_report(
        case,
        list(cameras),
        case_id=case_id,
        case_load_error=case_load_error,
        common_rules=common_rules,
        common_rules_error=common_rules_error,
        case_rules=case_rules,
        generated_at=generated_at,
    )


def origin_notice(job: AnalysisJob) -> str:
    """A line above the title only when the result is not a real AI analysis."""
    if job.origin is ResultOrigin.SAMPLE:
        return "시연용 결과 (실제 AI 분석 아님). 후보는 영상 내용을 보지 않고 만든 예시임"
    if job.origin is ResultOrigin.TEST_DOUBLE:
        return "테스트 응답 (실제 AI 분석 아님)"
    if job.origin is ResultOrigin.REAL_MODEL:
        return ""
    return "결과 출처 확인 안 됨"


# ----- building ------------------------------------------------------------------

_CAUTION_ORDER = (ResultOrigin.SAMPLE, ResultOrigin.TEST_DOUBLE, ResultOrigin.REAL_MODEL)


@dataclass(frozen=True, slots=True)
class _Candidate:
    camera: CameraSummaryInput
    index: int
    judgement: ReviewJudgement
    memo: str
    tags: tuple[str, ...]

    @property
    def segment(self):
        return self.camera.job.segments[self.index]

    @property
    def where(self) -> str:
        segment = self.segment
        return f"{self.camera.camera} {_time(segment.start_sec)}~{_time(segment.end_sec)}"


def _build_report(
    case: CaseRecord | None,
    cameras: list[CameraSummaryInput],
    *,
    case_id: str | None,
    case_load_error: str | None,
    common_rules: CommonRules | None,
    common_rules_error: str | None,
    case_rules: CaseRules | None,
    generated_at: datetime | None,
) -> ResultSummary:
    analyzed = [camera for camera in cameras if camera.job is not None]
    if not analyzed:
        raise ValueError("a report needs at least one camera with a saved result")
    for camera in analyzed:
        if camera.session is not None:
            camera.session.validate_against(camera.job)
    several = len(cameras) > 1
    candidates = [
        _candidate(camera, index)
        for camera in analyzed
        for index in sorted(
            range(len(camera.job.segments)),
            key=lambda i, c=camera: (c.job.segments[i].start_sec, c.job.segments[i].end_sec, i),
        )
    ]
    warnings: list[str] = []
    for camera in cameras:
        prefix = f"{camera.camera}: " if several else ""
        if camera.review_load_error:
            warnings.append(
                f"{prefix}검토 기록을 읽지 못해 미검토로 표시했습니다 ({camera.review_load_error})"
            )
        if camera.unsaved_count:
            warnings.append(
                f"{prefix}저장하지 않은 기록 {camera.unsaved_count}건은 보고에 포함되지 않았습니다."
            )
    if case_load_error:
        warnings.append(f"사건 정보를 불러오지 못했습니다: {case_load_error}")

    observations_text, observations_html = _observations(candidates, several)
    law_text, law_html = _law(case, common_rules, common_rules_error, case_rules)
    sections = (
        SummarySection("overview", "1. 개요", _overview(case, analyzed, candidates)),
        SummarySection(
            "observations",
            "2. 주요 관찰",
            observations_text,
            editable=False,
            html_body=observations_html,
        ),
        SummarySection("opinions", "3. 수사관 검토 의견", _opinions(candidates)),
        SummarySection("clues", "4. 사전 정보 (영상으로 확인하지 않은 정보)", _clues(case, analyzed)),
        SummarySection("law", "5. 참고 법령", law_text, editable=False, html_body=law_html),
        SummarySection(
            "open_items", "6. 추가 확인 및 유의 사항", _open_items(cameras, candidates, several)
        ),
        SummarySection("appendix", "붙임. 분석 조건", _appendix(case, cameras)),
    )
    moment = generated_at or datetime.now().astimezone()
    jobs = [camera.job for camera in analyzed]
    origin = next(
        (item for item in _CAUTION_ORDER if any(job.origin is item for job in jobs)), jobs[0].origin
    )
    return ResultSummary(
        job_id="" if case_id is not None else jobs[0].job_id,
        origin=origin,
        origin_notice=_origin_notice(analyzed),
        generated_at=moment.isoformat(timespec="seconds"),
        warnings=tuple(warnings),
        sections=sections,
        case_id=case_id,
        camera_count=len(cameras) if case_id is not None else 0,
        meta=_meta(case, cameras, moment),
    )


def _candidate(camera: CameraSummaryInput, index: int) -> _Candidate:
    review = camera.session.review_for(index) if camera.session is not None else None
    return _Candidate(
        camera=camera,
        index=index,
        judgement=review.judgement if review is not None else ReviewJudgement.UNREVIEWED,
        memo=review.memo.strip() if review is not None else "",
        tags=tuple(review.tags) if review is not None else (),
    )


def _origin_notice(analyzed: Sequence[CameraSummaryInput]) -> str:
    notices: list[str] = []
    for camera in analyzed:
        notice = origin_notice(camera.job)
        if notice and notice not in notices:
            notices.append(notice)
    if len(analyzed) > 1 and notices:
        return "일부 영상은 " + " / ".join(notices)
    return " / ".join(notices)


def _meta(
    case: CaseRecord | None, cameras: Sequence[CameraSummaryInput], moment: datetime
) -> tuple[tuple[str, str], ...]:
    names = [camera.file_name or camera.camera for camera in cameras]
    return (
        ("사건명", case.title if case is not None else "미입력"),
        ("사건 유형", (case.case_type or "미입력") if case is not None else "미입력"),
        ("작성일", f"{moment:%Y. %m. %d.}"),
        ("검토 대상", f"CCTV {len(cameras)}대 ({', '.join(names)})"),
    )


def _overview(
    case: CaseRecord | None,
    analyzed: Sequence[CameraSummaryInput],
    candidates: Sequence[_Candidate],
) -> str:
    purposes: list[str] = []
    for camera in analyzed:
        purpose = " ".join((camera.job.purpose or "").split())
        if purpose and purpose not in purposes:
            purposes.append(purpose)
    sample = all(camera.job.mode is AnalysisMode.SAMPLE for camera in analyzed)
    lines = [
        f"□ 검토 목적: {' / '.join(purposes) or '미기록'}",
        "□ 검토 방법: "
        + (
            "시연용 예시 후보(실제 AI 분석 아님)를 확인함"
            if sample
            else "AI가 고른 후보 구간을 수사관이 원본 영상으로 직접 확인함"
        ),
    ]
    if case is not None and case.focus_situations:
        focus = ", ".join(SITUATION_LABELS[item] for item in case.focus_situations)
        lines.append(f"  ○ 중점 관찰: {focus}")
    counts = {judgement: 0 for judgement in ReviewJudgement}
    for candidate in candidates:
        counts[candidate.judgement] += 1
    lines.append(
        f"  ○ 검토 결과: 후보 {len(candidates)}건 중 "
        + ", ".join(
            f"{JUDGEMENT_LABELS[judgement]} {counts[judgement]}건"
            for judgement in (
                ReviewJudgement.RELEVANT,
                ReviewJudgement.NEEDS_CHECK,
                ReviewJudgement.EXCLUDED,
                ReviewJudgement.UNREVIEWED,
            )
        )
    )
    if case is not None and case.occurrence_time_text:
        # The occurrence time is no longer asked; an older case that has one still shows it.
        certainty = _CERTAINTY_LABELS[case.occurrence_time_certainty]
        lines.append(f"  ○ 발생 시각(신고 기준): {case.occurrence_time_text} ({certainty})")
    return "\n".join(lines)


def _observations(candidates: Sequence[_Candidate], several: bool) -> tuple[str, str]:
    chosen = [
        candidate
        for candidate in candidates
        if candidate.judgement in (ReviewJudgement.RELEVANT, ReviewJudgement.NEEDS_CHECK)
    ]
    if not chosen:
        text = "□ 관련 있음 또는 추가 확인으로 판단한 후보 없음"
        return text, f'<div class="sq">{html.escape(text)}</div>'
    lines = []
    rows = []
    for candidate in chosen:
        segment = candidate.segment
        summary = tidy_seconds(segment.summary)
        lines.append(
            f"○ {candidate.where} [{JUDGEMENT_LABELS[candidate.judgement]}] {summary}"
            + (f" (수사관 메모: {candidate.memo})" if candidate.memo else "")
        )
        rows.append(
            (
                candidate.camera.camera,
                f"{_time(segment.start_sec)}~{_time(segment.end_sec)}",
                summary,
                JUDGEMENT_LABELS[candidate.judgement],
                candidate.memo or "-",
            )
        )
    if several:
        lines.append(f"※ {_TIMELINE_NOTE}")
    header = (
        ("영상", "15%"),
        ("시각", "16%"),
        ("관찰 내용 (AI 요약)", ""),
        ("판단", "12%"),
        ("수사관 메모", "22%"),
    )
    markup = ['<table class="grid" cellspacing="0" cellpadding="4" border="1"><tr>']
    for label, width in header:
        size = f' width="{width}"' if width else ""
        markup.append(f"<th{size}>{html.escape(label)}</th>")
    markup.append("</tr>")
    for row in rows:
        markup.append("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>")
    markup.append("</table>")
    if several:
        markup.append(f'<p class="note">※ {html.escape(_TIMELINE_NOTE)}</p>')
    return "\n".join(lines), "".join(markup)


def _opinions(candidates: Sequence[_Candidate]) -> str:
    lines = []
    for candidate in candidates:
        if candidate.judgement is ReviewJudgement.EXCLUDED:
            continue
        if candidate.memo or candidate.tags:
            tags = f" [태그: {', '.join(candidate.tags)}]" if candidate.tags else ""
            lines.append(f"□ {candidate.where}: {candidate.memo or '메모 없음'}{tags}")
    excluded = [c for c in candidates if c.judgement is ReviewJudgement.EXCLUDED]
    if excluded:
        lines.append(f"□ 제외 {len(excluded)}건")
        for candidate in excluded:
            tags = f" [태그: {', '.join(candidate.tags)}]" if candidate.tags else ""
            lines.append(f"  ○ {candidate.where}: {candidate.memo or '사유 미기록'}{tags}")
    return "\n".join(lines) or "□ 별도 의견 없음"


def _clues(case: CaseRecord | None, analyzed: Sequence[CameraSummaryInput]) -> str:
    seen: list[AnalysisClue] = []
    for camera in analyzed:
        for clue in camera.job.analysis_clues:
            if all(clue.clue_id != item.clue_id for item in seen):
                seen.append(clue)
    lines = [f"○ {clue.text} ({_SOURCE_LABELS[clue.source]})" for clue in seen]
    if case is not None and case.focus_hints:
        lines.append(f"○ 추가 탐색 힌트: {', '.join(case.focus_hints)} (사용자 입력)")
    if not lines:
        return "○ 없음"
    lines.append("※ 위 내용은 신고·진술 등에서 받은 정보로, 영상으로 확인한 사실이 아님")
    return "\n".join(lines)


def _law(
    case: CaseRecord | None,
    common_rules: CommonRules | None,
    common_rules_error: str | None,
    case_rules: CaseRules | None,
) -> tuple[str, str]:
    case_type = case.case_type if case is not None else ""
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    added_by_app = False
    for rule in case_rules.for_case_type(case_type) if case_rules is not None else ():
        added_by_app = added_by_app or rule.origin == "app"
        _add_to_group(groups, rule.law_name, rule.article, rule.link)
    common = (
        [(f"{_short_law_name(rule.law_name)} {rule.article}", rule.link) for rule in common_rules.rules]
        if common_rules is not None
        else []
    )
    text = []
    markup = [
        '<table class="grid law" cellspacing="0" cellpadding="4" border="1">'
        '<tr><th width="34%">법령</th><th>조문</th></tr>'
    ]
    if groups:
        for law_name, articles in groups:
            text.append(f"○ {law_name}: " + ", ".join(article for article, _link in articles))
            markup.append(
                f"<tr><td>{html.escape(law_name)}</td><td>"
                + ", ".join(_link_html(article, link) for article, link in articles)
                + "</td></tr>"
            )
    else:
        kind = f"'{case_type}'" if case_type else "미입력"
        text.append(f"○ 사건 유형({kind})에 따른 법령: 해당 자료 없음 (공통 규정만 참고)")
        markup.append(f"<tr><td>사건 유형별</td><td>{html.escape(f'{kind}: 해당 자료 없음')}</td></tr>")
    if common:
        text.append(f"○ {_COMMON_GROUP}: " + ", ".join(article for article, _link in common))
        markup.append(
            f"<tr><td>{_COMMON_GROUP}</td><td>"
            + ", ".join(_link_html(article, link) for article, link in common)
            + "</td></tr>"
        )
    else:
        missing = "자료를 불러오지 못함" if common_rules_error else "자료 없음"
        text.append(f"○ {_COMMON_GROUP}: {missing}")
        markup.append(f"<tr><td>{_COMMON_GROUP}</td><td>{missing}</td></tr>")
    markup.append("</table>")
    reference = common_rules or case_rules
    notes = [LAW_DISCLAIMER]
    if reference is not None:
        notes.append(
            f"조문 원문은 {reference.source_name}에서 확인 "
            f"(자료 기준일 {reference.checked_on}, 팀 시연 자료 기준)"
        )
    if added_by_app:
        notes.append("절도 관련 조문은 시연 자료에 없어 앱에서 추가한 것으로, 원문 확인 필요")
    for note in notes:
        text.append(f"※ {note}")
        markup.append(f'<p class="note">※ {html.escape(note)}</p>')
    return "\n".join(text), "".join(markup)


def _add_to_group(
    groups: list[tuple[str, list[tuple[str, str]]]], law_name: str, article: str, link: str
) -> None:
    for name, articles in groups:
        if name == law_name:
            articles.append((article, link))
            return
    groups.append((law_name, [(article, link)]))


_SHORT_NAMES = {
    "검사와 사법경찰관의 상호협력과 일반적 수사준칙에 관한 규정": "수사준칙",
    "디지털 증거의 처리 등에 관한 규칙": "디지털 증거 처리 규칙",
}


def _short_law_name(name: str) -> str:
    return _SHORT_NAMES.get(name, name)


def _link_html(article: str, link: str) -> str:
    return f'<a href="{html.escape(link)}">{html.escape(article)}</a>'


def _open_items(
    cameras: Sequence[CameraSummaryInput], candidates: Sequence[_Candidate], several: bool
) -> str:
    needs_check = [c.where for c in candidates if c.judgement is ReviewJudgement.NEEDS_CHECK]
    unreviewed = sum(1 for c in candidates if c.judgement is ReviewJudgement.UNREVIEWED)
    lines = [f"□ 추가 확인: {', '.join(needs_check) if needs_check else '없음'}"]
    if unreviewed:
        lines.append(f"□ 미검토 후보: {unreviewed}건")
    for camera in cameras:
        job = camera.job
        prefix = f"{camera.camera}: " if several else ""
        if job is None:
            lines.append(f"□ {prefix}분석 결과 없음")
            continue
        if job.status is JobStatus.PARTIAL:
            lines.append(
                f"□ {prefix}일부만 분석됨 (사진 {job.analyzed_frame_count}/{job.requested_frame_count}장)"
            )
        if not job.segments:
            lines.append(f"□ {prefix}AI가 후보를 찾지 못함")
    lines.append("□ 유의 사항")
    lines.append(f"  - {_SAMPLING_NOTE}")
    lines.append(f"  - {_NO_JUDGEMENT_NOTE}")
    if several:
        lines.append(f"  - {_TIMELINE_NOTE}")
    return "\n".join(lines)


def _appendix(case: CaseRecord | None, cameras: Sequence[CameraSummaryInput]) -> str:
    lines = []
    for camera in cameras:
        job = camera.job
        name = camera.file_name or camera.camera
        if job is None:
            lines.append(f"○ {name}: 분석 결과 없음")
            continue
        if job.mode is AnalysisMode.SAMPLE:
            method = "시연용 (실제 AI 분석 아님)"
        elif job.mode is AnalysisMode.LOCAL:
            method = f"AI 분석 ({job.model_id})"
        else:
            method = f"외부 AI 분석 ({job.model_id})"
        scope = (
            f"{_time(job.range_start_sec)}~{_time(job.range_end_sec)}"
            if job.range_start_sec is not None and job.range_end_sec is not None
            else "미기록"
        )
        parts = [
            method,
            f"범위 {scope}",
            f"사진 {job.analyzed_frame_count}/{job.requested_frame_count}장",
            "일부 완료" if job.status is JobStatus.PARTIAL else "완료",
        ]
        # Review and processing times are kept for evaluation only, not in the report.
        session = camera.session
        if session is not None and session.completed_at:
            parts.append(
                f"검토 완료 {datetime.fromisoformat(session.completed_at):%Y. %m. %d. %H:%M}"
            )
        lines.append(f"○ {name}: " + " · ".join(parts))
        lines.append(f"  - 영상 ID {job.video_id} · 결과 ID {job.job_id}")
        if case is not None and job.case_revision is not None and job.case_revision != case.revision:
            lines.append(
                f"  - 분석 당시 사건 정보 버전 {job.case_revision}, 현재 버전 {case.revision}"
            )
    return "\n".join(lines)


def _time(seconds: float) -> str:
    return format_milliseconds(seconds_to_milliseconds(seconds))


# ----- HTML ---------------------------------------------------------------------

_LINE_KINDS = (("□", "sq"), ("○", "o"), ("-", "d"), ("※", "n"))


def body_html(text: str) -> str:
    """Plain section text as official-style lines (□ ○ - ※ with their indents)."""
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append('<div class="gap"></div>')
            continue
        kind = next((name for mark, name in _LINE_KINDS if stripped.startswith(mark)), "p")
        out.append(f'<div class="{kind}">{html.escape(stripped)}</div>')
    return "".join(out)


def meta_rows(summary: ResultSummary, author: str = "") -> list[tuple[tuple[str, str], ...]]:
    """The header table rows: two pairs a row, 검토 대상 on a row of its own."""
    items = list(summary.meta)
    items.insert(3, ("작성자", author.strip()))
    rows: list[tuple[tuple[str, str], ...]] = []
    pending: list[tuple[str, str]] = []
    for item in items:
        if item[0] == "검토 대상":
            if pending:
                rows.append(tuple(pending))
                pending = []
            rows.append((item,))
            continue
        pending.append(item)
        if len(pending) == 2:
            rows.append(tuple(pending))
            pending = []
    if pending:
        rows.append(tuple(pending))
    return rows


def render_html(
    summary: ResultSummary, bodies: Mapping[str, str] | None = None, *, author: str = ""
) -> str:
    """Self-contained A4-style HTML. All text, including edited bodies, is escaped."""
    bodies = bodies or {}
    parts = [
        "<!doctype html>",
        '<html lang="ko"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(summary.title)} · {html.escape(summary.file_stem)}</title>",
        f"<style>{_HTML_STYLE}</style></head><body><main>",
    ]
    if summary.origin_notice:
        parts.append(f'<p class="banner">{html.escape(summary.origin_notice)}</p>')
    parts.append(f"<h1>{html.escape(summary.title)}</h1>")
    table = []
    for row in meta_rows(summary, author):
        if len(row) == 1:
            key, value = row[0]
            table.append(
                f'<tr><td class="h">{html.escape(key)}</td>'
                f'<td colspan="3">{html.escape(value)}</td></tr>'
            )
        else:
            table.append(
                "<tr>"
                + "".join(
                    f'<td class="h">{html.escape(key)}</td><td>{html.escape(value)}</td>'
                    for key, value in row
                )
                + "</tr>"
            )
    parts.append(f'<table class="meta">{"".join(table)}</table>')
    for warning in summary.warnings:
        parts.append(f'<p class="warning">{html.escape(warning)}</p>')
    for section in summary.sections:
        if section.editable:
            content = body_html(bodies.get(section.key, section.body))
        elif section.html_body is not None:
            content = section.html_body
        else:
            content = body_html(section.body)
        css = ' class="appendix"' if section.key == "appendix" else ""
        parts.append(f"<section{css}><h2>{html.escape(section.title)}</h2>{content}</section>")
    parts.append(
        '<p class="foot">저장된 분석 결과와 검토 기록으로 자동 작성한 초안을 '
        "수사관이 확인·수정한 문서임</p>"
    )
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"


_HTML_STYLE = """
:root { color-scheme: light; }
body { margin: 0; background: #e9ecef; color: #111;
       font-family: "Pretendard GOV", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif;
       line-height: 1.7; }
main { max-width: 794px; margin: 24px auto; background: #fff; padding: 56px 64px 64px;
       box-shadow: 0 1px 4px rgba(0,0,0,.15); font-size: 14.5px; box-sizing: border-box; }
h1 { text-align: center; font-size: 24px; letter-spacing: 2px; margin: 0 0 18px; padding: 10px 0;
     border-top: 3px double #111; border-bottom: 3px double #111; }
h2 { font-size: 16px; margin: 20px 0 6px; }
table { width: 100%; border-collapse: collapse; }
table.meta { font-size: 13.5px; margin-bottom: 18px; }
table.meta td { border: 1px solid #888; padding: 5px 8px; }
table.meta td.h { background: #f2f2f2; width: 16%; text-align: center; font-weight: 700; }
table.grid { font-size: 13px; margin: 6px 0 4px; }
table.grid th { background: #f2f2f2; border: 1px solid #888; padding: 4px 6px; }
table.grid td { border: 1px solid #888; padding: 4px 6px; vertical-align: top; }
table.law td:first-child { white-space: nowrap; }
a { color: #063a74; }
.sq { margin: 4px 0 2px; }
.o { margin: 2px 0 2px 18px; }
.d { margin: 1px 0 1px 36px; }
.n, .note { margin: 2px 0 2px 18px; color: #444; font-size: 13.5px; }
.p { margin: 2px 0; }
.gap { height: 6px; }
section.appendix { margin-top: 26px; border-top: 1px solid #888; padding-top: 6px;
                   font-size: 12.5px; color: #333; }
section.appendix h2 { font-size: 13px; }
.banner { padding: 8px 10px; background: #fff3db; color: #8a5c00; border: 1px solid #ffe0a3;
          font-weight: 700; font-size: 13px; }
.warning { padding: 6px 10px; background: #fff6d8; color: #6e5200; border: 1px solid #d6b858;
           font-size: 13px; }
.foot { margin-top: 24px; font-size: 12px; color: #666; }
@media print { body { background: #fff; } main { box-shadow: none; margin: 0; }
               section { break-inside: avoid; } }
"""
