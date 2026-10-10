"""Rule-based result summary for handing a reviewed analysis to other people.

No model is called. Every sentence the app writes comes from the fixed templates below;
reviewer memos are quoted as the reviewer's own words. The templates never state that a
crime happened or what a person intended, and prior information is never presented as
something the video confirmed.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from video_agent.knowledge.common_rules import CommonRules
from video_agent.contracts import (
    AnalysisJob,
    AnalysisMode,
    CaseRecord,
    ClueSource,
    InformationCertainty,
    JobStatus,
    ResultOrigin,
    ReviewJudgement,
    ReviewSession,
)
from video_agent.video.files import format_milliseconds, seconds_to_milliseconds


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

LAW_DISCLAIMER = "법 적용 여부는 수사관이 판단하며, 이 요약은 판단하지 않습니다."
CASE_LAW_NOT_CONNECTED = (
    "사건별 참고 법령: 아직 연결하지 않았습니다. 관련 자료를 확정한 뒤 연결할 예정입니다."
)


@dataclass(frozen=True, slots=True)
class SummarySection:
    key: str
    title: str
    body: str
    editable: bool = True
    # Pre-escaped HTML for fixed sections that carry links; plain `body` stays authoritative.
    html_body: str | None = None


@dataclass(frozen=True, slots=True)
class ResultSummary:
    job_id: str
    origin: ResultOrigin
    origin_notice: str
    generated_at: str
    warnings: tuple[str, ...]
    sections: tuple[SummarySection, ...]

    def section(self, key: str) -> SummarySection:
        return next(item for item in self.sections if item.key == key)


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
    generated_at: datetime | None = None,
) -> ResultSummary:
    """Build the six-section draft from saved data only (unsaved drafts are excluded)."""
    if session is not None:
        session.validate_against(job)
    judgements = {
        index: _judgement(session, index) for index in range(len(job.segments))
    }
    warnings: list[str] = []
    if unsaved_count:
        warnings.append(f"저장하지 않은 기록 {unsaved_count}건은 요약에 포함되지 않았습니다.")
    if case_load_error:
        warnings.append(f"사건 정보를 불러오지 못했습니다: {case_load_error}")
    sections = (
        SummarySection("case", "① 사건 개요·사전 정보", _case_text(job, case)),
        SummarySection(
            "scope", "② 분석 범위·조건", _scope_text(job, video_file_name)
        ),
        SummarySection(
            "observations", "③ 주요 관찰", _observations_text(job, session, judgements)
        ),
        SummarySection(
            "opinions", "④ 수사관 검토 의견", _opinions_text(job, session, judgements)
        ),
        _law_section(common_rules, common_rules_error),
        SummarySection(
            "open_items", "⑥ 미확인·추가 확인 사항", _open_items_text(job, session, judgements)
        ),
    )
    moment = generated_at or datetime.now().astimezone()
    return ResultSummary(
        job_id=job.job_id,
        origin=job.origin,
        origin_notice=origin_notice(job),
        generated_at=moment.isoformat(timespec="seconds"),
        warnings=tuple(warnings),
        sections=sections,
    )


def origin_notice(job: AnalysisJob) -> str:
    if job.origin is ResultOrigin.SAMPLE:
        return "샘플 결과 — 실제 AI 분석 아님. 후보는 영상 내용을 보지 않고 만든 예시입니다."
    if job.origin is ResultOrigin.TEST_DOUBLE:
        return "테스트용 가짜 모델 응답 — 실제 모델 검증 아님."
    if job.origin is ResultOrigin.REAL_MODEL:
        return (
            f"실제 모델 응답 — {job.model_id}. "
            "후보 요약은 모델 출력이며, 판단과 메모는 수사관이 남긴 기록입니다."
        )
    return "결과 출처 확인 안 됨."


def render_html(summary: ResultSummary, bodies: Mapping[str, str] | None = None) -> str:
    """Self-contained HTML. All text, including edited bodies, is escaped."""
    bodies = bodies or {}
    banner_class = {
        ResultOrigin.SAMPLE: "sample",
        ResultOrigin.TEST_DOUBLE: "test",
        ResultOrigin.REAL_MODEL: "real",
    }.get(summary.origin, "none")
    parts = [
        "<!doctype html>",
        '<html lang="ko"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>검토 결과 요약 — {html.escape(summary.job_id)}</title>",
        f"<style>{_HTML_STYLE}</style></head><body><main>",
        "<h1>검토 결과 요약</h1>",
        f'<p class="banner {banner_class}">{html.escape(summary.origin_notice)}</p>',
        '<p class="meta">'
        f"결과 ID {html.escape(summary.job_id)} · 작성 "
        f"{datetime.fromisoformat(summary.generated_at):%Y-%m-%d %H:%M}<br>"
        "저장된 분석 결과와 검토 기록으로 자동 작성한 초안을 수사관이 확인·수정한 문서입니다. "
        "영상만으로 범죄 성립이나 사람의 의도를 판단하지 않습니다.</p>",
    ]
    for warning in summary.warnings:
        parts.append(f'<p class="warning">{html.escape(warning)}</p>')
    for section in summary.sections:
        if section.editable:
            content = html.escape(bodies.get(section.key, section.body))
        elif section.html_body is not None:
            content = section.html_body
        else:
            content = html.escape(section.body)
        parts.append(
            f"<section><h2>{html.escape(section.title)}</h2>"
            f'<div class="body">{content}</div></section>'
        )
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"


# ----- section text -------------------------------------------------------------


def _law_section(rules: CommonRules | None, error: str | None) -> SummarySection:
    heading = "영상 증거 처리 공통 규정 (사건과 관계없이 영상 자료를 다룰 때 참고):"
    text = [LAW_DISCLAIMER, CASE_LAW_NOT_CONNECTED, ""]
    markup = [html.escape(line) for line in text]
    if rules is None:
        missing = (
            "영상 증거 처리 공통 규정: 자료를 불러오지 못했습니다."
            if error
            else "영상 증거 처리 공통 규정: 자료 없음"
        )
        text.append(missing)
        markup.append(html.escape(missing))
    else:
        text.append(heading)
        markup.append(html.escape(heading))
        for rule in rules.rules:
            text.extend([f"- {rule.title}", f"  {rule.source}"])
            markup.extend(
                [
                    f'- <a href="{html.escape(rule.link)}">{html.escape(rule.title)}</a>',
                    f"  {html.escape(rule.source)}",
                ]
            )
        footer = (
            f"출처: {rules.source_name} (원문 확인일 {rules.checked_on}, 팀 시연 자료 기준). "
            "조문 본문은 링크에서 확인하세요."
        )
        text.extend(["", footer])
        markup.extend(["", html.escape(footer)])
    return SummarySection(
        "law",
        "⑤ 참고 법령",
        "\n".join(text),
        editable=False,
        html_body="\n".join(markup),
    )


def _judgement(session: ReviewSession | None, index: int) -> ReviewJudgement:
    review = session.review_for(index) if session is not None else None
    return review.judgement if review is not None else ReviewJudgement.UNREVIEWED


def _time(seconds: float) -> str:
    return format_milliseconds(seconds_to_milliseconds(seconds))


def _case_text(job: AnalysisJob, case: CaseRecord | None) -> str:
    lines: list[str] = []
    if case is not None:
        lines.append(f"사건명: {case.title}")
        lines.append(f"사건 유형: {case.case_type or '미입력'}")
        occurrence = case.occurrence_time_text or "미입력"
        lines.append(
            f"발생 시각: {occurrence} "
            f"({_CERTAINTY_LABELS[case.occurrence_time_certainty]})"
        )
        if job.case_revision is not None and job.case_revision != case.revision:
            lines.append(
                f"분석 당시 사건 버전 {job.case_revision}, 현재 저장본 버전 {case.revision}"
            )
    elif job.case_id:
        lines.append(f"사건 정보를 찾지 못했습니다 (사건 ID {job.case_id}).")
    else:
        lines.append("연결된 사건 정보가 없습니다.")
    lines.append("")
    lines.append("분석에 사용한 사전 정보 (사용자 제공 정보이며 영상으로 확인한 사실이 아님):")
    if job.analysis_clues:
        for clue in job.analysis_clues:
            lines.append(
                f"- {clue.text} (출처: {_SOURCE_LABELS[clue.source]} · "
                f"확실성: {_CERTAINTY_LABELS[clue.certainty]})"
            )
    else:
        lines.append("- 없음")
    return "\n".join(lines)


def _scope_text(job: AnalysisJob, video_file_name: str | None) -> str:
    if job.mode is AnalysisMode.SAMPLE:
        method = "샘플 (실제 AI 분석 아님)"
    elif job.mode is AnalysisMode.LOCAL:
        method = f"로컬 VLM · {job.model_id}"
    else:
        method = f"외부 API · {job.model_id}"
    if job.range_start_sec is not None and job.range_end_sec is not None:
        scope = f"{_time(job.range_start_sec)}–{_time(job.range_end_sec)} (원본 시작 기준)"
    else:
        scope = "미기록"
    status = "일부 완료" if job.status is JobStatus.PARTIAL else "완료"
    lines = [
        f"영상: {video_file_name or '파일명 미확인'} (영상 ID {job.video_id})",
        f"결과 ID: {job.job_id}",
        f"분석 방식: {method}",
        f"분석 목적: {job.purpose or '미기록'}",
        f"분석 범위: {scope}",
        f"분석 프레임: {job.analyzed_frame_count}/{job.requested_frame_count}",
        f"결과 상태: {status}",
        f"모델 후보 수: {len(job.segments)}개",
    ]
    return "\n".join(lines)


def _observations_text(
    job: AnalysisJob,
    session: ReviewSession | None,
    judgements: Mapping[int, ReviewJudgement],
) -> str:
    chosen = [
        index
        for index in sorted(
            judgements, key=lambda i: (job.segments[i].start_sec, job.segments[i].end_sec, i)
        )
        if judgements[index] in {ReviewJudgement.RELEVANT, ReviewJudgement.NEEDS_CHECK}
    ]
    if not chosen:
        return "관련 있음 또는 추가 확인으로 판단한 후보가 없습니다."
    blocks = []
    for index in chosen:
        segment = job.segments[index]
        lines = [
            f"후보 #{index + 1} [{JUDGEMENT_LABELS[judgements[index]]}] "
            f"확인 범위 {_time(segment.start_sec)}–{_time(segment.end_sec)}",
        ]
        if segment.source_timestamps_sec:
            observed = ", ".join(f"{value:.3f}초" for value in segment.source_timestamps_sec)
            lines.append(f"  관찰 프레임 시각: {observed}")
        lines.append(f"  모델 요약 (모델 출력): {segment.summary}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _opinions_text(
    job: AnalysisJob,
    session: ReviewSession | None,
    judgements: Mapping[int, ReviewJudgement],
) -> str:
    counts = {judgement: 0 for judgement in ReviewJudgement}
    for judgement in judgements.values():
        counts[judgement] += 1
    lines = [
        "판단 현황: "
        + " · ".join(f"{JUDGEMENT_LABELS[j]} {counts[j]}" for j in ReviewJudgement)
        + f" (전체 {len(job.segments)})"
    ]
    memos = []
    excluded = []
    for index in range(len(job.segments)):
        review = session.review_for(index) if session is not None else None
        memo = review.memo.strip() if review is not None else ""
        tags = f" [태그: {', '.join(review.tags)}]" if review is not None and review.tags else ""
        if judgements[index] is ReviewJudgement.EXCLUDED:
            excluded.append(f"- 후보 #{index + 1}: {memo or '사유 미기록'}{tags}")
        elif memo or tags:
            memos.append(
                f"- 후보 #{index + 1} [{JUDGEMENT_LABELS[judgements[index]]}]: "
                f"{memo or '메모 없음'}{tags}"
            )
    lines.append("")
    lines.append("수사관 메모:")
    lines.extend(memos or ["- 없음"])
    lines.append("")
    lines.append("제외한 후보와 사유:")
    lines.extend(excluded or ["- 없음"])
    return "\n".join(lines)


def _open_items_text(
    job: AnalysisJob,
    session: ReviewSession | None,
    judgements: Mapping[int, ReviewJudgement],
) -> str:
    needs_check = [
        f"#{index + 1}"
        for index, judgement in sorted(judgements.items())
        if judgement is ReviewJudgement.NEEDS_CHECK
    ]
    unreviewed = sum(
        1 for judgement in judgements.values() if judgement is ReviewJudgement.UNREVIEWED
    )
    lines = [
        f"추가 확인으로 표시한 후보: {', '.join(needs_check) if needs_check else '없음'}",
        f"미검토 후보: {unreviewed}개",
    ]
    if job.status is JobStatus.PARTIAL:
        lines.append(
            f"일부 배치만 분석됨: {job.analyzed_frame_count}/{job.requested_frame_count}프레임"
            + (f" · {job.error}" if job.error else "")
        )
    if not job.segments:
        lines.append("모델이 후보를 반환하지 않았습니다.")
    lines.append(
        "모델 후보는 분석한 프레임에서 고른 것이므로, 후보가 없는 구간에 해당 장면이 "
        "없다는 뜻은 아닙니다."
    )
    completed = session.completed_at if session is not None else None
    if completed:
        lines.append(f"분석 완료 기록: {datetime.fromisoformat(completed):%Y-%m-%d %H:%M}")
    else:
        lines.append("분석 완료 기록: 없음")
    return "\n".join(lines)


_HTML_STYLE = """
:root { color-scheme: light; }
body { margin: 0; background: #f3f6fa; color: #1e2a36;
       font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
       line-height: 1.6; }
main { max-width: 860px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { margin: 0 0 12px; font-size: 24px; color: #18324a; }
h2 { margin: 0 0 8px; font-size: 17px; color: #18324a; }
section { background: #fff; border: 1px solid #ccd7e2; border-radius: 8px;
          padding: 16px 18px; margin: 12px 0; }
.body { white-space: pre-wrap; font-size: 14px; }
.banner { padding: 10px 12px; border-radius: 6px; font-weight: 700; }
.banner.sample { background: #fff0d7; color: #813f00; border: 1px solid #e6a654; }
.banner.test { background: #f5e9ff; color: #5d2b7a; border: 1px solid #b98bd0; }
.banner.real { background: #e4f4eb; color: #125d35; border: 1px solid #75b991; }
.banner.none { background: #eef1f4; color: #46515a; border: 1px solid #adb7c0; }
.meta { font-size: 13px; color: #4a5a6a; }
.warning { padding: 8px 12px; border-radius: 6px; background: #fff6d8; color: #6e5200;
           border: 1px solid #d6b858; font-size: 14px; }
@media print { body { background: #fff; } section { break-inside: avoid; } }
"""
