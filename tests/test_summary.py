from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from video_agent.contracts import (
    AnalysisClue,
    AnalysisJob,
    AnalysisMode,
    CandidateReview,
    CaseRecord,
    FocusSituation,
    CaseVideoLink,
    ClueSource,
    InformationCertainty,
    JobStatus,
    ResultOrigin,
    ReviewJudgement,
    ReviewSession,
    Segment,
)
from video_agent.knowledge.case_rules import load_case_rules
from video_agent.knowledge.common_rules import load_common_rules
from video_agent.models.config import RuntimeInventory
from video_agent.review.summary import (
    CameraSummaryInput,
    build_case_summary,
    build_summary,
    render_html,
)
from video_agent.storage.json_store import CaseStore
from video_agent.storage.paths import AppPaths
from video_agent.ui.main_window import MainWindow
from video_agent.ui.summary_dialog import ResultSummaryDialog

# UI tests run without a model, so they use the demonstration (off for staff).
os.environ.setdefault("VIDEO_AGENT_ENABLE_SAMPLE", "1")

CLUE = AnalysisClue(
    clue_id="clue-1",
    text="신고자는 문 앞 상자가 사라졌다고 설명함",
    source=ClueSource.REPORT,
    certainty=InformationCertainty.ESTIMATED,
)


def segment(start: float, summary: str) -> Segment:
    return Segment(
        video_id="video-sum",
        start_sec=start,
        end_sec=start + 2,
        summary=summary,
        evidence=(f"{start + 1:.1f}초 프레임에서 관찰됨",),
        uncertainty="원본 확인 필요",
        source_frame_ids=(f"video-sum:{int((start + 1) * 1000)}",),
        source_timestamps_sec=(start + 1,),
    )


def job(origin: ResultOrigin = ResultOrigin.REAL_MODEL, **changes) -> AnalysisJob:
    real = origin is not ResultOrigin.SAMPLE
    base = AnalysisJob(
        job_id="local-sum",
        status=JobStatus.COMPLETED,
        progress=100,
        video_id="video-sum",
        mode=AnalysisMode.LOCAL if real else AnalysisMode.SAMPLE,
        model_id="qwen3-vl:8b-instruct" if real else None,
        is_sample=not real,
        origin=origin,
        purpose="사람이 문 앞 상자를 들어 옮기는 장면 후보 찾기",
        range_start_sec=0.0,
        range_end_sec=60.0,
        requested_frame_count=12,
        analyzed_frame_count=12,
        elapsed_seconds=110.7,
        case_id="case-sum",
        case_revision=1,
        analysis_clues=(CLUE,),
        segments=(
            segment(40.0, "손이 상자 가까이에 있음"),
            segment(5.0, "사람이 문 앞에 서 있음"),
            segment(20.0, "그림자가 상자 위를 지남"),
            segment(50.0, "상자가 화면 밖으로 이동한 것으로 보임"),
        ),
    )
    return replace(base, **changes)


def review(index: int, judgement: ReviewJudgement, memo: str = "", tags=()) -> CandidateReview:
    return CandidateReview(
        job_id="local-sum",
        segment_index=index,
        judgement=judgement,
        memo=memo,
        tags=tuple(tags),
        reviewed_at="2026-10-10T23:00:00+09:00",
        review_seconds=30.0,
    )


def session(*reviews: CandidateReview, completed_at: str | None = None) -> ReviewSession:
    return ReviewSession(
        review_id="review-sum",
        job_id="local-sum",
        video_id="video-sum",
        started_at="2026-10-10T22:50:00+09:00",
        reviews=reviews,
        completed_at=completed_at,
        total_review_seconds=192.0,
    )


CASE = CaseRecord(
    case_id="case-sum",
    revision=2,
    title="문 앞 택배 이동 확인",
    case_type="관리용 분류",
    occurrence_time_text="10월 10일 오후",
    occurrence_time_certainty=InformationCertainty.ESTIMATED,
    management_notes="담당자 연락처 010-0000-0000",
    clues=(CLUE,),
)

GENERATED = datetime(2026, 10, 10, 23, 30, tzinfo=timezone.utc)


def reviewed_summary(**kwargs):
    return build_summary(
        job(),
        session(
            review(0, ReviewJudgement.RELEVANT, "41초에 손이 상자에 닿음", ("손 접촉",)),
            review(2, ReviewJudgement.EXCLUDED, "그림자일 뿐 사람 없음"),
            review(3, ReviewJudgement.NEEDS_CHECK, "화면 밖 이동 여부 재확인 필요"),
            completed_at="2026-10-10T23:20:00+09:00",
        ),
        CASE,
        video_file_name="front_door.mp4",
        generated_at=GENERATED,
        **kwargs,
    )


class SummaryBuilderTests(unittest.TestCase):
    def test_report_has_a_title_and_header_table(self) -> None:
        summary = reviewed_summary()
        self.assertEqual(summary.title, "CCTV 영상 검토 결과 보고")
        self.assertEqual(
            summary.meta,
            (
                ("사건명", "문 앞 택배 이동 확인"),
                ("사건 유형", "관리용 분류"),
                ("작성일", "2026. 10. 10."),
                ("검토 대상", "CCTV 1대 (front_door.mp4)"),
            ),
        )
        self.assertEqual(
            [section.title for section in summary.sections],
            [
                "1. 개요",
                "2. 주요 관찰",
                "3. 수사관 검토 의견",
                "4. 사전 정보 (영상으로 확인하지 않은 정보)",
                "5. 참고 법령",
                "6. 추가 확인 및 유의 사항",
                "붙임. 분석 조건",
            ],
        )
        # Official short style: every generated line starts with □ ○ - or ※.
        for section in summary.sections:
            for line in section.body.splitlines():
                self.assertTrue(line.strip()[:1] in "□○-※", (section.key, line))

    def test_overview_states_purpose_method_and_counts(self) -> None:
        text = reviewed_summary().section("overview").body
        self.assertIn("□ 검토 목적: 사람이 문 앞 상자를 들어 옮기는 장면 후보 찾기", text)
        self.assertIn("□ 검토 방법: AI가 고른 후보 구간을 수사관이 원본 영상으로 직접 확인함", text)
        self.assertIn(
            "○ 검토 결과: 후보 4건 중 관련 있음 1건, 추가 확인 1건, 제외 1건, 미검토 1건", text
        )
        self.assertIn("○ 발생 시각(신고 기준): 10월 10일 오후 (추정)", text)
        self.assertNotIn("010-0000-0000", text)

        focused = replace(
            CASE,
            case_type="실종",
            occurrence_time_text="",
            occurrence_time_certainty=InformationCertainty.UNKNOWN,
            focus_situations=(FocusSituation.MOVEMENT, FocusSituation.STAY),
            focus_hints=("보라색 조끼",),
        )
        summary = build_summary(
            job(), session(), focused, video_file_name="front_door.mp4", generated_at=GENERATED
        )
        overview = summary.section("overview").body
        self.assertIn("○ 중점 관찰: 이동·동선, 체류·이탈", overview)
        self.assertNotIn("발생 시각", overview)  # not asked, so not listed when empty
        self.assertIn("○ 추가 탐색 힌트: 보라색 조끼 (사용자 입력)", summary.section("clues").body)

    def test_clues_are_marked_as_not_seen_in_the_video(self) -> None:
        text = reviewed_summary().section("clues").body
        self.assertIn("○ 신고자는 문 앞 상자가 사라졌다고 설명함 (신고·보고)", text)
        self.assertIn("※ 위 내용은 신고·진술 등에서 받은 정보로, 영상으로 확인한 사실이 아님", text)

    def test_appendix_uses_file_name_and_ids_only(self) -> None:
        text = reviewed_summary().section("appendix").body
        self.assertIn(
            "○ front_door.mp4: AI 분석 (qwen3-vl:8b-instruct) · 범위 00:00~01:00 · 사진 12/12장 · 완료",
            text,
        )
        self.assertIn("- 영상 ID video-sum · 결과 ID local-sum", text)
        self.assertIn("- 분석 당시 사건 정보 버전 1, 현재 버전 2", text)
        self.assertIn("검토 완료 2026. 10. 10. 23:20", text)

    def test_observations_table_has_only_relevant_and_needs_check_in_time_order(self) -> None:
        section = reviewed_summary().section("observations")
        self.assertFalse(section.editable)
        text = section.body
        self.assertIn(
            "○ front_door 00:40~00:42 [관련 있음] 손이 상자 가까이에 있음 "
            "(수사관 메모: 41초에 손이 상자에 닿음)",
            text,
        )
        self.assertIn("○ front_door 00:50~00:52 [추가 확인] 상자가 화면 밖으로 이동한 것으로 보임", text)
        self.assertLess(text.index("00:40"), text.index("00:50"))
        self.assertNotIn("그림자", text)
        self.assertIn("<th", section.html_body)
        self.assertIn("<td>관련 있음</td>", section.html_body)

    def test_opinions_list_memos_and_exclusion_reasons(self) -> None:
        text = reviewed_summary().section("opinions").body
        self.assertIn("□ front_door 00:40~00:42: 41초에 손이 상자에 닿음 [태그: 손 접촉]", text)
        self.assertIn("□ 제외 1건", text)
        self.assertIn("○ front_door 00:20~00:22: 그림자일 뿐 사람 없음", text)

    def test_law_section_follows_the_case_type_and_never_judges(self) -> None:
        rules = load_case_rules()
        common = load_common_rules()
        theft = build_summary(
            job(), session(), replace(CASE, case_type="절도"), video_file_name="a.mp4",
            common_rules=common, case_rules=rules, generated_at=GENERATED,
        ).section("law")
        self.assertFalse(theft.editable)
        self.assertIn("○ 형법: 제329조(절도), 제330조(야간주거침입절도), 제331조(특수절도), 제366조(재물손괴등)", theft.body)
        self.assertIn("앱에서 추가한 것으로, 원문 확인 필요", theft.body)
        self.assertIn("○ 영상 증거 처리 공통: 수사준칙 제3조", theft.body)
        self.assertIn("※ 법 적용 여부는 수사관이 판단하며, 이 보고는 판단하지 않음", theft.body)

        assault = build_summary(
            job(), session(), replace(CASE, case_type="폭행"), video_file_name="a.mp4",
            common_rules=common, case_rules=rules,
        ).section("law").body
        self.assertIn("제260조(폭행, 존속폭행)", assault)
        self.assertIn("○ 경찰수사규칙: 제79조(피해자 보호의 원칙) 제2항", assault)
        self.assertNotIn("제329조", assault)
        self.assertNotIn("원문 확인 필요", assault)

        missing_child = build_summary(
            job(), session(), replace(CASE, case_type="실종"), video_file_name="a.mp4", case_rules=rules
        ).section("law").body
        self.assertIn("제9조의2", missing_child)
        self.assertIn("실종아동등 및 가출인 업무처리 규칙", missing_child)

        other = reviewed_summary(common_rules=common, case_rules=rules).section("law").body
        self.assertIn("○ 사건 유형('관리용 분류')에 따른 법령: 해당 자료 없음 (공통 규정만 참고)", other)
        for body in (theft.body, assault, other):
            for phrase in ("해당한다", "해당합니다", "성립", "적용된다", "적용됩니다", "위반"):
                self.assertNotIn(phrase, body)

    def test_law_section_without_rules(self) -> None:
        missing = reviewed_summary(common_rules_error="파일 없음").section("law")
        self.assertIn("○ 영상 증거 처리 공통: 자료를 불러오지 못함", missing.body)
        absent = reviewed_summary().section("law")
        self.assertIn("○ 영상 증거 처리 공통: 자료 없음", absent.body)

    def test_open_items_and_no_review_time_metrics(self) -> None:
        summary = reviewed_summary()
        text = summary.section("open_items").body
        self.assertIn("□ 추가 확인: front_door 00:50~00:52", text)
        self.assertIn("□ 미검토 후보: 1건", text)
        self.assertIn("후보가 없는 구간에 해당 장면이 없다는 뜻이 아님", text)
        self.assertIn("영상만으로 범죄 성립이나 사람의 의도를 판단하지 않음", text)
        everything = "\n".join(section.body for section in summary.sections)
        self.assertNotIn("확인 시간", everything)
        self.assertNotIn("03:12", everything)
        self.assertNotIn("110.7", everything)
        self.assertNotIn("1분 51초", everything)

    def test_partial_and_sample_results_are_labelled(self) -> None:
        partial = build_summary(
            job(status=JobStatus.PARTIAL, progress=None, error="2번째 배치 시간 초과", analyzed_frame_count=8),
            None,
            None,
            video_file_name=None,
        )
        self.assertIn("□ 일부만 분석됨 (사진 8/12장)", partial.section("open_items").body)
        self.assertIn("○ 영상: AI 분석", partial.section("appendix").body)
        self.assertIn("· 일부 완료", partial.section("appendix").body)
        self.assertIn(("사건명", "미입력"), partial.meta)
        self.assertIn("□ 미검토 후보: 4건", partial.section("open_items").body)
        self.assertEqual(partial.origin_notice, "")  # a real result needs no line

        sample = build_summary(job(ResultOrigin.SAMPLE), None, None, video_file_name="a.mp4")
        self.assertIn("시연용 결과 (실제 AI 분석 아님)", sample.origin_notice)
        self.assertIn("시연용 예시 후보(실제 AI 분석 아님)", sample.section("overview").body)
        self.assertIn("○ a.mp4: 시연용 (실제 AI 분석 아님)", sample.section("appendix").body)

    def test_generated_text_contains_no_judgement_of_guilt_or_intent(self) -> None:
        summary = build_summary(job(), session(), CASE, video_file_name="a.mp4")
        generated = summary.origin_notice + "\n".join(s.body for s in summary.sections)
        for phrase in ("범인", "가해자", "범죄가 성립", "유죄", "고의", "의도적으로", "절도"):
            self.assertNotIn(phrase, generated)

    def test_html_links_laws_to_encoded_official_urls(self) -> None:
        page = render_html(
            build_summary(
                job(), session(), replace(CASE, case_type="절도"), video_file_name="a.mp4",
                common_rules=load_common_rules(), case_rules=load_case_rules(),
            )
        )
        self.assertIn(
            '<a href="https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B2%BD%EC%B0%B0%EC%88%98%EC%82%AC%EA%B7%9C%EC%B9%99/%EC%A0%9C2%EC%A1%B0">'
            "경찰수사규칙 제2조(인권 보호 및 적법절차의 준수) 제2항</a>",
            page,
        )
        self.assertIn('href="https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulId=48364&amp;efYd=0"', page)
        self.assertEqual(page.count("<a href="), 7 + 4)
        self.assertEqual(page.count('<a href="https://www.law.go.kr/'), 7 + 4)

    def test_unsaved_drafts_are_reported_not_included(self) -> None:
        summary = reviewed_summary(unsaved_count=2)
        self.assertIn("저장하지 않은 기록 2건은 보고에 포함되지 않았습니다.", summary.warnings)

    def test_session_from_another_result_is_rejected(self) -> None:
        foreign = ReviewSession(
            review_id="r", job_id="other", video_id="video-sum", started_at="2026-10-10T22:00:00+09:00"
        )
        with self.assertRaises(ValueError):
            build_summary(job(), foreign, None, video_file_name=None)

    def test_html_is_an_official_page_with_escaped_edited_text(self) -> None:
        summary = reviewed_summary()
        page = render_html(
            summary,
            {"opinions": "<script>alert(1)</script> 수정한 의견", "law": "바꾸면 안 됨"},
            author="홍길동",
        )
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; 수정한 의견", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn("바꾸면 안 됨", page)
        self.assertIn("<h1>CCTV 영상 검토 결과 보고</h1>", page)
        self.assertIn('<td class="h">작성자</td><td>홍길동</td>', page)
        self.assertIn('<td class="h">작성일</td><td>2026. 10. 10.</td>', page)
        self.assertIn('<div class="sq">□ 검토 목적:', page)
        self.assertIn('<section class="appendix"><h2>붙임. 분석 조건</h2>', page)
        self.assertNotIn('class="banner"', page)  # a real result: no line above the title
        self.assertNotIn("초안", page)
        self.assertIn("영상만으로 범죄 성립이나 사람의 의도를 판단하지 않음", page)


def other_camera_job(origin: ResultOrigin = ResultOrigin.REAL_MODEL) -> AnalysisJob:
    second = replace(
        job(origin),
        job_id="local-side",
        video_id="video-side",
        segments=(
            replace(segment(12.0, "사람이 계단 쪽으로 걸어감"), video_id="video-side",
                    source_frame_ids=("video-side:13000",)),
        ),
    )
    return second


def case_cameras(**side_changes) -> list[CameraSummaryInput]:
    front = CameraSummaryInput(
        video_id="video-sum",
        camera="문앞 CCTV",
        file_name="front_door.mp4",
        link=CaseVideoLink(
            video_id="video-sum",
            camera_location="문앞 CCTV",
            clock_offset_seconds=-3.5,
            clock_certainty=InformationCertainty.ESTIMATED,
        ),
        job=job(),
        session=session(
            review(0, ReviewJudgement.RELEVANT, "41초에 손이 상자에 닿음"),
            review(2, ReviewJudgement.EXCLUDED, "그림자일 뿐 사람 없음"),
        ),
    )
    side_job = side_changes.pop("job", other_camera_job())
    side = CameraSummaryInput(
        video_id="video-side",
        camera="복도 CCTV",
        file_name=None,
        link=CaseVideoLink(video_id="video-side", camera_location="복도 CCTV"),
        job=side_job,
        session=None,
        **side_changes,
    )
    empty = CameraSummaryInput(video_id="video-none", camera="주차장 CCTV", file_name="park.mp4")
    return [front, side, empty]


class CaseSummaryBuilderTests(unittest.TestCase):
    def build(self, cameras=None, **kwargs):
        return build_case_summary(
            CASE,
            cameras or case_cameras(),
            case_id="case-sum",
            generated_at=GENERATED,
            **kwargs,
        )

    def test_header_lists_every_camera_without_clock_differences(self) -> None:
        summary = self.build()
        self.assertIn(("검토 대상", "CCTV 3대 (front_door.mp4, 복도 CCTV, park.mp4)"), summary.meta)
        everything = "\n".join(section.body for section in summary.sections)
        self.assertNotIn("시각 오차", everything)
        # Shared clues are listed once, still marked as information not seen in the video.
        clues = summary.section("clues").body
        self.assertEqual(clues.count(CLUE.text), 1)
        self.assertIn("영상으로 확인한 사실이 아님", clues)

    def test_cameras_are_named_on_each_line(self) -> None:
        summary = self.build()
        observations = summary.section("observations").body
        self.assertIn("○ 문앞 CCTV 00:40~00:42 [관련 있음]", observations)
        self.assertNotIn("주차장 CCTV", observations)
        self.assertIn("※ 시각은 각 영상의 시작 기준이며, 카메라 사이의 시간 순서는 비교하지 않음", observations)
        self.assertIn(
            "○ 검토 결과: 후보 5건 중 관련 있음 1건, 추가 확인 0건, 제외 1건, 미검토 3건",
            summary.section("overview").body,
        )
        self.assertIn("○ 문앞 CCTV 00:20~00:22: 그림자일 뿐 사람 없음", summary.section("opinions").body)
        open_items = summary.section("open_items").body
        self.assertIn("□ 주차장 CCTV: 분석 결과 없음", open_items)
        self.assertIn("□ 미검토 후보: 3건", open_items)
        self.assertEqual(open_items.count("후보가 없는 구간에 해당 장면이"), 1)
        appendix = summary.section("appendix").body
        self.assertIn("○ front_door.mp4: AI 분석", appendix)
        self.assertIn("○ park.mp4: 분석 결과 없음", appendix)

    def test_no_corrected_or_cross_camera_times(self) -> None:
        everything = "\n".join(section.body for section in self.build().sections)
        self.assertNotIn("보정", everything)
        self.assertNotIn("00:36", everything)  # 40 s minus the 3.5 s offset

    def test_mixed_origins_are_named(self) -> None:
        summary = self.build(case_cameras(job=other_camera_job(ResultOrigin.SAMPLE)))
        self.assertIs(summary.origin, ResultOrigin.SAMPLE)
        self.assertIn("일부 영상은 시연용 결과 (실제 AI 분석 아님)", summary.origin_notice)
        self.assertIn("○ 복도 CCTV: 시연용 (실제 AI 분석 아님)", summary.section("appendix").body)

        same = self.build()
        self.assertIs(same.origin, ResultOrigin.REAL_MODEL)
        self.assertEqual(same.origin_notice, "")

    def test_warnings_name_the_camera(self) -> None:
        summary = self.build(case_cameras(unsaved_count=2, review_load_error="손상된 파일"))
        self.assertIn("복도 CCTV: 저장하지 않은 기록 2건은 보고에 포함되지 않았습니다.", summary.warnings)
        self.assertIn("복도 CCTV: 검토 기록을 읽지 못해 미검토로 표시했습니다 (손상된 파일)", summary.warnings)

    def test_needs_a_saved_result(self) -> None:
        with self.assertRaises(ValueError):
            build_case_summary(None, case_cameras()[2:], case_id="case-sum")

    def test_html_names_the_case(self) -> None:
        page = render_html(self.build())
        self.assertIn('<td class="h">검토 대상</td><td colspan="3">CCTV 3대', page)
        self.assertIn("<title>CCTV 영상 검토 결과 보고 · 사건_case-sum</title>", page)

    def test_case_text_contains_no_judgement_of_guilt_or_intent(self) -> None:
        summary = self.build()
        generated = summary.origin_notice + "\n".join(s.body for s in summary.sections)
        for phrase in ("범인", "가해자", "범죄가 성립", "유죄", "고의", "의도적으로", "절도"):
            self.assertNotIn(phrase, generated)


class SummaryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.dialog = ResultSummaryDialog(lambda: reviewed_summary(unsaved_count=1))

    def tearDown(self) -> None:
        self.dialog.close()
        self.temporary.cleanup()

    def test_dialog_law_section_has_clickable_links(self) -> None:
        dialog = ResultSummaryDialog(lambda: reviewed_summary(common_rules=load_common_rules()))
        try:
            label = dialog._section_bodies["law"]
            self.assertTrue(label.openExternalLinks())
            self.assertIn('<a href="https://www.law.go.kr/', label.text())
            self.assertIn("<table", label.text())
        finally:
            dialog.close()

    def test_dialog_shows_sections_warning_and_fixed_law_text(self) -> None:
        self.assertEqual(
            set(self.dialog.editors), {"overview", "opinions", "clues", "open_items", "appendix"}
        )
        self.assertIn("<td>관련 있음</td>", self.dialog._section_bodies["observations"].text())
        self.assertIn("저장하지 않은 기록 1건", self.dialog.warning_label.text())
        self.assertTrue(self.dialog.banner_label.isHidden())  # real result: no banner
        self.assertEqual(self.dialog.windowTitle(), "결과 보고")
        self.assertEqual(self.dialog.width(), 860)
        self.assertEqual(self.dialog.regenerate_button.text(), "보고 내용 다시 만들기")
        self.assertEqual(self.dialog.title_label.text(), "CCTV 영상 검토 결과 보고")
        self.assertEqual(self.dialog.author_edit.placeholderText(), "수사관 성명")
        law_height = self.dialog.editors["open_items"].height()
        self.dialog.editors["open_items"].setPlainText("\n".join(["줄"] * 12))
        self.assertGreater(self.dialog.editors["open_items"].height(), law_height)

    def test_logged_in_operator_is_the_default_report_author(self) -> None:
        dialog = ResultSummaryDialog(lambda: reviewed_summary(), author="김수사관")
        try:
            self.assertEqual(dialog.author_edit.text(), "김수사관")
        finally:
            dialog.close()

    def test_export_writes_edited_text(self) -> None:
        self.dialog.editors["opinions"].setPlainText("수사관이 고친 의견")
        self.dialog.author_edit.setText("홍길동")
        target = Path(self.temporary.name) / "요약"
        with patch(
            "video_agent.ui.summary_dialog.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ):
            path = self.dialog.export_html()

        self.assertEqual(path, target.with_name("요약.html"))
        page = path.read_text(encoding="utf-8")
        self.assertIn("수사관이 고친 의견", page)
        self.assertIn("<td>홍길동</td>", page)
        self.assertIn("내보냄: 요약.html", self.dialog.export_status_label.text())

    def test_export_failure_is_shown(self) -> None:
        blocked = Path(self.temporary.name) / "no-such-dir" / "요약.html"
        with patch(
            "video_agent.ui.summary_dialog.QFileDialog.getSaveFileName",
            return_value=(str(blocked), ""),
        ):
            self.assertIsNone(self.dialog.export_html())
        self.assertIn("내보내기 실패", self.dialog.export_status_label.text())

    def test_export_cancel_writes_nothing(self) -> None:
        with patch(
            "video_agent.ui.summary_dialog.QFileDialog.getSaveFileName", return_value=("", "")
        ):
            self.assertIsNone(self.dialog.export_html())
        self.assertEqual(self.dialog.export_status_label.text(), "")

    def test_single_scope_has_no_scope_buttons(self) -> None:
        self.assertEqual(self.dialog.scope_buttons, {})
        self.assertEqual(self.dialog.scope, "camera")

    def test_case_scope_opens_first_and_switches_with_confirmation(self) -> None:
        dialog = ResultSummaryDialog(
            lambda: reviewed_summary(),
            case_build=lambda: build_case_summary(
                CASE, case_cameras(), case_id="case-sum", generated_at=GENERATED
            ),
        )
        try:
            self.assertEqual(dialog.scope, "case")
            self.assertTrue(dialog.scope_buttons["case"].isChecked())
            dialog.show()
            QApplication.processEvents()
            case_button = dialog.scope_buttons["case"]
            camera_button = dialog.scope_buttons["camera"]
            self.assertEqual(case_button.geometry().right() + 1, camera_button.geometry().left())
            self.assertIn("복도 CCTV", dialog.editors["appendix"].toPlainText())

            dialog.editors["opinions"].setPlainText("고친 문구")
            with patch.object(dialog, "_confirm_regenerate", return_value=False):
                dialog.scope_buttons["camera"].click()
            self.assertEqual(dialog.scope, "case")
            self.assertTrue(dialog.scope_buttons["case"].isChecked())
            self.assertEqual(dialog.editors["opinions"].toPlainText(), "고친 문구")

            with patch.object(dialog, "_confirm_regenerate", return_value=True):
                dialog.scope_buttons["camera"].click()
            self.assertEqual(dialog.scope, "camera")
            self.assertNotIn("복도 CCTV", dialog.editors["appendix"].toPlainText())

            dialog.scope_buttons["case"].click()  # nothing edited: no question
            self.assertEqual(dialog.scope, "case")
            with patch(
                "video_agent.ui.summary_dialog.QFileDialog.getSaveFileName", return_value=("", "")
            ) as chooser:
                dialog.export_html()
            self.assertTrue(chooser.call_args.args[2].endswith("검토결과보고_사건_case-sum.html"))
        finally:
            dialog.close()

    def test_regenerate_asks_before_discarding_edits(self) -> None:
        original = self.dialog.editors["opinions"].toPlainText()
        self.dialog.editors["opinions"].setPlainText("고친 문구")
        with patch.object(self.dialog, "_confirm_regenerate", return_value=False):
            self.dialog.regenerate()
        self.assertEqual(self.dialog.editors["opinions"].toPlainText(), "고친 문구")
        with patch.object(self.dialog, "_confirm_regenerate", return_value=True):
            self.dialog.regenerate()
        self.assertEqual(self.dialog.editors["opinions"].toPlainText(), original)


class MainWindowSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.env = patch.dict(
            os.environ,
            {"VIDEO_AGENT_DATA_DIR": str(root / "data"), "VIDEO_AGENT_CACHE_DIR": str(root / "cache")},
            clear=False,
        )
        self.env.start()
        self.runtime = patch(
            "video_agent.ui.main_window.RuntimeInventory.detect",
            return_value=RuntimeInventory(None, (), None, None),
        )
        self.runtime.start()
        self.window = MainWindow()

    def tearDown(self) -> None:
        dialog = getattr(self.window, "_summary_dialog", None)
        if dialog is not None:
            dialog.close()
        with patch.object(self.window, "_confirm_discard_unsaved_reviews", return_value=True):
            self.window.close()
        self.app.processEvents()
        self.runtime.stop()
        self.env.stop()
        self.temporary.cleanup()

    def test_summary_button_opens_dialog_with_saved_reviews_and_case(self) -> None:
        CaseStore(AppPaths.default()).save(CASE)
        self.window._video_path = Path(self.temporary.name) / "Users" / "secret" / "front_door.mp4"
        self.window._video_id = "video-sum"
        self.window._display_segments(job())
        panel = self.window._review_panel
        self.assertTrue(panel.summary_button.isEnabled())
        panel.candidate_list.setCurrentRow(2)  # candidate #1 in time order
        panel.judgement_buttons[ReviewJudgement.RELEVANT].setChecked(True)
        panel.memo_edit.setPlainText("저장한 메모")
        panel.save_current()
        panel.candidate_list.setCurrentRow(0)
        panel.memo_edit.setPlainText("저장 안 한 메모")

        panel.summary_button.click()

        dialog = self.window._summary_dialog
        self.assertIsInstance(dialog, ResultSummaryDialog)
        self.assertIn(("사건명", "문 앞 택배 이동 확인"), dialog._summary.meta)
        self.assertIn("front_door.mp4", dialog.editors["appendix"].toPlainText())
        self.assertNotIn("secret", dialog.editors["appendix"].toPlainText())
        self.assertIn("저장한 메모", dialog.editors["opinions"].toPlainText())
        self.assertNotIn("저장 안 한 메모", dialog.editors["opinions"].toPlainText())
        self.assertIn("저장하지 않은 기록 1건", dialog.warning_label.text())
        self.assertIn("디지털 증거 처리 규칙", dialog._section_bodies["law"].text())

    def test_summary_button_disabled_without_candidates(self) -> None:
        self.assertFalse(self.window._review_panel.summary_button.isEnabled())
        self.window._display_segments(job(segments=()))
        self.assertFalse(self.window._review_panel.summary_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
