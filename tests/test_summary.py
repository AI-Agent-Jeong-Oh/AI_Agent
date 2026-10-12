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
    def test_case_section_marks_clues_as_user_information(self) -> None:
        text = reviewed_summary().section("case").body
        self.assertIn("사건명: 문 앞 택배 이동 확인", text)
        self.assertIn("발생 시각: 10월 10일 오후 (추정)", text)
        self.assertIn("분석 당시 사건 버전 1, 현재 저장본 버전 2", text)
        self.assertIn("사용자 제공 정보이며 영상으로 확인한 사실이 아님", text)
        # Whatever an older file recorded, every clue reads as not checked against the video.
        self.assertIn(
            "신고자는 문 앞 상자가 사라졌다고 설명함 (출처: 신고·보고 · 확인 상태: 미확인(영상 확인 전))", text
        )
        self.assertNotIn("010-0000-0000", text)

    def test_case_section_shows_type_focus_and_hints_without_judgement(self) -> None:
        plain = reviewed_summary().section("case").body
        self.assertIn("중점 관찰 항목: 선택 없음", plain)
        self.assertNotIn("추가 탐색 힌트", plain)

        focused = replace(
            CASE,
            case_type="실종",
            occurrence_time_text="",
            occurrence_time_certainty=InformationCertainty.UNKNOWN,
            focus_situations=(FocusSituation.MOVEMENT, FocusSituation.STAY),
            focus_hints=("보라색 조끼",),
        )
        text = build_summary(
            job(), session(), focused, video_file_name="front_door.mp4", generated_at=GENERATED
        ).section("case").body
        self.assertIn("사건 유형: 실종", text)
        self.assertNotIn("발생 시각", text)  # not asked, so not listed when empty
        self.assertIn("중점 관찰 항목: 이동·동선, 체류·이탈", text)
        self.assertIn("범죄 여부를 판단한 것이 아님", text)
        self.assertIn("추가 탐색 힌트: 보라색 조끼 (사용자 입력이며 영상으로 확인한 사실이 아님)", text)

    def test_scope_section_uses_file_name_and_video_id_only(self) -> None:
        text = reviewed_summary().section("scope").body
        self.assertIn("영상: front_door.mp4 (영상 ID video-sum)", text)
        self.assertIn("분석 방식: 이 컴퓨터에서 AI 분석 (사용 모델 qwen3-vl:8b-instruct)", text)
        self.assertIn("분석 범위: 00:00–01:00", text)
        self.assertIn("결과 상태: 완료", text)
        self.assertNotIn("/", text.replace("12/12", ""))

    def test_observations_include_only_relevant_and_needs_check_in_time_order(self) -> None:
        text = reviewed_summary().section("observations").body
        self.assertIn("후보 #1 [관련 있음] 확인 범위 00:40–00:42", text)
        self.assertIn("후보 #4 [추가 확인] 확인 범위 00:50–00:52", text)
        self.assertLess(text.index("후보 #1"), text.index("후보 #4"))
        self.assertNotIn("후보 #2", text)
        self.assertNotIn("후보 #3", text)
        self.assertIn("관찰 프레임 시각: 41.000초", text)
        self.assertIn("모델 요약 (모델 출력): 손이 상자 가까이에 있음", text)

    def test_opinions_list_memos_and_exclusion_reasons(self) -> None:
        text = reviewed_summary().section("opinions").body
        self.assertIn("관련 있음 1 · 추가 확인 1 · 제외 1", text)
        self.assertIn("미검토 1", text)
        self.assertIn("- 후보 #1 [관련 있음]: 41초에 손이 상자에 닿음 [태그: 손 접촉]", text)
        self.assertIn("- 후보 #3: 그림자일 뿐 사람 없음", text)

    def test_law_section_lists_common_rules_without_judging(self) -> None:
        section = reviewed_summary(common_rules=load_common_rules()).section("law")
        self.assertFalse(section.editable)
        self.assertIn("법 적용 여부는 수사관이 판단하며, 이 요약은 판단하지 않습니다.", section.body)
        self.assertIn("사건별 참고 법령: 아직 연결하지 않았습니다", section.body)
        self.assertIn(
            "- 개인정보 보호법 제3조(개인정보 보호 원칙) 제1항·제2항\n  법률 제21445호 (시행 2026. 9. 11.)",
            section.body,
        )
        self.assertIn("원문 확인일 2026-10-07, 팀 시연 자료 기준", section.body)
        self.assertEqual(section.body.count("\n- "), 7)
        for phrase in ("해당한다", "해당합니다", "성립", "적용된다", "적용됩니다", "위반"):
            self.assertNotIn(phrase, section.body)

    def test_law_section_without_rules(self) -> None:
        missing = reviewed_summary(common_rules_error="파일 없음").section("law")
        self.assertIn("공통 규정: 자료를 불러오지 못했습니다.", missing.body)
        self.assertIn("사건별 참고 법령: 아직 연결하지 않았습니다", missing.body)
        absent = reviewed_summary().section("law")
        self.assertIn("공통 규정: 자료 없음", absent.body)

    def test_open_items_and_no_review_time_metrics(self) -> None:
        summary = reviewed_summary()
        text = summary.section("open_items").body
        self.assertIn("추가 확인으로 표시한 후보: #4", text)
        self.assertIn("미검토 후보: 1개", text)
        self.assertIn("분석 완료 기록: 2026-10-10 23:20", text)
        everything = "\n".join(section.body for section in summary.sections)
        self.assertNotIn("확인 시간", everything)
        self.assertNotIn("03:12", everything)
        self.assertNotIn("110.7", everything)

    def test_partial_and_sample_results_are_labelled(self) -> None:
        partial = build_summary(
            job(status=JobStatus.PARTIAL, progress=None, error="2번째 배치 시간 초과", analyzed_frame_count=8),
            None,
            None,
            video_file_name=None,
        )
        self.assertIn("결과 상태: 일부 완료", partial.section("scope").body)
        self.assertIn("일부 배치만 분석됨: 8/12프레임 · 2번째 배치 시간 초과", partial.section("open_items").body)
        self.assertIn("영상: 파일명 미확인", partial.section("scope").body)
        self.assertIn("사건 정보를 찾지 못했습니다", partial.section("case").body)
        self.assertIn("미검토 후보: 4개", partial.section("open_items").body)

        sample = build_summary(job(ResultOrigin.SAMPLE), None, None, video_file_name="a.mp4")
        self.assertIn("시연용 결과 (실제 AI 분석 아님)", sample.origin_notice)
        self.assertIn("분석 방식: 시연용 (실제 AI 분석 아님)", sample.section("scope").body)

    def test_generated_text_contains_no_judgement_of_guilt_or_intent(self) -> None:
        summary = build_summary(job(), session(), CASE, video_file_name="a.mp4")
        generated = summary.origin_notice + "\n".join(s.body for s in summary.sections)
        for phrase in ("범인", "가해자", "범죄가 성립", "유죄", "고의", "의도적으로", "절도"):
            self.assertNotIn(phrase, generated)

    def test_html_links_common_rules_to_encoded_official_urls(self) -> None:
        page = render_html(reviewed_summary(common_rules=load_common_rules()))
        self.assertIn(
            '<a href="https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EA%B2%BD%EC%B0%B0%EC%88%98%EC%82%AC%EA%B7%9C%EC%B9%99/%EC%A0%9C2%EC%A1%B0">'
            "경찰수사규칙 제2조(인권 보호 및 적법절차의 준수) 제2항</a>",
            page,
        )
        self.assertIn('href="https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulId=48364&amp;efYd=0"', page)
        self.assertEqual(page.count("<a href="), 7)
        self.assertEqual(page.count('<a href="https://www.law.go.kr/'), 7)

    def test_unsaved_drafts_are_reported_not_included(self) -> None:
        summary = reviewed_summary(unsaved_count=2)
        self.assertIn("저장하지 않은 기록 2건은 요약에 포함되지 않았습니다.", summary.warnings)

    def test_session_from_another_result_is_rejected(self) -> None:
        foreign = ReviewSession(
            review_id="r", job_id="other", video_id="video-sum", started_at="2026-10-10T22:00:00+09:00"
        )
        with self.assertRaises(ValueError):
            build_summary(job(), foreign, None, video_file_name=None)

    def test_html_escapes_text_and_uses_edited_bodies(self) -> None:
        summary = reviewed_summary()
        page = render_html(
            summary,
            {"opinions": "<script>alert(1)</script> 수정한 의견", "law": "바꾸면 안 됨"},
        )
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; 수정한 의견", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn("바꾸면 안 됨", page)
        self.assertIn("사건별 참고 법령: 아직 연결하지 않았습니다", page)
        self.assertIn('class="banner real"', page)
        self.assertIn("후보 #1 [관련 있음]", page)
        self.assertIn("영상만으로 범죄 성립이나 사람의 의도를 판단하지 않습니다", page)
        self.assertIn("작성 2026-10-10 23:30", page)


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

    def test_case_section_lists_cameras_without_clock_differences(self) -> None:
        text = self.build().section("case").body
        self.assertIn("사건명: 문 앞 택배 이동 확인", text)
        self.assertIn("CCTV 영상 3대:", text)
        self.assertIn(
            "- 문앞 CCTV: front_door.mp4 (영상 ID video-sum)", text
        )
        self.assertNotIn("시각 오차", text)
        self.assertIn("- 복도 CCTV: 파일명 미확인 (영상 ID video-side)", text)
        self.assertIn("카메라 사이의 시간 순서는 비교하지 않았습니다", text)
        # Shared clues are listed once, still marked as user information.
        self.assertEqual(text.count(CLUE.text), 1)
        self.assertIn("영상으로 확인한 사실이 아님", text)

    def test_sections_are_grouped_by_camera(self) -> None:
        summary = self.build()
        scope = summary.section("scope").body
        self.assertIn("[문앞 CCTV]\n  영상: front_door.mp4 (영상 ID video-sum)", scope)
        self.assertIn("[주차장 CCTV]\n  분석 결과 없음", scope)
        observations = summary.section("observations").body
        self.assertIn("[문앞 CCTV]\n  후보 #1 [관련 있음] 확인 범위 00:40–00:42", observations)
        self.assertIn("[복도 CCTV]\n  해당 후보 없음", observations)
        self.assertNotIn("주차장 CCTV", observations)
        opinions = summary.section("opinions").body
        self.assertIn("사건 전체 판단 현황: 미검토 3 · 관련 있음 1 · 추가 확인 0 · 제외 1 (전체 5)", opinions)
        self.assertIn("  - 후보 #3: 그림자일 뿐 사람 없음", opinions)
        open_items = summary.section("open_items").body
        self.assertIn("[복도 CCTV]\n  추가 확인으로 표시한 후보: 없음\n  미검토 후보: 1개", open_items)
        self.assertIn("분석 결과가 없는 카메라: 주차장 CCTV", open_items)
        self.assertEqual(open_items.count("후보가 없는 구간에 해당 장면이"), 1)

    def test_no_corrected_or_cross_camera_times(self) -> None:
        everything = "\n".join(section.body for section in self.build().sections)
        self.assertNotIn("보정", everything)
        self.assertNotIn("00:36", everything)  # 40 s minus the 3.5 s offset

    def test_mixed_origins_are_named_per_camera(self) -> None:
        summary = self.build(case_cameras(job=other_camera_job(ResultOrigin.SAMPLE)))
        self.assertIs(summary.origin, ResultOrigin.SAMPLE)
        self.assertIn("카메라마다 결과 출처가 다릅니다", summary.origin_notice)
        self.assertIn("문앞 CCTV: AI 분석 결과(사용 모델 qwen3-vl:8b-instruct)", summary.origin_notice)
        self.assertIn("복도 CCTV: 시연용 결과(실제 AI 분석 아님)", summary.origin_notice)
        self.assertIn("결과 출처: 시연용 결과(실제 AI 분석 아님)", summary.section("scope").body)

        same = self.build()
        self.assertIs(same.origin, ResultOrigin.REAL_MODEL)
        self.assertIn("AI 분석 결과 (사용 모델 qwen3-vl:8b-instruct)", same.origin_notice)

    def test_warnings_name_the_camera(self) -> None:
        summary = self.build(case_cameras(unsaved_count=2, review_load_error="손상된 파일"))
        self.assertIn("복도 CCTV: 저장하지 않은 기록 2건은 요약에 포함되지 않았습니다.", summary.warnings)
        self.assertIn("복도 CCTV: 검토 기록을 읽지 못해 미검토로 표시했습니다 (손상된 파일)", summary.warnings)

    def test_needs_a_saved_result(self) -> None:
        with self.assertRaises(ValueError):
            build_case_summary(None, case_cameras()[2:], case_id="case-sum")

    def test_html_names_the_case_and_camera_count(self) -> None:
        page = render_html(self.build())
        self.assertIn("사건 ID case-sum · CCTV 3대 · 작성 2026-10-10 23:30", page)
        self.assertIn("<title>검토 결과 요약 · 사건_case-sum</title>", page)
        self.assertIn('class="banner real"', page)

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
            self.assertIn("<br>", label.text())
        finally:
            dialog.close()

    def test_dialog_shows_sections_warning_and_fixed_law_text(self) -> None:
        self.assertEqual(
            set(self.dialog.editors), {"case", "scope", "observations", "opinions", "open_items"}
        )
        self.assertIn("후보 #1 [관련 있음]", self.dialog.editors["observations"].toPlainText())
        self.assertIn("저장하지 않은 기록 1건", self.dialog.warning_label.text())
        self.assertIn("AI 분석 결과", self.dialog.banner_label.text())
        law_height = self.dialog.editors["open_items"].height()
        self.dialog.editors["open_items"].setPlainText("\n".join(["줄"] * 12))
        self.assertGreater(self.dialog.editors["open_items"].height(), law_height)

    def test_export_writes_edited_text(self) -> None:
        self.dialog.editors["opinions"].setPlainText("수사관이 고친 의견")
        target = Path(self.temporary.name) / "요약"
        with patch(
            "video_agent.ui.summary_dialog.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ):
            path = self.dialog.export_html()

        self.assertEqual(path, target.with_name("요약.html"))
        page = path.read_text(encoding="utf-8")
        self.assertIn("수사관이 고친 의견", page)
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
            self.assertIn("[복도 CCTV]", dialog.editors["scope"].toPlainText())

            dialog.editors["opinions"].setPlainText("고친 문구")
            with patch.object(dialog, "_confirm_regenerate", return_value=False):
                dialog.scope_buttons["camera"].click()
            self.assertEqual(dialog.scope, "case")
            self.assertTrue(dialog.scope_buttons["case"].isChecked())
            self.assertEqual(dialog.editors["opinions"].toPlainText(), "고친 문구")

            with patch.object(dialog, "_confirm_regenerate", return_value=True):
                dialog.scope_buttons["camera"].click()
            self.assertEqual(dialog.scope, "camera")
            self.assertNotIn("[복도 CCTV]", dialog.editors["scope"].toPlainText())

            dialog.scope_buttons["case"].click()  # nothing edited: no question
            self.assertEqual(dialog.scope, "case")
            with patch(
                "video_agent.ui.summary_dialog.QFileDialog.getSaveFileName", return_value=("", "")
            ) as chooser:
                dialog.export_html()
            self.assertTrue(chooser.call_args.args[2].endswith("검토요약_사건_case-sum.html"))
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
        self.assertIn("문 앞 택배 이동 확인", dialog.editors["case"].toPlainText())
        self.assertIn("front_door.mp4", dialog.editors["scope"].toPlainText())
        self.assertNotIn("secret", dialog.editors["scope"].toPlainText())
        self.assertIn("저장한 메모", dialog.editors["opinions"].toPlainText())
        self.assertNotIn("저장 안 한 메모", dialog.editors["opinions"].toPlainText())
        self.assertIn("저장하지 않은 기록 1건", dialog.warning_label.text())
        self.assertIn("디지털 증거의 처리 등에 관한 규칙", dialog._section_bodies["law"].text())

    def test_summary_button_disabled_without_candidates(self) -> None:
        self.assertFalse(self.window._review_panel.summary_button.isEnabled())
        self.window._display_segments(job(segments=()))
        self.assertFalse(self.window._review_panel.summary_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
