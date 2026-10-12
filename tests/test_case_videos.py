from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QPushButton

from dataclasses import replace
from unittest.mock import MagicMock

from video_agent.analysis.vision import build_prompt
from video_agent.contracts import (
    AnalysisMode,
    CaseVideoLink,
    InformationCertainty,
    JobStatus,
    ResultOrigin,
    ReviewJudgement,
)
from video_agent.models.config import RuntimeInventory
from video_agent.ui.main_window import MainWindow
from video_agent.ui.video_list import VIDEO_ID_ROLE
from video_agent.video.files import build_video_id

from test_model_analysis import frame
from test_review_panel import job as review_job
from test_batch_analysis import BatchProbe, BatchScanner
from test_ui_workflow import metadata

# UI tests run without a model, so they use the demonstration (off for staff).
os.environ.setdefault("VIDEO_AGENT_ENABLE_SAMPLE", "1")

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
            patch("video_agent.ui.main_window.notify"),
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

    def test_row_buttons_work_again_after_the_list_unlocks(self) -> None:
        # Rows drawn while the list is locked (a video being checked) must not stay off.
        card = self.window._video_list
        card.set_locked(True)
        self.add(self.cam_a, self.cam_b)
        card.set_entries(card.entries, build_video_id(self.cam_a))
        card.set_locked(False)

        def buttons(video_id: str) -> list[QPushButton]:
            row = next(i for i, entry in enumerate(card.entries) if entry.video_id == video_id)
            return card.list.cellWidget(row, 2).findChildren(QPushButton)

        (remove,) = buttons(build_video_id(self.cam_b))
        self.assertTrue(remove.isEnabled())
        (current_remove,) = buttons(build_video_id(self.cam_a))
        self.assertFalse(current_remove.isEnabled())  # the open video cannot be removed

        remove.click()
        self.assertEqual([entry.video_id for entry in card.entries], [build_video_id(self.cam_a)])

    def test_adding_videos_lists_each_once_and_opens_the_first(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.add(self.cam_a)

        names = [entry.file_name for entry in self.entries()]
        self.assertEqual(names, ["cam_a.mp4", "cam_b.mp4"])
        self.assertEqual(self.window._video_id, build_video_id(self.cam_a))
        self.assertEqual(
            self.window._video_list.summary_text(), "영상 2건"
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

    def test_video_table_has_no_clock_column_or_clock_editing(self) -> None:
        self.add(self.cam_a, self.cam_b)
        card = self.window._video_list
        headers = [card.list.horizontalHeaderItem(c).text() for c in range(card.list.columnCount())]
        self.assertEqual(headers, ["영상", "분석 상태", ""])
        self.assertFalse(hasattr(card, "_clock_editor"))
        self.assertFalse(hasattr(self.window, "_set_video_clock"))
        # A new video needs nothing more at the bottom line.
        self.assertTrue(self.window._attention_label.isHidden())

    def test_camera_location_is_not_asked_but_an_old_one_is_kept(self) -> None:
        self.assertFalse(hasattr(self.window._case_panel, "camera_location_edit"))
        self.add(self.cam_a)
        video_id = build_video_id(self.cam_a)
        self.window._case_record = replace(
            self.window._case_record,
            video_links=(CaseVideoLink(video_id=video_id, camera_location="문앞 CCTV"),),
        )
        self.assertEqual(self.window._link_for(video_id).camera_location, "문앞 CCTV")

    def test_saved_case_links_all_videos_and_relinks_the_same_file_only(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.window._case_panel.title_edit.setText("문 앞 택배 이동")
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
        self.assertEqual(
            reopened._video_list.summary_text(), "영상 2건 · 파일 연결 필요 2건"
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

    def test_removing_a_video_drops_its_link_but_not_the_open_one(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.window._remove_video(build_video_id(self.cam_a))
        self.assertEqual(len(self.entries()), 2)

        self.window._remove_video(build_video_id(self.cam_b))
        self.assertEqual([entry.file_name for entry in self.entries()], ["cam_a.mp4"])
        self.window._case_panel.title_edit.setText("문 앞 택배 이동")
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
            self.window._video_list.summary_text(), "영상 2건 · 분석 1건"
        )

    def test_person_comparison_enables_only_for_real_candidates_from_two_videos(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.assertFalse(self.window._person_compare_button.isEnabled())
        model_id = "vision-model"
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            (model_id,),
            None,
            None,
            (model_id,),
        )
        self.window._refresh_analysis_choices()
        self.window._select_mode(AnalysisMode.LOCAL)
        self.window._set_model_id(model_id)
        for number, path in enumerate((self.cam_a, self.cam_b), start=1):
            video_id = build_video_id(path)
            base = review_job(ResultOrigin.REAL_MODEL)
            job = replace(
                base,
                job_id=f"job-real-{number}",
                video_id=video_id,
                segments=tuple(replace(item, video_id=video_id) for item in base.segments),
            )
            self.window._result_store.save(job)
        self.window._refresh_video_statuses()
        self.window._refresh_video_list()

        self.assertFalse(self.window._person_compare_button.isHidden())
        self.assertTrue(self.window._person_compare_button.isEnabled())
        self.assertEqual(
            {item.video_id for item in self.window._person_comparison_candidates()},
            {build_video_id(self.cam_a), build_video_id(self.cam_b)},
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
        # Linked videos need nothing more (no camera clock check).
        self.assertTrue(window._attention_label.isHidden())

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
        # Short names in list order; the file name is in the hover note.
        tabs = self.window._camera_tabs
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ["영상 1 (결과 없음)", "영상 2 (결과 없음)"])
        self.assertIn("cam_b", tabs.tabToolTip(1))

    def test_camera_tab_opens_that_video_and_its_newest_result(self) -> None:
        self.store(self.cam_b, "job-b")
        self.add(self.cam_a, self.cam_b)

        self.click_tab(self.cam_b)

        self.assertEqual(self.window._video_id, build_video_id(self.cam_b))
        self.assertEqual(self.window._review_panel.job.job_id, "job-b")
        # The only result is already shown, so no "이전 결과" row.
        self.assertTrue(self.window._saved_results_row.isHidden())
        self.assertEqual(self.window._camera_tabs.tabText(1), "영상 2 (3)")

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
        self.window._case_panel.title_edit.setText("문 앞 택배 이동")
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

        self.assertTrue(card.add_button.isHidden())
        # The row buttons (link, remove) belong to step ① only.
        self.assertFalse(card.list.isColumnHidden(1))
        self.assertTrue(card.list.isColumnHidden(2))
        self.select(build_video_id(self.cam_b))
        self.assertEqual(self.window._video_id, build_video_id(self.cam_b))

        self.window._show_step(2)
        self.assertTrue(card.add_button.isHidden())
        self.window._show_step(0)
        self.assertFalse(card.add_button.isHidden())
        self.assertFalse(card.list.isColumnHidden(2))

    def test_unlinked_video_is_not_relinked_from_later_steps(self) -> None:
        self.add(self.cam_a, self.cam_b)
        self.window._case_panel.title_edit.setText("문 앞 택배 이동")
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


class StepTwoVideoChoiceTests(unittest.TestCase):
    """Step ②: check one video (as before) or several (one prepared batch, same buttons)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    setUp = CaseVideoListTests.setUp
    tearDown = CaseVideoListTests.tearDown
    new_window = CaseVideoListTests.new_window
    video = CaseVideoListTests.video
    add = CaseVideoListTests.add

    def two_videos_in_step_two(self) -> tuple[str, str]:
        self.add(self.cam_a, self.cam_b)
        self.window._show_step(1)
        return build_video_id(self.cam_a), build_video_id(self.cam_b)

    def checked(self) -> list[str]:
        table = self.window._video_list.list
        return [
            table.item(row, 0).data(VIDEO_ID_ROLE)
            for row in range(table.rowCount())
            if table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def test_open_video_is_checked_and_more_can_be_added(self) -> None:
        a, b = self.two_videos_in_step_two()
        window = self.window
        self.assertEqual(self.checked(), [a])
        self.assertFalse(window._multi_target())
        self.assertFalse(window._single_range_box.isHidden())

        window._video_list._toggle_target(b)
        self.assertEqual(window._analysis_targets, (a, b))
        self.assertEqual(self.checked(), [a, b])
        # Several videos: each finds its own ranges, and the card says how many.
        self.assertTrue(window._single_range_box.isHidden())
        self.assertFalse(window._multi_range_label.isHidden())
        self.assertEqual(window._plan_group.aside_label.text(), "분석할 영상 2개")
        # Same buttons as one video; no separate batch window.
        self.assertFalse(hasattr(window, "_batch_button"))
        self.assertFalse(window._make_plan_button.isHidden())

        # Step ① and ③ have no check boxes.
        window._show_step(0)
        self.assertEqual(self.checked(), [])
        self.assertIsNone(window._video_list.list.item(0, 0).data(Qt.ItemDataRole.CheckStateRole))

    def test_select_all_and_clear_keep_the_open_video(self) -> None:
        a, b = self.two_videos_in_step_two()
        card = self.window._video_list
        self.assertFalse(card.all_button.isHidden())
        card.all_button.click()
        self.assertEqual(self.window._analysis_targets, (a, b))
        self.assertEqual(card.all_button.text(), "선택 해제")
        card.all_button.click()
        self.assertEqual(self.window._analysis_targets, (a,))

    def test_one_checked_video_is_opened(self) -> None:
        a, b = self.two_videos_in_step_two()
        window = self.window
        window._video_list._toggle_target(b)
        window._video_list._toggle_target(a)
        self.assertEqual(window._analysis_targets, (b,))
        self.assertEqual(window._video_id, b)
        self.assertFalse(window._multi_target())

    def test_external_ai_takes_one_video_at_a_time(self) -> None:
        a, b = self.two_videos_in_step_two()
        window = self.window
        window._video_list._toggle_target(b)
        window._metadata = replace(metadata(), video_id=a)
        window._select_mode(AnalysisMode.EXTERNAL)
        window._set_model_id("vision-model")
        window._purpose_edit.setPlainText("출입문 앞 물체를 들고 이동하는 장면 후보 확인")
        with patch("video_agent.ui.main_window.notify") as notify:
            window._make_plan()
        self.assertIn("한 번에 한 영상", notify.call_args.args[2])
        self.assertIsNone(window._batch_prepare_worker)

    def test_several_videos_plan_once_then_approval_runs_each_in_order(self) -> None:
        a, b = self.two_videos_in_step_two()
        window = self.window
        window._video_list._toggle_target(b)
        window._metadata = replace(metadata(), video_id=a)
        window._select_mode(AnalysisMode.SAMPLE)
        window._purpose_edit.setPlainText("출입문 앞 물체를 들고 이동하는 장면 후보 확인")
        probe = BatchProbe({self.cam_a: a, self.cam_b: b})
        from video_agent.analysis.batch import BatchPreparationWorker

        def preparation(*args, **kwargs):
            return BatchPreparationWorker(*args, probe=probe, scanner=BatchScanner(), **kwargs)

        with patch("video_agent.ui.main_window.BatchPreparationWorker", side_effect=preparation), patch(
            "video_agent.analysis.batch.QThread.start"
        ):
            window._make_plan()
            worker = window._batch_prepare_worker
            self.assertIsNotNone(worker)
            # No model call before approval: the plan step only probes and scans.
            worker.run()
            worker.isRunning = lambda: False
            window._on_batch_prepare_finished()
        self.assertTrue(window._batch_ready())
        self.assertFalse(window._approve_button.isHidden())
        self.assertTrue(window._approve_button.isEnabled())
        plan = window._plan_view.toPlainText()
        self.assertIn("영상 1", plan)
        self.assertIn("cam_b.mp4", plan)
        self.assertIsNone(window._batch_run_worker)

        with patch("video_agent.analysis.batch.QThread.start"), patch.object(
            window, "_confirm_discard_unsaved_reviews", return_value=True
        ):
            window._approve_and_run()
            runner = window._batch_run_worker
            self.assertEqual(window._video_status_text(a)[0], "분석 중")
            self.assertEqual(window._video_status_text(b)[0], "분석 대기")
            runner.run()
            runner.isRunning = lambda: False
            window._on_batch_run_finished()
        self.assertEqual([job.error for job in window._batch_results], [None, None])
        self.assertEqual([job.video_id for job in window._batch_results], [a, b])
        self.assertEqual(window._progress_label.text(), "끝남 · 결과 2개")
        # Each video keeps its own saved result.
        for video_id in (a, b):
            stored, _skipped = window._result_store.list_for_video(video_id)
            self.assertEqual(len(stored), 1)

    def test_changing_the_checked_videos_needs_a_new_plan(self) -> None:
        a, b = self.two_videos_in_step_two()
        window = self.window
        window._video_list._toggle_target(b)
        window._batch_preparation = MagicMock(prepared=(object(),), failures=())
        window._update_post_run_buttons()
        self.assertTrue(window._batch_ready())
        window._video_list._toggle_target(b)
        self.assertIsNone(window._batch_preparation)
        self.assertFalse(window._multi_target())

