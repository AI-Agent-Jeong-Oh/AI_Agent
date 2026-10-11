from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from dataclasses import replace
from unittest.mock import MagicMock

from video_agent.analysis.vision import build_prompt
from video_agent.contracts import CaseVideoLink, JobStatus, ResultOrigin, ReviewJudgement
from video_agent.models.config import RuntimeInventory
from video_agent.ui.main_window import MainWindow
from video_agent.video.files import build_video_id

from test_model_analysis import frame
from test_review_panel import job as review_job
from test_ui_workflow import metadata

# Dataset files name the answer: C041 = theft at the door, A30 = taking a parcel.
LABELED_NAME = "C041_A30_SY25_P07_S02_01DBS.mp4"


class CaseVideoListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = patch.dict(
            os.environ,
            {
                "VIDEO_AGENT_DATA_DIR": str(self.root / "data"),
                "VIDEO_AGENT_CACHE_DIR": str(self.root / "cache"),
            },
            clear=False,
        )
        self.env.start()
        self.patches = [
            patch(
                "video_agent.ui.main_window.RuntimeInventory.detect",
                return_value=RuntimeInventory(None, (), None, None),
            ),
            # No real decoding: these tests cover the list, not FFprobe or playback.
            patch("video_agent.ui.main_window.MainWindow._start_metadata_probe"),
            patch("video_agent.ui.main_window.QMessageBox.critical"),
        ]
        for item in self.patches:
            item.start()
        self.window = self.new_window()
        self.cam_a = self.video("cam_a.mp4")
        self.cam_b = self.video("cam_b.mp4")

    def tearDown(self) -> None:
        for window in self.__dict__.get("_windows", []):
            with patch.object(window, "_confirm_discard_unsaved_reviews", return_value=True):
                window.close()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.env.stop()
        self.temporary.cleanup()

    def new_window(self) -> MainWindow:
        window = MainWindow()
        window._player.setSource = lambda _url: None
        self.__dict__.setdefault("_windows", []).append(window)
        return window

    def video(self, name: str) -> Path:
        path = self.root / name
        path.write_bytes(name.encode())
        return path

    def add(self, *paths: Path, window: MainWindow | None = None) -> None:
        window = window or self.window
        with patch(
            "video_agent.ui.main_window.QFileDialog.getOpenFileNames",
            return_value=([str(path) for path in paths], ""),
        ):
            window._choose_video()

    def entries(self, window: MainWindow | None = None):
        return (window or self.window)._video_list.entries

    def select(self, video_id: str) -> None:
        video_list = self.window._video_list.list
        for row in range(video_list.rowCount()):
            if self.window._video_list.entries[row].video_id == video_id:
                video_list.setCurrentCell(row, 0)
                return
        self.fail(f"{video_id} not listed")

    def test_adding_videos_lists_each_once_and_opens_the_first(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.add(self.cam_a)

        names = [entry.file_name for entry in self.entries()]
        self.assertEqual(names, ["cam_a.mp4", "cam_b.mp4"])
        self.assertEqual(self.window._video_id, build_video_id(self.cam_a))
        self.assertEqual(
            self.window._video_list.count_label.text(), "영상 2건"
        )

    def test_selecting_another_video_opens_it(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.select(build_video_id(self.cam_b))

        self.assertEqual(self.window._video_id, build_video_id(self.cam_b))
        self.assertEqual(self.window._video_path, self.cam_b)
        self.assertEqual(self.window._video_list.selected_video_id(), build_video_id(self.cam_b))

    def test_canceling_unsaved_review_keeps_the_open_video(self) -> None:
        self.add(self.cam_a, self.cam_b)
        with patch.object(
            self.window, "_confirm_discard_unsaved_reviews", return_value=False
        ) as confirm:
            self.select(build_video_id(self.cam_b))

        confirm.assert_called_once_with("다른 영상을 열까요?")
        self.assertEqual(self.window._video_id, build_video_id(self.cam_a))
        self.assertEqual(self.window._video_list.selected_video_id(), build_video_id(self.cam_a))

    def test_video_cannot_change_while_analysis_runs(self) -> None:
        self.add(self.cam_a, self.cam_b)
        with patch.object(self.window, "_background_work_running", return_value=True):
            self.select(build_video_id(self.cam_b))

        self.assertEqual(self.window._video_id, build_video_id(self.cam_a))

    def test_camera_details_follow_each_video(self) -> None:
        self.add(self.cam_a, self.cam_b)
        panel = self.window._case_panel
        panel.clock_offset.setValue(3.0)
        self.select(build_video_id(self.cam_b))
        self.assertEqual(panel.clock_offset.value(), 0.0)
        panel.clock_offset.setValue(-2.0)
        self.select(build_video_id(self.cam_a))

        self.assertEqual(panel.clock_offset.value(), 3.0)
        offsets = {
            link.video_id: link.clock_offset_seconds
            for link in self.window._case_from_panel(1).video_links
        }
        self.assertEqual(
            offsets, {build_video_id(self.cam_a): 3.0, build_video_id(self.cam_b): -2.0}
        )

    def test_camera_location_is_not_asked_but_an_old_one_is_kept(self) -> None:
        self.assertFalse(hasattr(self.window._case_panel, "camera_location_edit"))
        self.add(self.cam_a)
        video_id = build_video_id(self.cam_a)
        panel = self.window._case_panel
        panel.show_link(CaseVideoLink(video_id=video_id, camera_location="문앞 CCTV"))
        self.assertEqual(panel.video_link(video_id).camera_location, "문앞 CCTV")

    def test_saved_case_links_all_videos_and_relinks_the_same_file_only(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.window._case_panel.clock_offset.setValue(3.0)
        self.window._save_case()
        saved = self.window._case_record
        self.assertEqual(
            {link.video_id for link in saved.video_links},
            {build_video_id(self.cam_a), build_video_id(self.cam_b)},
        )

        reopened = self.new_window()
        reopened._case_panel.set_saved_cases((saved,), saved.case_id)
        reopened._load_case()
        self.assertEqual([entry.file_name for entry in self.entries(reopened)], [None, None])
        # Camera details stay empty and locked until one of the case videos is open.
        self.assertEqual(reopened._case_panel.clock_offset.value(), 0.0)
        self.assertFalse(reopened._case_panel.clock_offset.isEnabled())
        self.assertEqual(
            reopened._video_list.count_label.text(), "영상 2건 · 파일 연결 필요 2건"
        )

        other = self.video("other.mp4")
        with patch(
            "video_agent.ui.main_window.QFileDialog.getOpenFileName",
            return_value=(str(other), ""),
        ):
            reopened._relink_video(build_video_id(self.cam_a))
        self.assertIsNone(reopened._case_videos[build_video_id(self.cam_a)])

        with patch(
            "video_agent.ui.main_window.QFileDialog.getOpenFileName",
            return_value=(str(self.cam_a), ""),
        ):
            reopened._relink_video(build_video_id(self.cam_a))
        self.assertEqual(reopened._case_videos[build_video_id(self.cam_a)], self.cam_a)
        self.assertEqual(reopened._video_id, build_video_id(self.cam_a))
        self.assertEqual(reopened._case_panel.clock_offset.value(), 3.0)

    def test_removing_a_video_drops_its_link_but_not_the_open_one(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.window._remove_video(build_video_id(self.cam_a))
        self.assertEqual(len(self.entries()), 2)

        self.window._remove_video(build_video_id(self.cam_b))
        self.assertEqual([entry.file_name for entry in self.entries()], ["cam_a.mp4"])
        self.window._save_case()
        self.assertEqual(
            [link.video_id for link in self.window._case_record.video_links],
            [build_video_id(self.cam_a)],
        )
        self.assertTrue(self.cam_b.exists())

    def status_of(self, path: Path) -> str:
        entry = next(e for e in self.entries() if e.video_id == build_video_id(path))
        return entry.analysis_status

    def store(self, path: Path, job_id: str, **changes) -> None:
        stored = replace(review_job(), job_id=job_id, video_id=build_video_id(path), **changes)
        self.window._result_store.save(stored)

    def test_each_video_shows_its_newest_stored_result(self) -> None:
        self.store(self.cam_b, "job-b")
        self.add(self.cam_a, self.cam_b)

        self.assertEqual(self.status_of(self.cam_a), "분석 전")
        self.assertEqual(self.status_of(self.cam_b), "분석 완료(테스트 응답) · 후보 3")
        self.assertEqual(
            self.window._video_list.count_label.text(), "영상 2건 · 분석 1건"
        )

    def test_partial_and_sample_results_are_named(self) -> None:
        self.store(
            self.cam_a, "job-a-partial", status=JobStatus.PARTIAL, segments=(), error="일부 배치 실패"
        )
        self.add(self.cam_a)
        self.assertEqual(self.status_of(self.cam_a), "일부 완료(테스트 응답) · 후보 없음")

    def test_running_failed_and_canceled_runs_are_shown(self) -> None:
        self.store(self.cam_a, "job-a")
        self.add(self.cam_a, self.cam_b)

        self.window._worker = MagicMock()
        self.window._refresh_video_list()
        self.assertEqual(self.status_of(self.cam_a), "분석 중")
        self.window._worker = None

        failed = replace(
            review_job(),
            job_id="job-a-failed",
            video_id=build_video_id(self.cam_a),
            status=JobStatus.FAILED,
            origin=ResultOrigin.NONE,
            segments=(),
            error="모델 응답 없음",
        )
        self.window._record_run_outcome(failed)
        self.assertEqual(
            self.status_of(self.cam_a), "최근 실행 실패 · 이전 분석 완료(테스트 응답) · 후보 3"
        )
        canceled = replace(
            failed, job_id="job-b-canceled", video_id=build_video_id(self.cam_b),
            status=JobStatus.CANCELED, error=None,
        )
        self.window._record_run_outcome(canceled)
        self.assertEqual(self.status_of(self.cam_b), "최근 실행 취소")

        self.store(self.cam_a, "job-a-new")
        self.window._record_run_outcome(replace(review_job(), job_id="job-a-new",
                                                video_id=build_video_id(self.cam_a)))
        self.assertEqual(self.status_of(self.cam_a), "분석 완료(테스트 응답) · 후보 3 · 결과 2개")

    def tab_ids(self, window: MainWindow | None = None) -> list[str]:
        tabs = (window or self.window)._camera_tabs
        return [tabs.tabData(i) for i in range(tabs.count())]

    def click_tab(self, path: Path, window: MainWindow | None = None) -> None:
        window = window or self.window
        window._camera_tabs.setCurrentIndex(self.tab_ids(window).index(build_video_id(path)))

    def test_work_context_shows_case_state_and_open_checks(self) -> None:
        window = self.window
        self.assertEqual(window._case_title_label.text(), "새 사건")
        self.assertEqual(window._case_state_label.text(), "저장 안 됨")
        self.assertEqual(window._video_stack.currentIndex(), 0)
        self.assertEqual(window._video_placeholder_text.text(), "CCTV 영상을 추가하세요")
        self.assertTrue(window._attention_label.isHidden())

        self.add(self.cam_a, self.cam_b)
        self.assertEqual(window._video_stack.currentIndex(), 1)
        # New videos have no checked clock yet: listed at the bottom until someone checks it.
        self.assertFalse(window._attention_label.isHidden())
        self.assertIn("확인 필요 2건", window._attention_label.text())
        self.assertIn("기준 시각 미확인", window._attention_label.toolTip())

        window._case_panel.title_edit.setText("문 앞 택배 이동")
        window._save_case()
        self.assertEqual(window._case_title_label.text(), "문 앞 택배 이동")
        self.assertEqual(window._case_state_label.text(), "저장됨 v1")
        window._case_panel.title_edit.setText("문 앞 택배 이동 확인")
        self.assertEqual(window._case_state_label.text(), "저장 안 된 수정 있음")

    def test_file_menu_offers_the_usual_shortcuts(self) -> None:
        window = self.window
        self.assertEqual(window._save_case_action.shortcut(), QKeySequence(QKeySequence.StandardKey.Save))
        self.assertEqual(window._new_case_action.shortcut(), QKeySequence(QKeySequence.StandardKey.New))
        self.assertEqual(window._add_video_action.shortcut(), QKeySequence(QKeySequence.StandardKey.Open))
        titles = [action.text() for action in window.menuBar().actions()]
        self.assertEqual(titles, ["파일", "보기"])

    def test_skip_buttons_move_ten_seconds_within_the_video(self) -> None:
        window = self.window
        window._duration_ms = 15_000
        with patch.object(window._player, "position", return_value=8_000), patch.object(
            window._player, "setPosition"
        ) as seek:
            window._skip_playback(10)
            seek.assert_called_with(15_000)
            window._skip_playback(-10)
            seek.assert_called_with(0)

    def test_camera_tabs_appear_for_two_or_more_videos(self) -> None:
        self.add(self.cam_a)
        self.assertTrue(self.window._camera_row.isHidden())
        self.add(self.cam_b)
        self.assertFalse(self.window._camera_row.isHidden())
        self.assertEqual(self.tab_ids(), [build_video_id(self.cam_a), build_video_id(self.cam_b)])

    def test_camera_tab_opens_that_video_and_its_newest_result(self) -> None:
        self.store(self.cam_b, "job-b")
        self.add(self.cam_a, self.cam_b)

        self.click_tab(self.cam_b)

        self.assertEqual(self.window._video_id, build_video_id(self.cam_b))
        self.assertEqual(self.window._review_panel.job.job_id, "job-b")

    def test_canceling_unsaved_review_keeps_the_camera_tab(self) -> None:
        self.store(self.cam_a, "job-a")
        self.store(self.cam_b, "job-b")
        self.add(self.cam_a, self.cam_b)
        self.window._show_camera(build_video_id(self.cam_a))
        with patch.object(
            self.window, "_confirm_discard_unsaved_reviews", return_value=False
        ) as confirm:
            self.click_tab(self.cam_b)

        confirm.assert_called_once_with("다른 카메라를 볼까요?")
        self.assertEqual(self.window._review_panel.job.job_id, "job-a")
        self.assertEqual(
            self.tab_ids()[self.window._camera_tabs.currentIndex()], build_video_id(self.cam_a)
        )

    def test_unlinked_camera_shows_saved_result_but_does_not_play(self) -> None:
        self.store(self.cam_a, "job-a")
        self.store(self.cam_b, "job-b")
        self.add(self.cam_a, self.cam_b)
        self.window._save_case()
        saved = self.window._case_record

        reopened = self.new_window()
        reopened._case_panel.set_saved_cases((saved,), saved.case_id)
        reopened._load_case()
        first = reopened._camera_tabs.currentIndex()
        reopened._camera_tabs.tabBarClicked.emit(first)
        self.assertEqual(reopened._review_panel.job.video_id, self.tab_ids(reopened)[first])
        self.click_tab(self.cam_b, reopened)

        self.assertEqual(reopened._review_panel.job.job_id, "job-b")
        self.assertIsNone(reopened._video_path)
        reopened._player = MagicMock()
        reopened._play_review_range(0.0, 5.0)
        reopened._player.play.assert_not_called()

    def test_case_overview_lists_every_camera_with_saved_judgements(self) -> None:
        self.store(self.cam_a, "job-a")
        self.store(self.cam_b, "job-b")
        self.add(self.cam_a, self.cam_b)
        self.window._show_camera(build_video_id(self.cam_a))
        panel = self.window._review_panel
        panel.select_segment(1)
        panel.judgement_buttons[ReviewJudgement.RELEVANT].setChecked(True)
        panel.save_current()

        rows = self.window._case_overview.rows
        self.assertEqual(len(rows), 6)
        self.assertEqual({row.job_id for row in rows}, {"job-a", "job-b"})
        relevant = [row for row in rows if row.judgement == "관련 있음"]
        self.assertEqual([(row.job_id, row.segment_index) for row in relevant], [("job-a", 1)])
        toggle = self.window._case_overview.toggle_button
        self.assertEqual(toggle.text(), "전체 후보 6")
        self.assertIn("관련 있음 1", toggle.toolTip())

    def test_overview_row_opens_the_other_camera_and_selects_the_candidate(self) -> None:
        self.store(self.cam_a, "job-a")
        self.store(self.cam_b, "job-b")
        self.add(self.cam_a, self.cam_b)
        self.window._show_camera(build_video_id(self.cam_a))

        self.window._activate_overview_candidate(build_video_id(self.cam_b), "job-b", 2)

        self.assertEqual(self.window._video_id, build_video_id(self.cam_b))
        self.assertEqual(self.window._review_panel.job.job_id, "job-b")
        self.assertEqual(self.window._review_panel._current_index, 2)

    def test_summary_covers_every_camera_with_saved_reviews(self) -> None:
        self.store(self.cam_a, "job-a")
        self.store(self.cam_b, "job-b")
        self.add(self.cam_a, self.cam_b)
        self.window._show_camera(build_video_id(self.cam_a))
        panel = self.window._review_panel
        panel.select_segment(1)
        panel.judgement_buttons[ReviewJudgement.RELEVANT].setChecked(True)
        panel.memo_edit.setPlainText("A 카메라 저장 메모")
        panel.save_current()
        self.click_tab(self.cam_b)
        panel.select_segment(0)
        panel.memo_edit.setPlainText("B 카메라 저장 안 한 메모")

        panel.summary_button.click()

        dialog = self.window._summary_dialog
        self.assertEqual(dialog.scope, "case")
        scope = dialog.editors["scope"].toPlainText()
        self.assertIn("[cam_a]", scope)
        self.assertIn("[cam_b]", scope)
        self.assertIn("영상: cam_a.mp4", scope)
        self.assertNotIn(str(self.root), scope)
        opinions = dialog.editors["opinions"].toPlainText()
        self.assertIn("A 카메라 저장 메모", opinions)
        self.assertNotIn("B 카메라 저장 안 한 메모", opinions)
        self.assertIn("cam_b: 저장하지 않은 기록 1건", dialog.warning_label.text())
        with patch.object(dialog, "_confirm_regenerate", return_value=True):
            dialog.scope_buttons["camera"].click()
        self.assertNotIn("[cam_a]", dialog.editors["scope"].toPlainText())
        self.assertIn("결과 ID: job-b", dialog.editors["scope"].toPlainText())
        dialog.close()

    def test_summary_has_one_scope_when_only_one_camera_has_a_result(self) -> None:
        self.store(self.cam_a, "job-a")
        self.add(self.cam_a, self.cam_b)
        self.window._show_camera(build_video_id(self.cam_a))

        self.window._review_panel.summary_button.click()

        dialog = self.window._summary_dialog
        self.assertEqual(dialog.scope, "camera")
        self.assertEqual(dialog.scope_buttons, {})
        dialog.close()

    def test_later_steps_only_choose_among_registered_videos(self) -> None:
        self.add(self.cam_a, self.cam_b)
        card = self.window._video_list
        self.window._show_step(1)

        for hidden in (card.add_button, card.relink_button, card.remove_button,
                       self.window._case_panel.camera_fields):
            self.assertTrue(hidden.isHidden())
        self.select(build_video_id(self.cam_b))
        self.assertEqual(self.window._video_id, build_video_id(self.cam_b))

        self.window._show_step(2)
        self.assertTrue(card.add_button.isHidden())
        self.window._show_step(0)
        self.assertFalse(card.add_button.isHidden())
        self.assertFalse(self.window._case_panel.camera_fields.isHidden())

    def test_unlinked_video_is_not_relinked_from_later_steps(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.window._save_case()
        saved = self.window._case_record
        reopened = self.new_window()
        reopened._case_panel.set_saved_cases((saved,), saved.case_id)
        reopened._load_case()
        reopened._show_step(1)
        card = reopened._video_list
        with patch.object(reopened, "_relink_video") as relink:
            card._item_double_clicked(card.list.item(0, 0))
        relink.assert_not_called()
        reopened._show_step(0)
        with patch.object(reopened, "_relink_video") as relink:
            card._item_double_clicked(card.list.item(0, 0))
        relink.assert_called_once()

    def test_dataset_file_name_never_reaches_the_model_prompt(self) -> None:
        labeled = self.video(LABELED_NAME)
        self.add(labeled)
        window = self.window
        window._metadata = metadata()
        window._range_start.setRange(0, 9.999)
        window._range_end.setRange(0, 10.0)
        window._purpose_edit.setPlainText("문 앞 상자를 옮기는 장면 후보 찾기")
        window._make_plan()
        window._approve_plan()
        request = window._session.create_request(str(labeled), "job-names")

        prompt = build_prompt(request, (frame(self.root / "frame.jpg"),))

        self.assertEqual(request.video_path, labeled)
        for leaked in ("C041", "A30", "SY25", "01DBS", Path(LABELED_NAME).stem):
            self.assertNotIn(leaked, prompt)


if __name__ == "__main__":
    unittest.main()
