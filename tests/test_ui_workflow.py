from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from video_agent.contracts import (
    AnalysisJob,
    AnalysisMode,
    JobStatus,
    MotionProposal,
    MotionScanProfile,
    MotionScanResult,
    ResultOrigin,
    SamplingStrategy,
    Segment,
)
from video_agent.models.config import RuntimeInventory
from video_agent.storage.json_store import AppSettings
from video_agent.ui.main_window import MainWindow
from video_agent.video.probe import VideoMetadata


def metadata() -> VideoMetadata:
    return VideoMetadata(
        video_id="video-ui",
        duration_seconds=10.0,
        coded_width=320,
        coded_height=240,
        display_width=320,
        display_height=240,
        rotation_degrees=0,
        average_frame_rate=24.0,
        nominal_frame_rate=24.0,
        variable_frame_rate_detected=False,
        video_codec="h264",
        has_audio=False,
        start_time_seconds=0,
        stream_time_base="1/12288",
        file_size_bytes=10,
    )


class UiWorkflowTests(unittest.TestCase):
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
                "VIDEO_AGENT_EXTERNAL_PROVIDER": "test-provider",
                "VIDEO_AGENT_EXTERNAL_ENDPOINT": "https://provider.invalid/v1/chat/completions",
                "VIDEO_AGENT_LOCAL_MODEL": "",
                "VIDEO_AGENT_EXTERNAL_MODEL": "",
            },
            clear=False,
        )
        self.env.start()
        self.runtime = patch(
            "video_agent.ui.main_window.RuntimeInventory.detect",
            return_value=RuntimeInventory(None, (), None, None),
        )
        self.runtime.start()
        self.video = root / "video.mp4"
        self.video.touch()
        self.window = MainWindow()
        self.window._video_path = self.video
        self.window._video_id = "video-ui"
        self.window._metadata = metadata()
        self.window._range_start.setRange(0, 9.999)
        self.window._range_end.setRange(0, 10.0)

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()
        self.runtime.stop()
        self.env.stop()
        self.temporary.cleanup()

    def test_analysis_cannot_run_before_approval_and_change_invalidates_it(self) -> None:
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.assertFalse(self.window._run_button.isEnabled())

        self.window._approve_plan()
        self.assertTrue(self.window._run_button.isEnabled())

        self.window._purpose_edit.setPlainText("주차 차량 주변의 쓰러짐 장면 후보 찾기")
        self.assertFalse(self.window._run_button.isEnabled())
        self.assertIsNone(self.window._session.analysis_approval)

    def test_analysis_panel_preserves_readable_motion_and_plan_areas(self) -> None:
        self.assertGreaterEqual(self.window._motion_list.minimumHeight(), 120)
        self.assertGreaterEqual(self.window._plan_view.minimumHeight(), 140)
        self.assertGreaterEqual(self.window._plan_group.minimumHeight(), 450)
        self.assertIs(self.window._analysis_scroll.widget(), self.window._plan_group.parentWidget())

    def test_ui_zoom_scales_controls_and_is_bounded(self) -> None:
        base_size = self.window.font().pointSizeF()

        self.window._set_ui_zoom(130)

        self.assertEqual(self.window._ui_zoom_percent, 130)
        self.assertEqual(self.window._zoom_reset_button.text(), "130%")
        self.assertGreater(self.window.font().pointSizeF(), base_size)
        self.assertGreaterEqual(self.window._motion_list.minimumHeight(), 166)

        self.window._set_ui_zoom(999)
        self.assertEqual(self.window._ui_zoom_percent, self.window.UI_ZOOM_MAX)
        self.assertFalse(self.window._zoom_in_button.isEnabled())

        self.window._set_ui_zoom(-1)
        self.assertEqual(self.window._ui_zoom_percent, self.window.UI_ZOOM_MIN)
        self.assertFalse(self.window._zoom_out_button.isEnabled())

        self.window._set_ui_zoom(100)
        self.assertEqual(self.window.font().pointSizeF(), base_size)

    def test_vague_duration_purpose_requires_a_time_threshold(self) -> None:
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 오래 머무르는 장면 후보 찾기")

        self.window._make_plan()

        self.assertIn("몇 초 또는 몇 분", self.window._question_label.text())
        self.assertIsNone(self.window._session.plan)

        self.window._clarification_edit.setText("30초 이상")
        self.window._make_plan()

        self.assertIsNotNone(self.window._session.plan)
        self.assertIn("지속 시간 기준: 30초 이상", self.window._session.plan.purpose)

    def test_external_transfer_approval_is_separate(self) -> None:
        self.window._mode_combo.setCurrentIndex(self.window._mode_combo.findData(AnalysisMode.EXTERNAL))
        self.window._model_edit.setText("vision-model")
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()

        self.assertFalse(self.window._run_button.isEnabled())
        self.assertTrue(self.window._transfer_button.isEnabled())

        self.window._approve_transfer()
        self.window._update_post_run_buttons()
        self.assertTrue(self.window._run_button.isEnabled())
        self.assertFalse(self.window._transfer_button.isEnabled())

    def test_late_result_after_cancel_is_ignored(self) -> None:
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()
        request = self.window._session.create_request(str(self.video), "job-late")
        self.window._session.cancel_active()
        late = AnalysisJob(
            job_id=request.job_id,
            plan_id=request.plan_id,
            status=JobStatus.COMPLETED,
            progress=100,
            video_id=request.video_id,
            origin=ResultOrigin.SAMPLE,
        )

        self.window._on_analysis_result(late)

        self.assertEqual(self.window._result_list.count(), 0)
        self.assertEqual(self.window._progress_label.text(), "대기 중")

    def test_canceled_result_is_not_saved(self) -> None:
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()
        request = self.window._session.create_request(str(self.video), "job-canceled")
        canceled = AnalysisJob(
            job_id=request.job_id,
            plan_id=request.plan_id,
            status=JobStatus.CANCELED,
            progress=None,
            video_id=request.video_id,
            origin=ResultOrigin.NONE,
        )

        self.window._on_analysis_result(canceled)

        self.assertFalse((Path(os.environ["VIDEO_AGENT_DATA_DIR"]) / "results" / "job-canceled.json").exists())

    def test_result_list_distinguishes_review_range_and_observation_time(self) -> None:
        job = AnalysisJob(
            job_id="job-display",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-ui",
            mode=AnalysisMode.LOCAL,
            model_id="vision-model",
            is_sample=False,
            origin=ResultOrigin.TEST_DOUBLE,
            segments=(
                Segment(
                    video_id="video-ui",
                    start_sec=1.0,
                    end_sec=3.0,
                    summary="사람의 발과 물체가 가까워 보이는 후보",
                    evidence=("2초 프레임에서 가까운 위치가 관찰됨",),
                    uncertainty="희소 프레임이므로 원본 동작 확인 필요",
                    source_frame_ids=("video-ui:2000",),
                    source_timestamps_sec=(2.0,),
                ),
            ),
        )

        self.window._display_segments(job)

        text = self.window._result_list.item(0).text()
        self.assertIn("확인용 재생 범위", text)
        self.assertIn("클릭 시 맥락 재생 시작 00:00", text)
        self.assertIn("관찰 프레임 원본 시각: 2.000초", text)

    def test_result_click_starts_one_second_before_review_range(self) -> None:
        segment = Segment(
            video_id="video-ui",
            start_sec=3.0,
            end_sec=4.0,
            summary="짧은 접촉 후보",
            evidence=("3.5초 프레임에서 접촉 가능성이 관찰됨",),
            uncertainty="원본 동작 확인 필요",
            source_frame_ids=("video-ui:3500",),
            source_timestamps_sec=(3.5,),
        )
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, segment)
        self.window._duration_ms = 10_000
        self.window._player = MagicMock()

        self.window._seek_to_segment(item)

        self.window._player.setPosition.assert_called_once_with(2_000)
        self.assertIn("원본 00:02부터", self.window.statusBar().currentMessage())
        self.assertIn("00:03–00:04", self.window.statusBar().currentMessage())

    def test_result_context_preroll_is_clamped_to_video_start(self) -> None:
        segment = Segment(
            video_id="video-ui",
            start_sec=0.4,
            end_sec=1.0,
            summary="영상 시작 부근 후보",
            evidence=("0.5초 프레임에서 변화가 관찰됨",),
            uncertainty="원본 확인 필요",
            source_frame_ids=("video-ui:500",),
            source_timestamps_sec=(0.5,),
        )

        self.assertEqual(self.window._segment_playback_start_sec(segment), 0.0)

    def test_duration_purpose_displays_observation_span_without_claiming_continuity(self) -> None:
        segment = Segment(
            video_id="video-ui",
            start_sec=0.5,
            end_sec=9.5,
            summary="출입문 앞에 사람이 보이는 후보",
            evidence=("1초와 9초 프레임에서 사람이 관찰됨",),
            uncertainty="중간 연속 체류는 원본 확인 필요",
            source_frame_ids=("video-ui:1000", "video-ui:9000"),
            source_timestamps_sec=(1.0, 9.0),
        )

        text = self.window._segment_text(
            segment,
            "출입문 앞에 5초 이상 머무르는 장면 찾기",
        )

        self.assertIn("지속 시간 표본 근거", text)
        self.assertIn("2개 관찰 시각", text)
        self.assertIn("첫·마지막 표본 사이 8.000초", text)
        self.assertIn("중간의 연속성은 원본 확인 필요", text)

    def test_completed_empty_model_result_is_not_presented_as_failure(self) -> None:
        job = AnalysisJob(
            job_id="job-empty",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-ui",
            mode=AnalysisMode.LOCAL,
            model_id="vision-model",
            is_sample=False,
            origin=ResultOrigin.REAL_MODEL,
        )

        self.window._display_segments(job)

        self.assertIn("후보를 찾지 못했습니다", self.window._result_list.item(0).text())

    def test_switching_modes_preserves_each_explicit_model_setting(self) -> None:
        local_index = self.window._mode_combo.findData(AnalysisMode.LOCAL)
        external_index = self.window._mode_combo.findData(AnalysisMode.EXTERNAL)
        self.window._mode_combo.setCurrentIndex(local_index)
        self.window._model_edit.setText("local-vision-model")
        self.window._mode_combo.setCurrentIndex(external_index)
        self.window._model_edit.setText("external-vision-model")

        self.window._mode_combo.setCurrentIndex(local_index)
        self.assertEqual(self.window._model_edit.text(), "local-vision-model")
        self.window._mode_combo.setCurrentIndex(external_index)
        self.assertEqual(self.window._model_edit.text(), "external-vision-model")

    def test_local_mode_selects_verified_instruct_model_and_uses_local_limit(self) -> None:
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:8b", "qwen3-vl:4b-instruct"),
            None,
            None,
            ("qwen3-vl:8b", "qwen3-vl:4b-instruct"),
        )
        self.window._settings = AppSettings()
        local_index = self.window._mode_combo.findData(AnalysisMode.LOCAL)
        self.window._mode_combo.setCurrentIndex(local_index)
        self.window._purpose_edit.setPlainText("사람과 유모차가 강하게 접촉하는 장면 후보 찾기")

        self.window._make_plan()

        self.assertEqual(self.window._model_edit.text(), "qwen3-vl:4b-instruct")
        self.assertIsNotNone(self.window._session.plan)
        self.assertEqual(self.window._session.plan.limits.max_runtime_seconds, 300.0)
        self.assertEqual(self.window._session.plan.limits.max_provider_requests, 3)
        self.assertEqual(self.window._session.plan.limits.max_frames_per_request, 4)

    def test_fine_detail_plan_discloses_overview_and_center_crop(self) -> None:
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:4b-instruct",),
            None,
            None,
            ("qwen3-vl:4b-instruct",),
        )
        local_index = self.window._mode_combo.findData(AnalysisMode.LOCAL)
        self.window._mode_combo.setCurrentIndex(local_index)
        self.window._purpose_edit.setPlainText(
            "사람의 손가락이 초인종 버튼을 반복해서 누르는 장면 후보 찾기"
        )

        self.window._make_plan()

        plan = self.window._session.plan
        self.assertEqual(plan.sampling_strategy, SamplingStrategy.TEMPORAL_SEQUENCE)
        plan_text = self.window._plan_view.toPlainText()
        self.assertIn("최대 1920px 원본", plan_text)
        self.assertIn("전체 화면 보기 + 중앙 60% 확대 보기", plan_text)
        self.assertNotIn("2×2 시간순 비교 보드", plan_text)

    def test_fine_detail_motion_scan_uses_peak_profile(self) -> None:
        self.window._purpose_edit.setPlainText(
            "사람의 손가락이 초인종 버튼을 반복해서 누르는 장면 후보 찾기"
        )

        with patch("video_agent.ui.main_window.MotionScanWorker.start") as start:
            self.window._start_motion_scan()

        self.assertEqual(
            self.window._motion_worker._profile,
            MotionScanProfile.GENERAL_PEAK,
        )
        self.assertIn("작은 손·손가락·버튼 접촉", self.window._motion_help.text())
        self.assertIn("전체 화면 보기와 중앙 60% 확대 보기", self.window._motion_help.text())
        start.assert_called_once()
        self.window._on_motion_scan_finished()

    def test_failed_model_result_replaces_previous_success_banner(self) -> None:
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:8b",),
            None,
            None,
            ("qwen3-vl:8b",),
        )
        local_index = self.window._mode_combo.findData(AnalysisMode.LOCAL)
        self.window._mode_combo.setCurrentIndex(local_index)
        self.window._model_edit.setText("qwen3-vl:8b")
        self.window._purpose_edit.setPlainText(
            "사람의 손가락이 초인종 버튼을 누르는 장면 후보 찾기"
        )
        self.window._make_plan()
        self.window._approve_plan()
        analysis_request = self.window._session.create_request(
            str(self.video),
            "job-empty-content",
        )
        self.window._set_result_banner(ResultOrigin.REAL_MODEL, "qwen3-vl:4b-instruct")
        failed = AnalysisJob(
            job_id=analysis_request.job_id,
            plan_id=analysis_request.plan_id,
            status=JobStatus.FAILED,
            progress=None,
            video_id=analysis_request.video_id,
            error="모델이 최종 JSON content 없이 thinking만 반환했습니다.",
            mode=AnalysisMode.LOCAL,
            model_id="qwen3-vl:8b",
            is_sample=False,
            origin=ResultOrigin.NONE,
        )

        with patch.object(self.window, "_show_error") as show_error:
            self.window._on_analysis_result(failed)

        self.assertEqual(
            self.window._mode_banner.text(),
            "모델 응답 실패 — qwen3-vl:8b",
        )
        self.assertIn("qwen3-vl:8b", self.window._result_list.item(0).text())
        self.assertIn("thinking만 반환", self.window._result_list.item(0).text())
        show_error.assert_called_once()

    def test_applying_motion_proposal_invalidates_approval_and_builds_dense_plan(self) -> None:
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:4b-instruct",),
            None,
            None,
            ("qwen3-vl:4b-instruct",),
        )
        local_index = self.window._mode_combo.findData(AnalysisMode.LOCAL)
        self.window._mode_combo.setCurrentIndex(local_index)
        self.window._purpose_edit.setPlainText("사람의 발과 물체 위치 변화가 보이는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()
        self.assertIsNotNone(self.window._session.analysis_approval)

        proposal = MotionProposal(
            proposal_id="motion-1",
            video_id="video-ui",
            start_sec=2.0,
            end_sec=8.0,
            peak_timestamp_sec=5.0,
            motion_score=4.2,
            recommended_sampling_interval_sec=0.5,
        )
        self.window._active_motion_scan_id = "scan-1"
        self.window._on_motion_scan_result(
            MotionScanResult(
                scan_id="scan-1",
                video_id="video-ui",
                range_start_sec=0.0,
                range_end_sec=10.0,
                sample_interval_sec=0.5,
                sampled_frame_count=19,
                proposals=(proposal,),
            )
        )

        self.window._apply_motion_proposal()

        self.assertIsNone(self.window._session.analysis_approval)
        self.assertEqual(self.window._range_start.value(), 2.0)
        self.assertEqual(self.window._range_end.value(), 8.0)
        self.assertFalse(self.window._run_button.isEnabled())

        self.window._make_plan()

        self.assertEqual(len(self.window._session.plan.sample_timestamps_sec), 12)
        self.assertAlmostEqual(self.window._session.plan.sampling_interval_sec, 0.25)
        self.assertIn("최대 변화 시각 중심", self.window._plan_view.toPlainText())
        self.assertEqual(
            self.window._session.plan.sampling_strategy,
            SamplingStrategy.MOTION_PEAK,
        )

    def test_repeated_direction_purpose_spreads_temporal_samples_and_displays_guidance(self) -> None:
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:4b-instruct",),
            None,
            None,
            ("qwen3-vl:4b-instruct",),
        )
        local_index = self.window._mode_combo.findData(AnalysisMode.LOCAL)
        self.window._mode_combo.setCurrentIndex(local_index)
        self.window._purpose_edit.setPlainText(
            "사람이 주변을 두리번거리며 머리 방향을 반복해서 바꾸는 장면 후보 찾기"
        )
        proposal = MotionProposal(
            proposal_id="temporal-1",
            video_id="video-ui",
            start_sec=2.0,
            end_sec=8.0,
            peak_timestamp_sec=7.0,
            motion_score=3.2,
            recommended_sampling_interval_sec=0.5,
        )
        self.window._active_motion_scan_id = "scan-temporal"
        self.window._on_motion_scan_result(
            MotionScanResult(
                scan_id="scan-temporal",
                video_id="video-ui",
                range_start_sec=0.0,
                range_end_sec=10.0,
                sample_interval_sec=0.5,
                sampled_frame_count=19,
                proposals=(proposal,),
                profile=MotionScanProfile.REPEATED_SUBTLE,
            )
        )

        self.assertIn("반복 변화 점수", self.window._motion_list.item(0).text())
        self.window._apply_motion_proposal()
        self.window._make_plan()

        plan = self.window._session.plan
        self.assertEqual(plan.sampling_strategy, SamplingStrategy.TEMPORAL_SEQUENCE)
        self.assertEqual(len(plan.sample_timestamps_sec), 12)
        self.assertLess(plan.sample_timestamps_sec[0], 2.5)
        self.assertGreater(plan.sample_timestamps_sec[-1], 7.5)
        self.assertTrue(plan.observation_guidance)
        plan_text = self.window._plan_view.toPlainText()
        self.assertIn("각 구간 전체에 분산한 시간순 비교", plan_text)
        self.assertIn("관찰 기준:", plan_text)

    def test_temporal_result_displays_observation_span_without_claiming_continuity(self) -> None:
        segment = Segment(
            video_id="video-ui",
            start_sec=1.0,
            end_sec=7.0,
            summary="사람의 머리 방향이 서로 다르게 보이는 후보",
            evidence=("2초에는 왼쪽, 6초에는 오른쪽을 향하는 것으로 보임",),
            uncertainty="표본 사이의 동작은 원본 확인 필요",
            source_frame_ids=("video-ui:2000", "video-ui:6000"),
            source_timestamps_sec=(2.0, 6.0),
        )

        text = self.window._segment_text(
            segment,
            "사람이 주변을 두리번거리는 장면 후보 찾기",
        )

        self.assertIn("시간 변화 표본 근거", text)
        self.assertIn("2개 관찰 시각", text)
        self.assertIn("중간 동작과 실제 반복 횟수는 원본 확인 필요", text)

    def test_top_three_motion_proposals_build_one_bounded_clustered_plan(self) -> None:
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:4b-instruct",),
            None,
            None,
            ("qwen3-vl:4b-instruct",),
        )
        local_index = self.window._mode_combo.findData(AnalysisMode.LOCAL)
        self.window._mode_combo.setCurrentIndex(local_index)
        self.window._purpose_edit.setPlainText("사람의 발과 물체 위치 변화가 보이는 장면 후보 찾기")
        proposals = tuple(
            MotionProposal(
                proposal_id=f"motion-{index}",
                video_id="video-ui",
                start_sec=start,
                end_sec=start + 2.0,
                peak_timestamp_sec=start + 1.0,
                motion_score=6.0 - index,
                recommended_sampling_interval_sec=0.5,
            )
            for index, start in enumerate((0.0, 3.0, 6.0, 8.0), start=1)
        )
        self.window._active_motion_scan_id = "scan-multi"
        self.window._on_motion_scan_result(
            MotionScanResult(
                scan_id="scan-multi",
                video_id="video-ui",
                range_start_sec=0.0,
                range_end_sec=10.0,
                sample_interval_sec=0.5,
                sampled_frame_count=19,
                proposals=proposals,
            )
        )

        self.assertEqual(len(self.window._checked_motion_proposals()), 3)
        self.assertEqual(self.window._selected_motion_list.count(), 3)
        self.assertIn("3/3", self.window._motion_selection_label.text())
        self.assertEqual(
            self.window._motion_list.item(3).checkState(),
            Qt.CheckState.Unchecked,
        )

        self.window._motion_list.item(3).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(len(self.window._checked_motion_proposals()), 3)
        self.assertEqual(
            self.window._motion_list.item(3).checkState(),
            Qt.CheckState.Unchecked,
        )

        self.window._selected_motion_list.setCurrentRow(1)
        self.window._uncheck_selected_motion_proposal()
        self.assertEqual(len(self.window._checked_motion_proposals()), 2)
        self.assertIn("2/3", self.window._motion_selection_label.text())
        self.window._motion_list.item(1).setCheckState(Qt.CheckState.Checked)
        self.window._apply_motion_proposal()
        self.window._make_plan()

        plan = self.window._session.plan
        self.assertEqual((plan.range_start_sec, plan.range_end_sec), (0.0, 8.0))
        self.assertEqual(len(plan.sample_timestamps_sec), 12)
        self.assertEqual(sum(0.0 <= value < 2.0 for value in plan.sample_timestamps_sec), 4)
        self.assertEqual(sum(3.0 <= value < 5.0 for value in plan.sample_timestamps_sec), 4)
        self.assertEqual(sum(6.0 <= value < 8.0 for value in plan.sample_timestamps_sec), 4)
        self.assertIn("움직임 후보 3개", self.window._plan_view.toPlainText())
        self.assertIn("등록된 분석 구간", self.window._plan_view.toPlainText())
        self.assertIn("1. 0.000~2.000초", self.window._plan_view.toPlainText())
        self.assertIn("2. 3.000~5.000초", self.window._plan_view.toPlainText())
        self.assertIn("3. 6.000~8.000초", self.window._plan_view.toPlainText())

    def test_stale_motion_scan_result_is_ignored(self) -> None:
        proposal = MotionProposal(
            proposal_id="motion-1",
            video_id="video-ui",
            start_sec=2.0,
            end_sec=8.0,
            peak_timestamp_sec=5.0,
            motion_score=4.2,
            recommended_sampling_interval_sec=0.5,
        )
        self.window._active_motion_scan_id = None

        self.window._on_motion_scan_result(
            MotionScanResult(
                scan_id="stale-scan",
                video_id="video-ui",
                range_start_sec=0.0,
                range_end_sec=10.0,
                sample_interval_sec=0.5,
                sampled_frame_count=19,
                proposals=(proposal,),
            )
        )

        self.assertEqual(self.window._motion_list.count(), 0)
        self.assertFalse(self.window._motion_apply_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
