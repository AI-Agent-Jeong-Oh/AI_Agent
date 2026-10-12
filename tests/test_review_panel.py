from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QLabel

from video_agent.contracts import (
    AnalysisJob,
    AnalysisMode,
    CandidateReview,
    JobStatus,
    ResultOrigin,
    ReviewJudgement,
    ReviewSession,
    Segment,
)
from video_agent.models.config import RuntimeInventory
from video_agent.storage.json_store import ResultStore, ReviewStore
from video_agent.storage.paths import AppPaths
from video_agent.ui.candidate_delegate import ROW_ROLE
from video_agent.ui.main_window import MainWindow
from video_agent.ui.review_panel import (
    CandidateReviewPanel,
    ReviewRecord,
    SEGMENT_INDEX_ROLE,
    parse_tags,
)


def segment(start: float, end: float, summary: str) -> Segment:
    middle = (start + end) / 2
    return Segment(
        video_id="video-review",
        start_sec=start,
        end_sec=end,
        summary=summary,
        evidence=(f"{middle:.1f}초 프레임에서 관찰됨",),
        uncertainty="원본 확인 필요",
        source_frame_ids=(f"video-review:{int(middle * 1000)}",),
        source_timestamps_sec=(middle,),
    )


def job(origin: ResultOrigin = ResultOrigin.TEST_DOUBLE) -> AnalysisJob:
    return AnalysisJob(
        job_id="job-review",
        status=JobStatus.COMPLETED,
        progress=100,
        video_id="video-review",
        mode=AnalysisMode.SAMPLE if origin is ResultOrigin.SAMPLE else AnalysisMode.LOCAL,
        model_id=None if origin is ResultOrigin.SAMPLE else "vision-model",
        is_sample=origin is ResultOrigin.SAMPLE,
        origin=origin,
        purpose="문 앞 상자를 옮기는 장면 후보 찾기",
        segments=(
            segment(40.0, 42.0, "세 번째 시각의 후보"),
            segment(5.0, 7.0, "첫 번째 시각의 후보"),
            segment(20.0, 22.0, "두 번째 시각의 후보"),
        ),
    )


class StepClock:
    """Test clock advanced by hand so time-based behaviour runs without waiting."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class CandidateReviewPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = CandidateReviewPanel(lambda seg, purpose: f"상세 {seg.summary} / {purpose}")
        self.panel.set_job(job())

    def select(self, index: int) -> None:
        for row in range(self.panel.candidate_list.count()):
            if self.panel.candidate_list.item(row).data(SEGMENT_INDEX_ROLE) == index:
                self.panel.candidate_list.setCurrentRow(row)
                return
        self.fail(f"candidate {index} not visible")

    def visible_indexes(self) -> list[int]:
        return [
            self.panel.candidate_list.item(row).data(SEGMENT_INDEX_ROLE)
            for row in range(self.panel.candidate_list.count())
        ]

    def test_default_sort_is_time_and_model_order_is_available(self) -> None:
        self.assertEqual(self.visible_indexes(), [1, 2, 0])
        self.assertTrue(self.panel.sort_actions["time"].isChecked())
        self.panel.sort_actions["model"].trigger()
        self.assertEqual(self.visible_indexes(), [0, 1, 2])
        self.assertTrue(self.panel.sort_actions["model"].isChecked())

    def test_tags_are_folded_unless_used(self) -> None:
        self.select(0)
        self.assertTrue(self.panel.tags_edit.isHidden())
        self.panel.tags_toggle.click()
        self.assertFalse(self.panel.tags_edit.isHidden())
        self.panel.tags_toggle.click()
        self.panel.tags_edit.setText("손 접촉")
        self.panel.save_current()
        self.select(2)
        self.select(0)
        self.assertTrue(self.panel.tags_toggle.isChecked())
        self.assertFalse(self.panel.tags_edit.isHidden())

    def test_one_blue_button_moves_from_review_to_finish_to_summary(self) -> None:
        panel = self.panel

        def blue() -> list:
            buttons = (panel.save_button, panel.complete_button, panel.summary_button)
            return [button for button in buttons if button.property("variant") == "primary"]

        self.assertEqual(blue(), [])
        self.select(0)
        panel.judgement_buttons[ReviewJudgement.RELEVANT].setChecked(True)
        # A judgement saves itself, so there is no save button to press.
        self.assertFalse(panel.has_unsaved_changes())
        self.assertTrue(panel.save_button.isHidden())
        self.assertEqual(blue(), [])  # two candidates are still unreviewed
        for index in (1, 2):
            self.select(index)
            panel.judgement_buttons[ReviewJudgement.EXCLUDED].setChecked(True)
        self.assertEqual(blue(), [panel.complete_button])
        # Before finishing only [분석 완료] shows; the summary comes after it.
        self.assertTrue(panel.summary_button.isHidden())
        self.assertTrue(panel.completion_label.isHidden())
        with patch.object(panel, "_confirm_completion", return_value=True):
            panel.complete_review()
        self.assertEqual(blue(), [panel.summary_button])
        self.assertFalse(panel.summary_button.isHidden())
        self.assertFalse(panel.completion_label.isHidden())

    def test_items_start_unreviewed_and_show_badge(self) -> None:
        item = self.panel.candidate_list.item(0)
        text = item.text()
        self.assertIn("미검토", text)
        self.assertNotIn("[", text)  # no bracketed state words
        self.assertIn("후보 #2", text)
        self.assertIn("00:05–00:07", text)
        row = item.data(ROW_ROLE)
        self.assertEqual((row.number, row.time_text, row.length_text), (2, "00:05–00:07", "2초"))
        self.assertIs(row.judgement, ReviewJudgement.UNREVIEWED)
        self.assertFalse(row.unsaved)

    def test_list_keys_set_the_judgement_and_play(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        played = []
        self.panel.play_toggle_requested.connect(lambda: played.append(True))
        self.select(1)
        self.panel.candidate_list.setFocus()
        QTest.keyClick(self.panel.candidate_list, Qt.Key.Key_2)
        self.assertTrue(self.panel.judgement_buttons[ReviewJudgement.NEEDS_CHECK].isChecked())
        QTest.keyClick(self.panel.candidate_list, Qt.Key.Key_0)
        self.assertTrue(self.panel.judgement_buttons[ReviewJudgement.UNREVIEWED].isChecked())
        QTest.keyClick(self.panel.candidate_list, Qt.Key.Key_Space)
        self.assertEqual(played, [True])
        # Typing in the memo keeps its digits.
        self.panel.memo_edit.setFocus()
        QTest.keyClicks(self.panel.memo_edit, "12")
        self.assertEqual(self.panel.memo_edit.toPlainText(), "12")
        self.assertTrue(self.panel.judgement_buttons[ReviewJudgement.UNREVIEWED].isChecked())

    def test_length_text(self) -> None:
        from video_agent.ui.review_panel import length_text

        self.assertEqual([length_text(v) for v in (2, 59.6, 60, 65)], ["2초", "1분", "1분", "1분 5초"])

    def test_selecting_candidate_shows_detail_and_enables_editor(self) -> None:
        self.assertFalse(self.panel.record_group.isEnabled())
        self.select(2)
        self.assertTrue(self.panel.record_group.isEnabled())
        self.assertIn("후보 #3", self.panel.detail_view.toPlainText())
        self.assertIn("두 번째 시각의 후보", self.panel.detail_view.toPlainText())
        self.assertTrue(self.panel.judgement_buttons[ReviewJudgement.UNREVIEWED].isChecked())

    def test_record_survives_switching_candidates_and_save(self) -> None:
        self.select(1)
        self.panel.judgement_buttons[ReviewJudgement.RELEVANT].setChecked(True)
        self.panel.memo_edit.setPlainText("6초에 손이 상자에 닿음")
        self.panel.tags_edit.setText("손 접촉, 재확인, 손 접촉")
        self.assertTrue(self.panel.has_unsaved_changes())  # memo and tags wait for a pause
        self.panel.bookmark_check.setChecked(True)  # a bookmark saves the whole record at once
        self.assertFalse(self.panel.has_unsaved_changes())

        self.select(0)
        self.assertEqual(self.panel.memo_edit.toPlainText(), "")
        self.select(1)

        self.assertTrue(self.panel.judgement_buttons[ReviewJudgement.RELEVANT].isChecked())
        self.assertEqual(self.panel.memo_edit.toPlainText(), "6초에 손이 상자에 닿음")
        self.assertEqual(self.panel.tags_edit.text(), "손 접촉, 재확인")
        self.assertTrue(self.panel.bookmark_check.isChecked())
        self.assertEqual(
            self.panel.saved_record_for(1),
            ReviewRecord(ReviewJudgement.RELEVANT, "6초에 손이 상자에 닿음", ("손 접촉", "재확인"), True),
        )

    def test_typed_memo_saves_after_a_pause_or_when_moving_on(self) -> None:
        self.select(0)
        self.panel.judgement_buttons[ReviewJudgement.EXCLUDED].setChecked(True)
        self.assertEqual(self.panel.saved_record_for(0).judgement, ReviewJudgement.EXCLUDED)
        self.panel.memo_edit.setPlainText("상자 아님")
        # While typing the row says so; the timer then saves it.
        self.assertEqual(self.panel.unsaved_indexes(), (0,))
        self.assertTrue(self.panel.candidate_list.currentItem().data(ROW_ROLE).unsaved)
        self.assertTrue(self.panel._autosave_timer.isActive())
        self.panel._autosave_timer.timeout.emit()
        self.assertEqual(self.panel.saved_record_for(0).memo, "상자 아님")
        self.assertFalse(self.panel.has_unsaved_changes())

        self.panel.memo_edit.setPlainText("상자 아님, 다른 사람")
        self.select(2)  # moving to another candidate saves what was typed
        self.assertEqual(self.panel.saved_record_for(0).memo, "상자 아님, 다른 사람")
        self.assertFalse(self.panel.has_unsaved_changes())
        self.select(0)
        self.assertEqual(self.panel.memo_edit.toPlainText(), "상자 아님, 다른 사람")

    def test_editing_back_to_saved_value_clears_draft(self) -> None:
        self.select(0)
        self.panel.memo_edit.setPlainText("임시")
        self.panel.memo_edit.setPlainText("")
        self.assertFalse(self.panel.has_unsaved_changes())

    def test_filters_by_judgement_and_bookmark(self) -> None:
        self.select(0)
        self.panel.judgement_buttons[ReviewJudgement.NEEDS_CHECK].setChecked(True)
        self.panel.save_current()
        self.select(2)
        self.panel.bookmark_check.setChecked(True)
        self.panel.save_current()

        actions = self.panel.filter_actions
        actions[ReviewJudgement.NEEDS_CHECK.value].trigger()
        self.assertEqual(self.visible_indexes(), [0])
        actions["bookmarked"].trigger()
        self.assertEqual(self.visible_indexes(), [2])
        actions[ReviewJudgement.UNREVIEWED.value].trigger()
        self.assertEqual(self.visible_indexes(), [1, 2])
        self.assertEqual(actions[ReviewJudgement.UNREVIEWED.value].text(), "미검토 2")
        self.assertEqual(actions[ReviewJudgement.NEEDS_CHECK.value].text(), "추가 확인 1")
        self.assertEqual(actions["bookmarked"].text(), "북마크 1")
        self.assertEqual(actions["all"].text(), "전체 3")
        # One "보기" button names the shown set; progress counts saved judgements.
        self.assertEqual(self.panel.view_button.text(), "보기: 미검토 2")
        self.assertEqual(self.panel.progress_label.text(), "확인 1/3 · 남은 후보 2")

    def test_view_menu_holds_filters_and_sort_in_one_button(self) -> None:
        menu = self.panel.view_button.menu()
        texts = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(texts, ["전체 3", "미검토 3", "관련 있음 0", "추가 확인 0", "제외 0", "북마크 0", "시간순", "모델 순서"])
        self.assertEqual(self.panel.view_button.text(), "보기: 전체 3")
        self.assertEqual(self.panel.progress_label.text(), "확인 0/3 · 남은 후보 3")

    def test_filter_hiding_selection_clears_editor(self) -> None:
        self.select(0)
        self.panel.filter_actions[ReviewJudgement.RELEVANT.value].trigger()
        self.assertEqual(self.panel.candidate_list.count(), 0)
        self.assertFalse(self.panel.record_group.isEnabled())

    def test_playback_buttons_request_padded_range(self) -> None:
        requested: list[tuple[float, float]] = []
        self.panel.playback_range_requested.connect(lambda s, e: requested.append((s, e)))
        self.select(1)
        for button in self.panel.playback_buttons:
            button.click()
        self.assertEqual(requested, [(5.0, 7.0), (0.0, 37.0), (0.0, 127.0)])
        # The range last played stays marked.
        self.assertTrue(self.panel.playback_buttons[2].isChecked())
        self.assertFalse(self.panel.playback_buttons[0].isChecked())

    def test_origin_label_distinguishes_sample_and_real_model(self) -> None:
        self.panel.set_job(job(ResultOrigin.SAMPLE))
        self.assertIn("시연용 결과 (실제 AI 분석 아님)", self.panel.origin_label.text())
        self.panel.set_job(job(ResultOrigin.REAL_MODEL))
        self.assertIn("AI 분석 결과 (수사관 확인 전)", self.panel.origin_label.text())

    def test_new_job_resets_records(self) -> None:
        self.select(0)
        self.panel.memo_edit.setPlainText("이전 결과 메모")
        self.panel.set_job(job())
        self.assertFalse(self.panel.has_unsaved_changes())
        self.assertEqual(self.panel.record_for(0), ReviewRecord())

    def test_empty_result_shows_message_without_editor(self) -> None:
        empty = AnalysisJob(
            job_id="job-empty",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-review",
            origin=ResultOrigin.SAMPLE,
        )
        self.panel.set_job(empty)
        self.assertIn("후보를 찾지 못했습니다", self.panel.candidate_list.item(0).text())
        self.assertFalse(self.panel.record_group.isEnabled())

    def test_parse_tags_trims_and_deduplicates(self) -> None:
        self.assertEqual(parse_tags(" a, ,b,a "), ("a", "b"))


class CandidateReviewPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.clock = StepClock()
        self.panel = CandidateReviewPanel(lambda seg, purpose: seg.summary, clock=self.clock)
        self.written: list[ReviewSession] = []

    def persist(self, session: ReviewSession) -> Path:
        self.written.append(session)
        return Path("/data/reviews") / f"{session.job_id}.json"

    def edit(self, row: int, judgement: ReviewJudgement, memo: str) -> None:
        self.panel.candidate_list.setCurrentRow(row)
        self.panel.judgement_buttons[judgement].setChecked(True)
        self.panel.memo_edit.setPlainText(memo)

    def test_save_passes_only_saved_records_to_persist(self) -> None:
        self.panel.set_job(job(), persist=self.persist)
        self.assertIn("아직 파일에 저장한 기록 없음", self.panel.save_status_label.text())
        self.edit(0, ReviewJudgement.RELEVANT, "저장할 메모")
        self.panel.save_current()
        saved_so_far = len(self.written)
        self.panel.candidate_list.setCurrentRow(1)
        self.panel.memo_edit.setPlainText("아직 입력 중인 메모")  # waits for a pause

        session = self.written[-1]
        self.assertEqual(len(self.written), saved_so_far)
        self.assertEqual([r.segment_index for r in session.reviews], [1])
        self.assertEqual(session.reviews[0].memo, "저장할 메모")
        self.assertIsNotNone(session.reviews[0].reviewed_at)
        self.assertEqual(session.reviews[0].review_seconds, 0.0)
        self.assertRegex(self.panel.save_status_label.text(), r"^저장됨 \d\d:\d\d$")
        self.assertIn("reviews/job-review.json", self.panel.save_status_label.toolTip())

    def test_failed_save_keeps_draft_and_shows_error(self) -> None:
        def failing(_session: ReviewSession) -> Path:
            raise OSError(28, "No space left on device")

        self.panel.set_job(job(), persist=failing)
        self.edit(0, ReviewJudgement.RELEVANT, "실패해도 남아야 함")

        self.assertFalse(self.panel.save_current())

        self.assertIn("저장 실패", self.panel.save_status_label.text())
        self.assertIn("저장 공간이 부족합니다", self.panel.save_status_label.text())
        self.assertIn("No space left on device", self.panel.save_status_label.toolTip())
        self.assertEqual(self.panel.unsaved_indexes(), (1,))
        self.assertEqual(self.panel.saved_record_for(1), ReviewRecord())
        self.assertEqual(self.panel.memo_edit.toPlainText(), "실패해도 남아야 함")
        self.assertTrue(self.panel.candidate_list.currentItem().data(ROW_ROLE).unsaved)
        self.assertTrue(self.panel.save_button.isEnabled())

    def test_invalid_record_is_reported_as_failure(self) -> None:
        self.panel.set_job(job(), persist=self.persist)
        self.edit(0, ReviewJudgement.RELEVANT, "가" * 4001)
        written = len(self.written)  # the judgement alone was saved

        self.assertFalse(self.panel.save_current())

        self.assertEqual(len(self.written), written)
        self.assertIn("저장 실패", self.panel.save_status_label.text())
        self.assertIn("메모 4000자", self.panel.save_status_label.text())
        # A save that did not go through shows the buttons to retry or undo.
        self.assertFalse(self.panel.save_button.isHidden())
        self.assertEqual(self.panel.save_button.text(), "다시 저장")
        self.assertFalse(self.panel.revert_button.isHidden())

    def test_loaded_session_restores_records(self) -> None:
        stored = ReviewSession(
            review_id="review-1",
            job_id="job-review",
            video_id="video-review",
            started_at="2026-10-10T22:00:00+09:00",
            reviews=(
                CandidateReview(
                    job_id="job-review",
                    segment_index=2,
                    judgement=ReviewJudgement.NEEDS_CHECK,
                    memo="불러온 메모",
                    tags=("재확인",),
                    bookmarked=True,
                    reviewed_at="2026-10-10T22:01:00+09:00",
                ),
            ),
        )

        self.panel.set_job(job(), session=stored, persist=self.persist)
        self.panel.candidate_list.setCurrentRow(1)

        self.assertTrue(self.panel.judgement_buttons[ReviewJudgement.NEEDS_CHECK].isChecked())
        self.assertEqual(self.panel.memo_edit.toPlainText(), "불러온 메모")
        self.assertEqual(self.panel.tags_edit.text(), "재확인")
        self.assertTrue(self.panel.bookmark_check.isChecked())
        self.assertIn("1건을 불러왔습니다", self.panel.save_status_label.text())
        self.assertFalse(self.panel.has_unsaved_changes())

        self.edit(0, ReviewJudgement.RELEVANT, "새 메모")
        self.panel.save_current()
        session = self.written[-1]
        self.assertEqual(session.review_id, "review-1")
        self.assertEqual(session.started_at, "2026-10-10T22:00:00+09:00")
        earlier = session.review_for(2)
        self.assertEqual(earlier.reviewed_at, "2026-10-10T22:01:00+09:00")
        self.assertEqual(earlier.memo, "불러온 메모")

    def test_load_error_blocks_saving(self) -> None:
        self.panel.set_job(job(), persist=self.persist, load_error=ValueError("unknown fields"))
        self.edit(0, ReviewJudgement.RELEVANT, "메모")

        self.assertFalse(self.panel.save_button.isEnabled())
        self.assertFalse(self.panel.save_current())
        self.assertEqual(self.written, [])
        self.assertIn("저장을 막았습니다", self.panel.save_status_label.text())
        self.assertIn("형식이 맞지 않음", self.panel.save_status_label.text())
        self.assertIn("unknown fields", self.panel.save_status_label.toolTip())

    def test_unsaved_row_is_hidden_when_nothing_is_pending(self) -> None:
        self.panel.set_job(job(), persist=self.persist)
        self.panel.show()
        self.panel.candidate_list.setCurrentRow(0)
        self.assertFalse(self.panel.unsaved_label.isVisible())
        self.panel.memo_edit.setPlainText("임시")
        # Waiting for the automatic save is not a warning.
        self.assertFalse(self.panel.unsaved_label.isVisible())
        self.panel.memo_edit.setPlainText("가" * 4001)
        self.assertFalse(self.panel.save_current())  # fails: too long
        self.assertTrue(self.panel.unsaved_label.isVisible())
        self.panel.memo_edit.setPlainText("임시")
        self.panel.save_current()
        self.assertFalse(self.panel.unsaved_label.isVisible())


class ReviewTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.clock = StepClock()
        self.panel = CandidateReviewPanel(lambda seg, purpose: seg.summary, clock=self.clock)
        self.written: list[ReviewSession] = []
        self.timed_job = replace(job(), elapsed_seconds=110.7)

    def persist(self, session: ReviewSession) -> Path:
        self.written.append(session)
        return Path("/data/reviews") / f"{session.job_id}.json"

    def test_total_and_per_candidate_time_are_counted_separately(self) -> None:
        self.panel.set_job(self.timed_job, persist=self.persist)
        self.clock.now += 10
        self.panel.candidate_list.setCurrentRow(0)  # candidate #2
        self.clock.now += 30
        self.panel.candidate_list.setCurrentRow(1)  # candidate #3
        self.clock.now += 20

        self.assertAlmostEqual(self.panel.total_review_seconds(), 60)
        self.assertAlmostEqual(self.panel.segment_review_seconds(1), 30)
        self.assertAlmostEqual(self.panel.segment_review_seconds(2), 20)

    def test_time_stops_while_app_is_inactive(self) -> None:
        self.panel.set_job(self.timed_job, persist=self.persist)
        self.clock.now += 5
        self.panel.set_app_active(False)
        self.clock.now += 600
        self.assertAlmostEqual(self.panel.total_review_seconds(), 5)
        self.panel.set_app_active(True)
        self.clock.now += 5

        self.assertAlmostEqual(self.panel.total_review_seconds(), 10)

    def test_review_and_ai_time_are_not_shown_on_screen(self) -> None:
        stored = ReviewSession(
            review_id="review-1",
            job_id="job-review",
            video_id="video-review",
            started_at="2026-10-10T22:00:00+09:00",
            completed_at="2026-10-10T23:50:00+09:00",
            total_review_seconds=192.0,
        )
        self.panel.set_job(self.timed_job, session=stored, persist=self.persist)
        self.panel.candidate_list.setCurrentRow(0)
        self.clock.now += 75

        texts = " ".join(label.text() for label in self.panel.findChildren(QLabel))
        for hidden in ("사람 확인 0", "AI 처리", "03:12", "04:27", "110.7", "01:15"):
            self.assertNotIn(hidden, texts)
        self.assertAlmostEqual(self.panel.total_review_seconds(), 267)

    def test_saved_time_continues_after_reopen(self) -> None:
        self.panel.set_job(self.timed_job, persist=self.persist)
        self.panel.candidate_list.setCurrentRow(0)
        self.clock.now += 40
        self.panel.judgement_buttons[ReviewJudgement.RELEVANT].setChecked(True)  # saves at once
        stored = self.written[-1]
        self.assertAlmostEqual(stored.total_review_seconds, 40)
        self.assertAlmostEqual(stored.review_for(1).review_seconds, 40)

        reopened = CandidateReviewPanel(lambda seg, purpose: seg.summary, clock=self.clock)
        reopened.set_job(self.timed_job, session=stored, persist=self.persist)
        reopened.candidate_list.setCurrentRow(0)
        self.clock.now += 15

        self.assertAlmostEqual(reopened.total_review_seconds(), 55)
        self.assertAlmostEqual(reopened.segment_review_seconds(1), 55)

    def test_complete_without_open_items_records_time(self) -> None:
        self.panel.set_job(self.timed_job, persist=self.persist)
        for row in range(3):
            self.panel.candidate_list.setCurrentRow(row)
            self.panel.judgement_buttons[ReviewJudgement.EXCLUDED].setChecked(True)
            self.panel.save_current()
        self.clock.now += 90

        with patch.object(self.panel, "_confirm_completion") as confirm:
            self.assertTrue(self.panel.complete_review())

        confirm.assert_not_called()
        session = self.written[-1]
        self.assertIsNotNone(session.completed_at)
        self.assertAlmostEqual(session.total_review_seconds, 90)
        self.assertRegex(self.panel.completion_label.text(), r"^완료 \d\d-\d\d \d\d:\d\d$")
        self.assertIn("완료 기록", self.panel.save_status_label.text())

    def test_complete_warns_about_unreviewed_and_unsaved(self) -> None:
        self.panel.set_job(self.timed_job, persist=self.persist)
        self.panel.candidate_list.setCurrentRow(1)
        self.panel.memo_edit.setPlainText("저장 안 함")

        with patch.object(self.panel, "_confirm_completion", return_value=False) as confirm:
            self.assertFalse(self.panel.complete_review())

        message = confirm.call_args.args[0]
        self.assertIn("미검토 후보 3개", message)
        self.assertIn("저장하지 않은 기록 1개(후보 #3)", message)
        self.assertEqual(self.written, [])
        self.assertEqual(self.panel.completion_label.text(), "완료 기록 없음")

        with patch.object(self.panel, "_confirm_completion", return_value=True):
            self.assertTrue(self.panel.complete_review())
        self.assertEqual(self.written[-1].reviews, ())
        self.assertEqual(self.panel.unsaved_indexes(), (2,))

    def test_editing_after_completion_is_allowed(self) -> None:
        self.panel.set_job(self.timed_job, persist=self.persist)
        with patch.object(self.panel, "_confirm_completion", return_value=True):
            self.panel.complete_review()
        completed_at = self.written[-1].completed_at

        self.panel.candidate_list.setCurrentRow(0)
        self.panel.memo_edit.setPlainText("완료 후 수정")
        self.assertTrue(self.panel.save_current())

        self.assertEqual(self.written[-1].completed_at, completed_at)
        self.assertEqual(self.written[-1].review_for(1).memo, "완료 후 수정")

    def test_completion_is_blocked_with_unreadable_review_file(self) -> None:
        self.panel.set_job(self.timed_job, persist=self.persist, load_error=ValueError("x"))
        self.assertFalse(self.panel.complete_button.isEnabled())
        self.assertFalse(self.panel.complete_review())
        self.assertEqual(self.written, [])

    def test_time_is_not_written_without_a_button(self) -> None:
        self.panel.set_job(self.timed_job, persist=self.persist)
        self.panel.candidate_list.setCurrentRow(0)
        self.clock.now += 300
        self.panel.set_job(job(), persist=self.persist)
        self.assertEqual(self.written, [])

    def test_loaded_completion_is_shown(self) -> None:
        stored = ReviewSession(
            review_id="review-1",
            job_id="job-review",
            video_id="video-review",
            started_at="2026-10-10T22:00:00+09:00",
            completed_at="2026-10-10T23:50:00+09:00",
            total_review_seconds=192.0,
        )
        self.panel.set_job(self.timed_job, session=stored, persist=self.persist)
        self.assertEqual(self.panel.completion_label.text(), "완료 10-10 23:50")
        self.assertAlmostEqual(self.panel.total_review_seconds(), 192)


class MainWindowReviewIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.env = patch.dict(
            os.environ,
            {
                "VIDEO_AGENT_DATA_DIR": str(root / "data"),
                "VIDEO_AGENT_CACHE_DIR": str(root / "cache"),
            },
            clear=False,
        )
        self.env.start()
        self.runtime = patch(
            "video_agent.ui.main_window.RuntimeInventory.detect",
            return_value=RuntimeInventory(None, (), None, None),
        )
        self.runtime.start()
        self.window = MainWindow()
        self.window._display_segments(job())
        self.panel = self.window._review_panel
        # The job's video file is open in the player, as after choosing it in the CCTV list.
        self.window._video_id = "video-review"
        self.window._video_path = root / "video.mp4"

    def tearDown(self) -> None:
        with patch.object(self.window, "_confirm_discard_unsaved_reviews", return_value=True):
            self.window.close()
        self.app.processEvents()
        self.runtime.stop()
        self.env.stop()
        self.temporary.cleanup()

    def test_playback_needs_the_candidates_video_file_open(self) -> None:
        self.window._player = MagicMock()
        self.window._video_id = "another-video"

        self.window._play_review_range(0.0, 5.0)

        self.window._player.play.assert_not_called()
        self.assertIn("영상 파일이 열려 있지 않아", self.window.statusBar().currentMessage())

    def test_start_button_returns_to_the_played_range_or_the_video_start(self) -> None:
        self.window._duration_ms = 60_000
        self.window._player = MagicMock()
        self.window._set_player_controls_enabled(True)  # as once a video is loaded
        self.window._play_review_range(10.0, 20.0)
        self.window._on_position_changed(20_000)  # the range ended and paused
        self.window._player.setPosition.reset_mock()

        self.window._start_button.click()
        self.window._player.setPosition.assert_called_once_with(10_000)
        self.assertEqual(self.window._playback_stop_ms, 20_000)

        self.window._slider_pressed()  # moving the bar leaves the range
        self.window._slider_released()
        self.window._player.setPosition.reset_mock()
        self.window._start_button.click()
        self.window._player.setPosition.assert_called_once_with(0)

    def test_speed_choice_sets_the_player_rate(self) -> None:
        combo = self.window._speed_combo
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], ["0.5×", "1×", "1.5×", "2×"])
        self.assertEqual(combo.currentText(), "1×")
        self.window._player = MagicMock()
        combo.setCurrentIndex(combo.findText("2×"))
        self.window._player.setPlaybackRate.assert_called_with(2.0)
        combo.setCurrentIndex(combo.findText("0.5×"))
        self.window._player.setPlaybackRate.assert_called_with(0.5)

    def test_play_button_keeps_its_width_when_playing(self) -> None:
        button = self.window._play_button
        width = button.width()
        self.window._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(button.text(), "일시정지")
        self.assertEqual(button.width(), width)
        self.assertEqual(button.minimumWidth(), button.maximumWidth())

    def test_playback_range_is_clamped_to_video_and_stops_at_end(self) -> None:
        self.window._duration_ms = 30_000
        self.window._player = MagicMock()

        self.window._play_review_range(0.0, 127.0)

        self.window._player.setPosition.assert_called_once_with(0)
        self.window._player.play.assert_called_once()
        self.assertEqual(self.window._playback_stop_ms, 30_000)
        self.window._on_position_changed(29_000)
        self.window._player.pause.assert_not_called()
        self.window._on_position_changed(30_000)
        self.window._player.pause.assert_called_once()
        self.assertIsNone(self.window._playback_stop_ms)

    def test_candidate_click_keeps_one_second_context_seek(self) -> None:
        self.window._duration_ms = 60_000
        self.window._player = MagicMock()
        self.window._playback_stop_ms = 10_000
        item = self.panel.candidate_list.item(0)

        self.window._seek_to_segment(item)

        self.window._player.setPosition.assert_called_once_with(4_000)
        self.assertIsNone(self.window._playback_stop_ms)

    def test_close_asks_when_review_records_are_unsaved(self) -> None:
        self.panel.candidate_list.setCurrentRow(0)
        self.panel.memo_edit.setPlainText("저장 안 한 메모")
        event = MagicMock()

        with patch.object(
            self.window, "_confirm_discard_unsaved_reviews", return_value=False
        ) as confirm:
            self.window.closeEvent(event)

        confirm.assert_called_once()
        event.ignore.assert_called_once()
        event.accept.assert_not_called()

    def test_saved_review_is_written_to_file_and_restored(self) -> None:
        paths = AppPaths.default()
        result_path = ResultStore(paths).save(job())
        result_before = result_path.read_bytes()
        self.panel.candidate_list.setCurrentRow(0)
        self.panel.judgement_buttons[ReviewJudgement.RELEVANT].setChecked(True)
        self.panel.memo_edit.setPlainText("파일에 저장")
        self.panel.tags_edit.setText("손 접촉")
        self.panel.bookmark_check.setChecked(True)  # saves the record, memo included

        self.assertFalse(self.panel.has_unsaved_changes())

        stored = ReviewStore(paths).load("job-review")
        self.assertEqual(stored.review_for(1).memo, "파일에 저장")
        self.assertEqual(result_path.read_bytes(), result_before)

        self.window._review_panel.clear()
        self.window._display_segments(job())
        self.panel.candidate_list.setCurrentRow(0)
        self.assertEqual(self.panel.memo_edit.toPlainText(), "파일에 저장")
        self.assertEqual(self.panel.tags_edit.text(), "손 접촉")
        self.assertTrue(self.panel.bookmark_check.isChecked())
        self.assertTrue(self.panel.judgement_buttons[ReviewJudgement.RELEVANT].isChecked())

    def test_corrupt_review_file_is_not_overwritten(self) -> None:
        reviews_dir = AppPaths.default().reviews_dir
        reviews_dir.mkdir(parents=True, exist_ok=True)
        corrupt = reviews_dir / "job-review.json"
        corrupt.write_text("{not json", encoding="utf-8")

        self.window._display_segments(job())
        self.panel.candidate_list.setCurrentRow(0)
        self.panel.memo_edit.setPlainText("덮어쓰면 안 됨")

        self.assertFalse(self.panel.save_current())
        self.assertEqual(corrupt.read_text(encoding="utf-8"), "{not json")
        self.assertIn("저장을 막았습니다", self.panel.save_status_label.text())
        self.assertIn("파일 형식이 손상됨", self.panel.save_status_label.text())

    def test_review_for_other_result_is_not_loaded(self) -> None:
        ReviewStore(AppPaths.default()).save(
            ReviewSession(
                review_id="review-x",
                job_id="job-review",
                video_id="other-video",
                started_at="2026-10-10T22:00:00+09:00",
            )
        )

        self.window._display_segments(job())

        self.assertIn("저장을 막았습니다", self.panel.save_status_label.text())

    def test_saved_result_reopens_with_reviews_after_restart(self) -> None:
        paths = AppPaths.default()
        ResultStore(paths).save(job(ResultOrigin.SAMPLE))
        self.window._video_id = "video-review"
        self.window._refresh_saved_results()
        self.window._open_saved_result()
        self.panel.candidate_list.setCurrentRow(0)
        self.panel.judgement_buttons[ReviewJudgement.NEEDS_CHECK].setChecked(True)
        self.panel.memo_edit.setPlainText("재시작 후에도 남아야 함")
        self.assertTrue(self.panel.save_current())

        restarted = MainWindow()
        try:
            restarted._video_id = "video-review"
            restarted._refresh_saved_results()
            self.assertEqual(restarted._saved_results_combo.count(), 1)
            self.assertIn("시연용 · 완료 · 후보 3개", restarted._saved_results_combo.currentText())
            self.assertTrue(restarted._open_result_button.isEnabled())

            restarted._open_saved_result()

            panel = restarted._review_panel
            panel.candidate_list.setCurrentRow(0)
            self.assertEqual(panel.memo_edit.toPlainText(), "재시작 후에도 남아야 함")
            self.assertTrue(panel.judgement_buttons[ReviewJudgement.NEEDS_CHECK].isChecked())
            self.assertEqual(restarted._mode_banner.text(), "시연용 결과 (실제 AI 분석 아님)")
        finally:
            restarted.close()

    def test_opening_saved_result_keeps_plan_approval(self) -> None:
        from test_ui_workflow import metadata

        self.window._video_path = Path(self.temporary.name) / "video.mp4"
        self.window._video_path.touch()
        self.window._video_id = "video-ui"
        self.window._metadata = metadata()
        self.window._range_start.setRange(0, 9.999)
        self.window._range_end.setRange(0, 10.0)
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()
        state = self.window._session.state
        self.assertTrue(self.window._run_button.isEnabled())
        ResultStore(AppPaths.default()).save(
            AnalysisJob(
                job_id="job-ui",
                status=JobStatus.COMPLETED,
                progress=100,
                video_id="video-ui",
                origin=ResultOrigin.SAMPLE,
            )
        )
        self.window._refresh_saved_results()

        self.window._open_saved_result()

        self.assertIs(self.window._session.state, state)
        self.assertTrue(self.window._run_button.isEnabled())
        self.assertEqual(self.window._review_panel.job.job_id, "job-ui")

    def test_saved_results_list_without_video_or_results(self) -> None:
        self.window._video_id = None
        self.window._refresh_saved_results()
        self.assertEqual(self.window._saved_results_combo.currentText(), "영상을 먼저 선택하세요")
        self.assertFalse(self.window._open_result_button.isEnabled())
        self.window._video_id = "video-without-results"
        self.window._refresh_saved_results()
        self.assertEqual(
            self.window._saved_results_combo.currentText(), "이 영상의 저장된 결과 없음"
        )
        self.assertFalse(self.window._saved_results_combo.isEnabled())

    def test_saved_results_are_locked_while_analysis_runs(self) -> None:
        ResultStore(AppPaths.default()).save(job())
        self.window._video_id = "video-review"
        self.window._refresh_saved_results()
        self.assertTrue(self.window._open_result_button.isEnabled())

        self.window._set_analysis_running(True)
        self.assertFalse(self.window._open_result_button.isEnabled())
        self.assertFalse(self.window._saved_results_combo.isEnabled())
        self.window._set_analysis_running(False)
        self.assertTrue(self.window._open_result_button.isEnabled())

    def test_opening_saved_result_keeps_unsaved_reviews_when_canceled(self) -> None:
        ResultStore(AppPaths.default()).save(job(ResultOrigin.SAMPLE))
        self.window._video_id = "video-review"
        self.window._refresh_saved_results()
        self.panel.candidate_list.setCurrentRow(0)
        self.panel.memo_edit.setPlainText("저장 안 한 메모")

        with patch.object(
            self.window, "_confirm_discard_unsaved_reviews", return_value=False
        ) as confirm:
            self.window._open_saved_result()

        confirm.assert_called_once_with("다른 결과를 열까요?")
        self.assertEqual(self.panel.unsaved_indexes(), (1,))
        self.assertEqual(self.panel.memo_edit.toPlainText(), "저장 안 한 메모")

    def test_choosing_video_keeps_unsaved_reviews_when_canceled(self) -> None:
        video = Path(self.temporary.name) / "other.mp4"
        video.write_bytes(b"not really a video")
        self.window._video_id = "video-review"
        self.panel.candidate_list.setCurrentRow(0)
        self.panel.memo_edit.setPlainText("저장 안 한 메모")

        with (
            patch(
                "video_agent.ui.main_window.QFileDialog.getOpenFileNames",
                return_value=([str(video)], ""),
            ),
            patch.object(
                self.window, "_confirm_discard_unsaved_reviews", return_value=False
            ) as confirm,
        ):
            self.window._choose_video()

        confirm.assert_called_once_with("다른 영상을 열까요?")
        self.assertEqual(self.window._video_id, "video-review")
        self.assertEqual(self.panel.unsaved_indexes(), (1,))

    def test_new_analysis_is_not_started_when_discard_is_canceled(self) -> None:
        self.window._session = MagicMock()
        self.window._session.plan = MagicMock()
        self.window._video_path = Path(self.temporary.name) / "video.mp4"
        self.panel.candidate_list.setCurrentRow(0)
        self.panel.memo_edit.setPlainText("저장 안 한 메모")

        with (
            patch.object(
                self.window, "_confirm_discard_unsaved_reviews", return_value=False
            ) as confirm,
            patch("video_agent.ui.main_window.create_analyzer") as create,
        ):
            self.window._start_analysis()

        confirm.assert_called_once_with("새 분석을 시작할까요?")
        create.assert_not_called()
        self.assertIsNone(self.window._worker)
        self.assertEqual(self.panel.unsaved_indexes(), (1,))

    def test_failed_result_message_is_shown_in_panel(self) -> None:
        self.window._review_panel.clear()
        self.window._review_panel.show_message("분석 실패 — vision-model · 오류")
        self.assertEqual(self.window._result_list.count(), 1)
        self.assertFalse(self.panel.record_group.isEnabled())


if __name__ == "__main__":
    unittest.main()
