from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QGroupBox

from video_agent.contracts import AnalysisJob, JobStatus, ResultOrigin
from video_agent.models.config import RuntimeInventory
from video_agent.ui.main_window import MainWindow

from test_review_panel import job as review_job
from test_ui_workflow import metadata

# UI tests run without a model, so they use the demonstration (off for staff).
os.environ.setdefault("VIDEO_AGENT_ENABLE_SAMPLE", "1")


class StepNavigationTests(unittest.TestCase):
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
        self.video = root / "video.mp4"
        self.video.touch()

    def tearDown(self) -> None:
        with patch.object(self.window, "_confirm_discard_unsaved_reviews", return_value=True):
            self.window.close()
        self.app.processEvents()
        self.runtime.stop()
        self.env.stop()
        self.temporary.cleanup()

    def step(self) -> int:
        return self.window._step_stack.currentIndex()

    def test_starts_on_case_step_with_next_button(self) -> None:
        self.assertEqual(self.step(), 0)
        self.assertTrue(self.window._step_buttons[0].isChecked())
        self.assertFalse(self.window._prev_step_button.isEnabled())
        self.assertEqual(self.window._next_step_button.text(), "다음: 영상 분석")
        self.assertTrue(self.window._prev_step_button.isHidden())

    def test_each_step_holds_its_sections(self) -> None:
        pages = [self.window._step_stack.widget(index) for index in range(3)]
        self.assertTrue(pages[0].isAncestorOf(self.window._case_panel))
        for group in (
            self.window._plan_group,
            self.window._approval_group,
            self.window._progress_group,
        ):
            self.assertTrue(pages[1].isAncestorOf(group))
        self.assertTrue(pages[2].isAncestorOf(self.window._result_group))
        self.assertFalse(pages[1].isAncestorOf(self.window._result_group))

    def test_approval_and_progress_stay_outside_the_plan_scroll(self) -> None:
        scrolled = self.window._plan_scroll.widget()
        self.assertTrue(scrolled.isAncestorOf(self.window._plan_group))
        self.assertFalse(scrolled.isAncestorOf(self.window._approval_group))
        self.assertFalse(scrolled.isAncestorOf(self.window._progress_group))
        self.assertTrue(self.window._step_stack.widget(1).isAncestorOf(self.window._run_button))

    def test_next_previous_and_step_bar_move_freely(self) -> None:
        self.window._next_step_button.click()
        self.assertEqual(self.step(), 1)
        self.assertEqual(self.window._prev_step_button.text(), "이전")
        self.assertEqual(self.window._next_step_button.text(), "다음: 결과 검토")
        self.window._next_step_button.click()
        self.assertEqual(self.step(), 2)
        self.assertFalse(self.window._next_step_button.isEnabled())
        self.window._step_buttons[0].click()
        self.assertEqual(self.step(), 0)
        self.assertTrue(self.window._step_buttons[0].isChecked())
        self.window._step_buttons[2].click()
        self.assertEqual(self.step(), 2)

    def test_review_step_shows_empty_state_until_a_result_is_shown(self) -> None:
        self.window._step_buttons[2].click()
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window._review_empty_label.isVisible())
        self.assertFalse(self.window._review_panel.isVisible())
        self.window._display_segments(review_job())
        self.assertFalse(self.window._review_empty_label.isVisible())
        self.assertTrue(self.window._review_panel.isVisible())
        self.window._review_panel.clear()
        self.window._refresh_review_empty_state()
        self.assertTrue(self.window._review_empty_label.isVisible())

    def test_inputs_and_approval_survive_step_changes(self) -> None:
        self.window._video_path = self.video
        self.window._video_id = "video-ui"
        self.window._metadata = metadata()
        self.window._range_start.setRange(0, 9.999)
        self.window._range_end.setRange(0, 10.0)
        self.window._show_step(1)
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()
        state = self.window._session.state

        self.window._show_step(0)
        self.window._show_step(2)
        self.window._show_step(1)

        self.assertIs(self.window._session.state, state)
        self.assertTrue(self.window._run_button.isEnabled())
        self.assertIn("30초 이상", self.window._purpose_edit.toPlainText())

    def test_completed_analysis_stays_on_plan_step_and_points_to_review(self) -> None:
        self.window._video_path = self.video
        self.window._video_id = "video-ui"
        self.window._metadata = metadata()
        self.window._range_start.setRange(0, 9.999)
        self.window._range_end.setRange(0, 10.0)
        self.window._show_step(1)
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()
        request = self.window._session.create_request(str(self.video), "job-step")
        done = AnalysisJob(
            job_id=request.job_id,
            plan_id=request.plan_id,
            status=JobStatus.COMPLETED,
            progress=100,
            video_id=request.video_id,
            origin=ResultOrigin.SAMPLE,
        )

        self.window._on_analysis_result(done)

        self.assertEqual(self.step(), 1)
        self.assertIn("[결과 검토]", self.window.statusBar().currentMessage())
        self.assertIs(self.window._review_panel.job, done)

    def test_review_step_needs_no_horizontal_scroll_when_zoomed(self) -> None:
        self.window.resize(1220, 850)
        self.window.show()
        self.window._display_segments(review_job())
        self.window._show_step(2)
        scroll = self.window._step_scrolls[2]
        for percent in (130, 150):
            self.window._set_ui_zoom(percent)
            self.app.processEvents()
            self.assertLessEqual(
                scroll.widget().minimumSizeHint().width(), scroll.viewport().width(), percent
            )

    def test_reviewer_notes_are_not_squeezed_when_zoomed(self) -> None:
        self.window.resize(1220, 850)
        self.window.show()
        self.window._display_segments(review_job())
        self.window._show_step(2)
        card = self.window._review_panel.record_group
        for percent in (100, 130, 150):
            self.window._set_ui_zoom(percent)
            self.app.processEvents()
            self.assertGreaterEqual(card.height(), card.minimumSizeHint().height(), percent)

    def test_status_message_is_repeated_in_the_padded_label(self) -> None:
        self.window.statusBar().showMessage("상태 확인")
        self.assertEqual(self.window._status_label.text(), "상태 확인")
        self.window.statusBar().clearMessage()
        self.assertEqual(self.window._status_label.text(), "")

    def test_section_titles_have_no_step_numbers(self) -> None:
        titles = [box.title() for box in self.window.findChildren(QGroupBox)]
        self.assertIn("사건 정보", titles)
        self.assertIn("CCTV 영상", titles)
        self.assertIn("중요 구간 후보", titles)
        for title in titles:
            self.assertNotRegex(title, r"^\d\.\s")


if __name__ == "__main__":
    unittest.main()
