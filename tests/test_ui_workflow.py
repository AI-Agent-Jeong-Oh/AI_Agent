from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QListWidgetItem,
    QStyledItemDelegate,
)

from video_agent.agent.situations import RECOMMENDED
from video_agent.contracts import (
    AnalysisJob,
    AnalysisMode,
    CaseRecord,
    ClueSource,
    FocusSituation,
    InformationCertainty,
    JobStatus,
    MotionProposal,
    MotionScanProfile,
    MotionScanResult,
    ResultOrigin,
    ReviewJudgement,
    SamplingStrategy,
    Segment,
)
from video_agent.agent.planning import WorkflowState
from video_agent.models.config import RuntimeInventory
from video_agent.storage.json_store import AppSettings, SettingsStore
from video_agent.ui.judgement_marks import JUDGEMENT_MARKS
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
                "VIDEO_AGENT_EXTERNAL_MODEL_SUPPORTS_VISION": "false",
                "OPENAI_API_KEY": "",
                "VIDEO_AGENT_EXTERNAL_API_KEY": "",
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

    def test_case_type_suggests_purpose_and_focus_flows_into_the_plan(self) -> None:
        window = self.window
        panel = window._case_panel
        panel.case_type.buttons["실종"].click()
        self.assertEqual(
            window._purpose_edit.toPlainText(), "실종 대상자의 마지막 확인 위치와 이동 경로 파악"
        )
        # The checks sit in step ② (the plan card) and follow the type chosen in step ①.
        self.assertEqual(
            window._focus_section.focus(), (FocusSituation.MOVEMENT, FocusSituation.STAY)
        )
        self.assertIs(window._focus_section.window(), window)
        self.assertFalse(panel.isAncestorOf(window._focus_section))
        self.assertTrue(window._plan_group.isAncestorOf(window._focus_section))
        window._purpose_edit.setPlainText("사람이 출입문을 지나가는 장면 후보 찾기")
        panel.case_type.buttons["폭행"].click()  # a written purpose is never replaced
        self.assertEqual(window._purpose_edit.toPlainText(), "사람이 출입문을 지나가는 장면 후보 찾기")

        window._update_post_run_buttons()
        window._make_plan()
        plan_text = window._plan_view.toPlainText()
        self.assertIn("우선 확인", plan_text)
        self.assertIn("인물 간 상호작용, 자세·움직임 변화", plan_text)
        # How the situations are used is one click away, under "세부 조건".
        self.assertIn("선택하지 않은 상황도 분석합니다", window._plan_details_view.toPlainText())
        window._approve_plan()
        self.assertTrue(window._run_button.isEnabled())

        window._focus_section.tiles[FocusSituation.ENVIRONMENT].check.click()
        self.assertFalse(window._run_button.isEnabled())
        self.assertIsNone(window._session.analysis_approval)

    def test_case_save_button_sits_in_the_bottom_bar_on_step_one(self) -> None:
        window = self.window
        save = window._case_panel.save_button
        self.assertTrue(window._step_navigation.isAncestorOf(save))
        self.assertFalse(window._case_panel.isAncestorOf(save))
        window._show_step(0)
        self.assertFalse(save.isHidden())
        window._show_step(1)
        self.assertTrue(save.isHidden())

    def test_new_case_name_starts_empty_and_is_needed_to_save(self) -> None:
        window = self.window
        panel = window._case_panel
        self.assertEqual(panel.title_edit.text(), "")
        self.assertEqual(window._case_title_label.text(), "새 사건")
        window._save_case()
        self.assertFalse(window._case_saved)
        self.assertFalse(panel.title_error.isHidden())
        panel.title_edit.setText("문 앞 택배 분실")
        window._save_case()
        self.assertTrue(window._case_saved)

    def test_case_focus_is_saved_and_restored(self) -> None:
        window = self.window
        panel = window._case_panel
        panel.case_type.buttons["아동학대"].click()
        panel.title_edit.setText("아동 확인")
        window._save_case()
        stored = window._case_store.load(window._case_record.case_id)
        self.assertEqual(stored.case_type, "아동학대")
        self.assertEqual(set(stored.focus_situations), set(RECOMMENDED["아동학대"]))
        self.assertEqual(stored.focus_hints, ())

        window._new_case()
        self.assertEqual(window._focus_section.focus(), ())
        panel.set_saved_cases((stored,), stored.case_id)
        window._load_case()
        self.assertEqual(panel.case_type_text(), "아동학대")
        self.assertEqual(set(window._focus_section.focus()), set(RECOMMENDED["아동학대"]))

    def test_older_search_hints_load_as_clues(self) -> None:
        window = self.window
        panel = window._case_panel
        older = CaseRecord(
            case_id="case-older",
            revision=1,
            title="예전 사건",
            case_type="아동학대",
            focus_situations=(FocusSituation.INTERACTION,),
            focus_hints=("노란색 상의 아동",),
        )
        window._case_store.save(older)
        panel.set_saved_cases((older,), older.case_id)
        window._load_case()
        (clue,) = panel.clues()
        self.assertEqual(clue.text, "노란색 상의 아동")
        self.assertIs(clue.source, ClueSource.OTHER)
        self.assertIs(clue.certainty, InformationCertainty.UNKNOWN)
        self.assertTrue(clue.use_for_analysis)
        self.assertEqual(window._focus_section.focus(), (FocusSituation.INTERACTION,))
        window._save_case()
        resaved = window._case_store.load(older.case_id)
        self.assertEqual(resaved.focus_hints, ())
        self.assertEqual([item.text for item in resaved.clues], ["노란색 상의 아동"])

    def test_case_clue_is_saved_shown_in_plan_and_change_requires_reapproval(self) -> None:
        panel = self.window._case_panel
        panel.title_edit.setText("문 앞 택배 이동 확인")
        panel.case_type.set_value("관리용 사건 분류")
        panel.clue_source.setCurrentIndex(panel.clue_source.findData(ClueSource.USER_STATEMENT))
        panel.clue_text.setText("신고자는 사람이 문 앞 상자를 들었다고 설명함")
        panel.add_clue_button.click()
        self.window._save_case()

        stored = self.window._case_store.load(self.window._case_record.case_id)
        self.assertEqual(stored.title, "문 앞 택배 이동 확인")
        self.assertEqual(len(stored.analysis_clues), 1)

        self.window._purpose_edit.setPlainText(
            "사람이 문 앞 상자를 들어 이동시키는 장면 후보 찾기"
        )
        self.window._make_plan()
        self.assertIn("영상에서 확인한 사실이 아닙니다", self.window._plan_view.toPlainText())
        self.assertIn("문 앞 상자를 들었다고 설명함", self.window._plan_view.toPlainText())
        self.window._approve_plan()
        self.assertTrue(self.window._run_button.isEnabled())

        item = panel.clue_list.item(0)
        item.setCheckState(Qt.CheckState.Unchecked)

        self.assertFalse(self.window._run_button.isEnabled())
        self.assertIsNone(self.window._session.analysis_approval)

    def test_editing_clue_source_keeps_it_in_plan_and_requires_reapproval(self) -> None:
        panel = self.window._case_panel
        panel.clue_source.setCurrentIndex(panel.clue_source.findData(ClueSource.USER_STATEMENT))
        panel.clue_text.setText("신고자는 문 앞 상자가 사라졌다고 설명함")
        panel.add_clue_button.click()
        self.window._purpose_edit.setPlainText(
            "사람이 문 앞 상자를 들어 이동시키는 장면 후보 찾기"
        )
        self.window._make_plan()
        self.window._approve_plan()
        self.assertTrue(self.window._run_button.isEnabled())

        panel.clue_list.setCurrentRow(0)
        panel.clue_source.setCurrentIndex(panel.clue_source.findData(ClueSource.DOCUMENT))
        panel.add_clue_button.click()

        self.assertFalse(self.window._run_button.isEnabled())
        self.assertIsNone(self.window._session.analysis_approval)
        self.window._make_plan()
        (clue,) = self.window._session.plan.analysis_clues
        self.assertIs(clue.source, ClueSource.DOCUMENT)
        self.assertIs(clue.certainty, InformationCertainty.UNKNOWN)
        self.assertIn("문 앞 상자가 사라졌다고 설명함 (출처: 문서)", self.window._plan_view.toPlainText())

    def test_status_line_shows_only_mode_and_selected_model(self) -> None:
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:8b-instruct", "qwen3-vl:4b-instruct", "kimi-k3:cloud"),
            None,
            None,
            ("qwen3-vl:8b-instruct", "qwen3-vl:4b-instruct", "kimi-k3:cloud"),
        )
        self.window._select_mode(AnalysisMode.LOCAL)
        self.window._set_model_id("qwen3-vl:8b-instruct")
        banner = self.window._mode_banner
        # Plain words on the line; program and model names only in the tooltip.
        self.assertEqual(banner.text(), "정확한 분석 · 이 컴퓨터에서 처리")
        self.assertIn("qwen3-vl:8b-instruct", banner.toolTip())
        self.assertIn("자동 전환하지 않습니다", banner.toolTip())

        self.window._set_model_id("qwen3-vl:4b-instruct")
        self.assertEqual(banner.text(), "빠른 분석 · 이 컴퓨터에서 처리")
        self.window._set_model_id("kimi-k3:cloud")
        self.assertEqual(banner.text(), "외부 서버에서 실행되는 프로그램 · 이 컴퓨터 분석에 쓸 수 없음")
        self.assertIn("외부 서버", banner.toolTip())

        self.window._select_mode(AnalysisMode.EXTERNAL)
        self.window._set_model_id("gpt-vision-test")
        self.assertEqual(banner.text(), "외부 AI 분석 · 영상 일부를 외부로 보냄")
        self.assertIn("gpt-vision-test", banner.toolTip())
        self.assertIn("외부 전송 승인", banner.toolTip())
        self.window._select_mode(AnalysisMode.SAMPLE)
        self.assertEqual(banner.text(), "시연용 결과 (실제 AI 분석 아님)")

    def test_step_indicator_is_compact_and_names_each_state(self) -> None:
        self.window.resize(1440, 900)
        self.window.show()
        self.window._show_step(1)
        self.app.processEvents()
        indicator = self.window._step_indicator
        self.assertLessEqual(indicator.height(), 48)
        self.assertEqual(
            [button.accessibleDescription() for button in self.window._step_buttons],
            ["지난 단계", "현재 단계", "다음 단계"],
        )
        centers = [button.geometry().center().x() for button in self.window._step_buttons]
        self.assertLessEqual(centers[-1] - centers[0], 2 * 260)

    def test_step_two_is_one_card_in_three_numbered_parts(self) -> None:
        window = self.window
        find_part, range_part, plan_part = window._plan_parts
        self.assertEqual(
            [part.title_label.text() for part in window._plan_parts],
            ["무엇을 찾을지", "볼 구간", "계획 확인·실행"],
        )
        self.assertTrue(find_part.isAncestorOf(window._purpose_edit))
        self.assertTrue(find_part.isAncestorOf(window._focus_section))
        self.assertTrue(range_part.isAncestorOf(window._motion_scan_button))
        self.assertTrue(range_part.isAncestorOf(window._range_start))
        self.assertTrue(plan_part.isAncestorOf(window._analysis_choice))
        self.assertTrue(plan_part.isAncestorOf(window._plan_view))
        # The left side of step ② is the video list and player, as in steps ① and ③.
        self.assertFalse(window._video_stack.parentWidget().isAncestorOf(window._purpose_edit))
        # One part open at a time; folded parts show what was chosen.
        self.assertTrue(find_part.is_open())
        self.assertFalse(range_part.is_open())
        range_part.header.clicked.emit()
        self.assertTrue(range_part.is_open())
        self.assertFalse(find_part.is_open())
        range_part.header.clicked.emit()  # ⌄ on an open part folds it
        self.assertFalse(range_part.is_open())
        range_part.header.clicked.emit()
        self.assertTrue(range_part.is_open())
        self.assertIn("상황", find_part.summary_label.text())
        self.assertEqual(range_part.summary_label.text(), "")
        self.assertIn("계획 없음", plan_part.summary_label.text())
        # No empty plan box and no idle progress line before there is a plan.
        self.assertTrue(window._plan_view.isHidden())
        self.assertTrue(window._progress_bar.isHidden())
        self.assertTrue(window._progress_label.isHidden())
        window.resize(1440, 900)
        window.show()
        window._show_step(1)
        self.app.processEvents()
        self.assertGreaterEqual(window._video_stack.height(), 288)

    def test_step_two_shows_one_next_action_at_a_time(self) -> None:
        window = self.window
        window._update_post_run_buttons()
        window._purpose_edit.setPlainText("사람이 문 앞 상자를 들어 이동시키는 장면 후보 찾기")

        def blue() -> list:
            return [b for b in window._next_action_buttons if b.property("variant") == "primary"]

        def shown() -> list:
            buttons = (window._make_plan_button, window._approve_button, window._run_button)
            return [button for button in buttons if not button.isHidden()]

        self.assertEqual(blue(), [window._motion_scan_button])
        self.assertEqual(shown(), [window._make_plan_button])
        window._make_plan()
        self.assertEqual(shown(), [window._approve_button])
        self.assertEqual(blue(), [window._approve_button])
        window._approve_plan()
        self.assertEqual(shown(), [window._run_button])
        self.assertEqual(blue(), [window._run_button])
        # A changed purpose needs a new plan and a new approval.
        window._purpose_edit.setPlainText("사람이 상자를 옮기는 장면 후보 찾기")
        self.assertEqual(shown(), [window._make_plan_button])
        self.assertFalse(window._run_button.isEnabled())

    def test_one_click_approves_and_runs_a_local_plan(self) -> None:
        window = self.window
        window._purpose_edit.setPlainText("사람이 문 앞 상자를 들어 이동시키는 장면 후보 찾기")
        window._make_plan()
        self.assertEqual(window._approve_button.text(), "승인하고 실행")
        rows = window._plan_view.toPlainText()
        for label in ("찾을 장면", "볼 영상", "우선 확인", "사용 단서", "분석 방식"):
            self.assertIn(label, rows)
        for detail in ("한도", "표본", "범위 확대"):
            self.assertNotIn(detail, rows)
        self.assertFalse(window._plan_details_toggle.isHidden())
        self.assertTrue(window._plan_details_view.isHidden())
        with patch.object(window, "_start_analysis") as start:
            window._approve_button.click()
        # The click is the approval of the plan shown, then the run starts.
        self.assertIsNotNone(window._session.analysis_approval)
        start.assert_called_once()

    def test_every_drop_down_uses_the_styled_list(self) -> None:
        combos = self.window.findChildren(QComboBox)
        self.assertGreaterEqual(len(combos), 5)
        for combo in combos:
            self.assertIsInstance(combo.itemDelegate(), QStyledItemDelegate)

    def test_one_analysis_choice_and_external_rows_only_when_needed(self) -> None:
        window = self.window
        window._select_mode(AnalysisMode.SAMPLE)
        self.assertFalse(window._analysis_choice.isHidden())
        for widget in (window._external_model_edit, window._external_api_key_edit):
            self.assertTrue(widget.isHidden())
        window._select_mode(AnalysisMode.EXTERNAL)
        for widget in (
            window._external_model_edit,
            window._external_api_key_edit,
            window._external_vision_confirm,
        ):
            self.assertFalse(widget.isHidden())

    def test_changing_checked_candidates_after_a_plan_requires_a_new_plan(self) -> None:
        window = self.window
        window._update_post_run_buttons()
        window._purpose_edit.setPlainText("사람이 문 앞 상자를 들어 이동시키는 장면 후보 찾기")
        proposals = tuple(
            MotionProposal(
                proposal_id=f"motion-{index}", video_id="video-ui", start_sec=start,
                end_sec=start + 2.0, peak_timestamp_sec=start + 1.0, motion_score=5.0 - index,
                recommended_sampling_interval_sec=0.5,
            )
            for index, start in enumerate((1.0, 6.0))
        )
        window._active_motion_scan_id = "scan-two"
        window._on_motion_scan_result(
            MotionScanResult(
                scan_id="scan-two", video_id="video-ui", range_start_sec=0.0,
                range_end_sec=10.0, sample_interval_sec=0.5, sampled_frame_count=19,
                proposals=proposals,
            )
        )
        window._make_plan()
        self.assertEqual((window._session.plan.range_start_sec, window._session.plan.range_end_sec), (1.0, 8.0))
        self.assertTrue(window._approve_button.isEnabled())

        window._motion_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(window._approve_button.isEnabled())
        self.assertFalse(window._make_plan_button.isHidden())
        window._make_plan()
        self.assertTrue(all(1.0 <= value <= 3.0 for value in window._session.plan.sample_timestamps_sec))

        # With nothing checked the plan goes back to the range the user had set.
        window._motion_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        window._make_plan()
        self.assertEqual((window._range_start.value(), window._range_end.value()), (0.0, 0.0))
        self.assertEqual(window._applied_motion_proposals, ())

    def test_motion_guidance_is_short_and_split_by_importance(self) -> None:
        window = self.window
        self.assertEqual(
            window._motion_help.text(),
            "영상 내 움직임이 큰 구간을 후보로 제안합니다. 최대 3개까지 선택할 수 있습니다.",
        )
        self.assertEqual(window._motion_note.text(), "움직임 구간은 AI의 행동 판정 결과가 아닙니다.")
        self.assertEqual(window._motion_note.objectName(), "subsectionHelp")

    def test_motion_candidates_show_short_empty_states(self) -> None:
        window = self.window
        self.assertEqual(window._motion_empty_label.text(), "등록된 CCTV 영상이 없습니다.")
        self.assertTrue(window._motion_list.isHidden())

        window._case_videos[window._video_id] = self.video
        window._refresh_motion_empty_state()
        # Before a scan nothing is said; the button is the next step.
        self.assertTrue(window._motion_empty_label.isHidden())
        result = dict(
            video_id="video-ui", range_start_sec=0.0, range_end_sec=10.0,
            sample_interval_sec=0.5, sampled_frame_count=19,
        )
        window._active_motion_scan_id = "scan-empty"
        window._on_motion_scan_result(MotionScanResult(scan_id="scan-empty", proposals=(), **result))
        self.assertEqual(
            window._motion_empty_label.text(), "이 범위에는 움직임 구간이 없습니다."
        )
        self.assertTrue(window._motion_list.isHidden())

        proposal = MotionProposal(
            proposal_id="motion-1", video_id="video-ui", start_sec=2.0, end_sec=8.0,
            peak_timestamp_sec=5.0, motion_score=4.2, recommended_sampling_interval_sec=0.5,
        )
        window._active_motion_scan_id = "scan-found"
        window._on_motion_scan_result(
            MotionScanResult(scan_id="scan-found", proposals=(proposal,), **result)
        )
        self.assertTrue(window._motion_empty_label.isHidden())
        self.assertFalse(window._motion_list.isHidden())
        self.assertFalse(window._motion_selection_label.isHidden())
        self.assertEqual(window._motion_list.item(0).checkState(), Qt.CheckState.Checked)

        window._range_changed()  # inputs changed: proposals cleared, to be found again
        self.assertTrue(window._motion_list.isHidden())
        self.assertEqual(
            window._motion_empty_label.text(), "범위가 바뀌었습니다. 움직임 구간을 다시 찾아 주세요."
        )

    def test_analysis_panel_preserves_readable_motion_and_plan_areas(self) -> None:
        self.assertGreaterEqual(self.window._motion_list.minimumHeight(), 120)
        self.assertGreaterEqual(self.window._plan_view.minimumHeight(), 140)
        self.assertGreaterEqual(self.window._plan_group.minimumHeight(), 450)
        self.assertIs(self.window._step_scrolls[1].widget(), self.window._plan_group.parentWidget())

    def test_ui_zoom_scales_controls_and_is_bounded(self) -> None:
        base_size = QFontInfo(self.window.font()).pixelSize()
        card_text_size = QFontInfo(self.window._metadata_label.font()).pixelSize()

        self.window._set_ui_zoom(130)
        self.app.processEvents()
        self.assertGreater(QFontInfo(self.window._metadata_label.font()).pixelSize(), card_text_size)

        self.assertEqual(self.window._ui_zoom_percent, 130)
        self.assertEqual(self.window._zoom_reset_action.text(), "기본 크기 (현재 130%)")
        self.assertGreater(QFontInfo(self.window.font()).pixelSize(), base_size)
        self.assertGreaterEqual(self.window._motion_list.minimumHeight(), 166)

        self.window._set_ui_zoom(999)
        self.assertEqual(self.window._ui_zoom_percent, self.window.UI_ZOOM_MAX)
        self.assertFalse(self.window._zoom_in_action.isEnabled())

        self.window._set_ui_zoom(-1)
        self.assertEqual(self.window._ui_zoom_percent, self.window.UI_ZOOM_MIN)
        self.assertFalse(self.window._zoom_out_action.isEnabled())

        self.window._set_ui_zoom(100)
        self.assertEqual(QFontInfo(self.window.font()).pixelSize(), base_size)

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
        self.window._select_mode(AnalysisMode.EXTERNAL)
        self.window._set_model_id("vision-model")
        self.window._external_vision_confirm.setChecked(True)
        self.window._external_api_key_edit.setText("sk-test-not-real")
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()

        self.assertFalse(self.window._run_button.isEnabled())
        self.assertTrue(self.window._transfer_button.isEnabled())

        self.window._approve_transfer()
        self.window._update_post_run_buttons()
        self.assertTrue(self.window._run_button.isEnabled())
        self.assertFalse(self.window._transfer_button.isEnabled())

    def test_openai_key_is_masked_memory_only_and_required_for_external_run(self) -> None:
        self.window._select_mode(AnalysisMode.EXTERNAL)
        self.window._set_model_id("vision-model")
        self.window._external_vision_confirm.setChecked(True)
        self.window._purpose_edit.setPlainText(
            "출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기"
        )
        self.window._make_plan()
        self.window._approve_plan()
        self.window._approve_transfer()

        self.assertEqual(
            self.window._external_api_key_edit.echoMode(),
            QLineEdit.EchoMode.Password,
        )
        self.assertFalse(self.window._run_button.isEnabled())

        secret = "sk-test-memory-only"
        self.window._external_api_key_edit.setText(secret)

        self.assertTrue(self.window._run_button.isEnabled())
        settings_text = Path(os.environ["VIDEO_AGENT_DATA_DIR"]).joinpath(
            "settings.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn(secret, settings_text)
        self.assertIn("OPENAI_API_KEY", settings_text)

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
        self.window._result_list.setCurrentRow(0)

        text = self.window._review_panel.detail_view.toPlainText()
        self.assertIn("확인용 재생 범위", text)
        self.assertIn("재생 시작\n00:00 (확인용 범위 시작 최대 1초 전", text)
        self.assertIn("관찰 프레임 원본 시각\n2.000초", text)

    def test_playback_bar_marks_candidates_and_a_click_selects_one(self) -> None:
        window = self.window
        window._duration_ms = 10_000
        window._timeline.setRange(0, 10_000)
        segments = tuple(
            Segment(
                video_id="video-ui",
                start_sec=start,
                end_sec=start + 1.0,
                summary=f"{start}초 후보",
                evidence=("관찰됨",),
                uncertainty="원본 확인 필요",
                source_frame_ids=(f"video-ui:{int(start * 1000)}",),
                source_timestamps_sec=(start,),
            )
            for start in (2.0, 6.0)
        )
        job = AnalysisJob(
            job_id="job-marks",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-ui",
            mode=AnalysisMode.LOCAL,
            model_id="fake-vlm",
            is_sample=False,
            origin=ResultOrigin.TEST_DOUBLE,
            purpose="사람이 문을 지나가는 장면 후보 찾기",
            segments=segments,
        )
        window._display_segments(job)
        marks = window._timeline.marks
        self.assertEqual([(mark.start_ms, mark.end_ms) for mark in marks], [(2000, 3000), (6000, 7000)])
        self.assertIn("후보 #1", marks[0].label)
        self.assertIn("미검토", marks[0].label)
        # Unreviewed AI candidates are outlines, as on the demo page, and the legend names them.
        self.assertIs(marks[0].style, JUDGEMENT_MARKS[ReviewJudgement.UNREVIEWED])
        self.assertTrue(marks[0].style.dashed)
        self.assertFalse(window._timeline_legend.isHidden())

        with patch.object(window, "_seek_to_segment") as seek:
            window._timeline_mark_clicked(1)
        self.assertEqual(window._review_panel.current_index, 1)
        seek.assert_called_once()
        self.assertTrue(window._timeline.marks[1].selected)

        # A reviewer's saved judgement recolours the mark.
        window._review_panel.judgement_buttons[ReviewJudgement.RELEVANT].setChecked(True)
        window._review_panel.bookmark_check.setChecked(True)
        window._review_panel.save_current()
        self.assertIn("관련 있음", window._timeline.marks[1].label)
        self.assertIs(window._timeline.marks[1].style, JUDGEMENT_MARKS[ReviewJudgement.RELEVANT])
        self.assertTrue(window._timeline.marks[1].bookmarked)
        self.assertIn("북마크", window._timeline.marks[1].label)

        # Another video's playback bar shows no marks for this result.
        window._video_id = "video-other"
        window._refresh_timeline_marks()
        self.assertEqual(window._timeline.marks, ())
        self.assertTrue(window._timeline_legend.isHidden())

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
        self.window._video_id = "video-ui"
        self.window._video_path = self.video
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

    def local_inventory(self) -> RuntimeInventory:
        models = ("qwen3-vl:8b-instruct", "qwen3-vl:4b-instruct", "kimi-k3:cloud", "glm-5.3:cloud")
        return RuntimeInventory("/opt/homebrew/bin/ollama", models, None, None, models[:3])

    def test_local_models_are_listed_by_name_without_cloud_models(self) -> None:
        window = self.window
        window._runtime_inventory = self.local_inventory()
        window._settings = AppSettings()
        window._select_mode(AnalysisMode.LOCAL)
        choice = window._analysis_choice
        labels = [choice.itemText(index) for index in range(choice.count())]
        self.assertEqual(
            labels,
            ["빠른 분석", "정확한 분석", "시연용 (실제 분석 아님)", "외부 AI 분석 (영상 일부를 외부로 보냄)"],
        )
        # "정확한 분석" (the largest model) is the default.
        self.assertEqual(window._model_id(), "qwen3-vl:8b-instruct")
        self.assertEqual(window._mode_banner.text(), "정확한 분석 · 이 컴퓨터에서 처리")

        choice.setCurrentIndex(0)
        self.assertEqual(window._model_id(), "qwen3-vl:4b-instruct")
        self.assertEqual(window._mode_banner.text(), "빠른 분석 · 이 컴퓨터에서 처리")

    def test_local_mode_refuses_a_cloud_model_and_can_reread_models(self) -> None:
        window = self.window
        window._runtime_inventory = self.local_inventory()
        window._select_mode(AnalysisMode.LOCAL)
        window._set_model_id("kimi-k3:cloud")
        self.assertEqual(window._analysis_choice.currentText(), "AI 분석 (확인되지 않은 프로그램)")
        window._purpose_edit.setPlainText("사람이 출입문을 지나가는 장면 후보 찾기")
        with patch.object(window, "_show_error") as error:
            window._make_plan()
        self.assertIn("외부 서버에서 실행", error.call_args.args[0])
        self.assertIsNone(window._session.plan)

        newer = RuntimeInventory(
            "/opt/homebrew/bin/ollama", ("qwen3-vl:2b-instruct",), None, None, ("qwen3-vl:2b-instruct",)
        )
        with patch("video_agent.ui.main_window.RuntimeInventory.detect", return_value=newer):
            window._model_refresh_button.click()
        self.assertEqual(window._analysis_choice.itemText(0), "AI 분석")

    def test_first_run_starts_with_local_analysis_when_installed(self) -> None:
        self.runtime.stop()
        fresh = Path(self.temporary.name) / "fresh"
        with patch(
            "video_agent.ui.main_window.RuntimeInventory.detect",
            return_value=self.local_inventory(),
        ), patch.dict(os.environ, {"VIDEO_AGENT_DATA_DIR": str(fresh)}):
            first = MainWindow()
        self.runtime.start()
        self.assertIs(first._current_mode(), AnalysisMode.LOCAL)
        self.assertEqual(first._analysis_choice.currentText(), "정확한 분석")
        first.close()
        # Without an installed program the first run stays on the demonstration.
        self.assertIs(self.window._current_mode(), AnalysisMode.SAMPLE)

    def test_every_start_uses_accurate_analysis_whatever_was_saved(self) -> None:
        self.runtime.stop()
        data = Path(self.temporary.name) / "saved-demo"
        SettingsStore(data / "settings.json").save(
            AppSettings(mode=AnalysisMode.SAMPLE, local_model_id="qwen3-vl:4b-instruct")
        )
        with patch(
            "video_agent.ui.main_window.RuntimeInventory.detect",
            return_value=self.local_inventory(),
        ), patch.dict(os.environ, {"VIDEO_AGENT_DATA_DIR": str(data)}):
            reopened = MainWindow()
        self.runtime.start()
        self.assertEqual(reopened._settings_store.path, data / "settings.json")
        self.assertIs(reopened._current_mode(), AnalysisMode.LOCAL)
        self.assertEqual(reopened._analysis_choice.currentText(), "정확한 분석")
        self.assertEqual(reopened._model_id(), "qwen3-vl:8b-instruct")
        reopened.close()

    def test_switching_modes_preserves_each_explicit_model_setting(self) -> None:
        self.window._select_mode(AnalysisMode.LOCAL)
        self.window._set_model_id("local-vision-model")
        self.window._select_mode(AnalysisMode.EXTERNAL)
        self.window._set_model_id("external-vision-model")

        self.window._select_mode(AnalysisMode.LOCAL)
        self.assertEqual(self.window._model_id(), "local-vision-model")
        self.window._select_mode(AnalysisMode.EXTERNAL)
        self.assertEqual(self.window._model_id(), "external-vision-model")

    def test_local_mode_selects_verified_instruct_model_and_uses_local_limit(self) -> None:
        self.window._runtime_inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:8b", "qwen3-vl:4b-instruct"),
            None,
            None,
            ("qwen3-vl:8b", "qwen3-vl:4b-instruct"),
        )
        self.window._settings = AppSettings()
        self.window._select_mode(AnalysisMode.LOCAL)
        self.window._purpose_edit.setPlainText("사람과 유모차가 강하게 접촉하는 장면 후보 찾기")

        self.window._make_plan()

        self.assertEqual(self.window._model_id(), "qwen3-vl:4b-instruct")
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
        self.window._select_mode(AnalysisMode.LOCAL)
        self.window._purpose_edit.setPlainText(
            "사람의 손가락이 초인종 버튼을 반복해서 누르는 장면 후보 찾기"
        )

        self.window._make_plan()

        plan = self.window._session.plan
        self.assertEqual(plan.sampling_strategy, SamplingStrategy.TEMPORAL_SEQUENCE)
        plan_text = self.window._plan_details_view.toPlainText()
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
        self.window._select_mode(AnalysisMode.LOCAL)
        self.window._set_model_id("qwen3-vl:8b")
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
            "AI 분석 실패",
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
        self.window._select_mode(AnalysisMode.LOCAL)
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
        # The entered range stays as typed; the plan takes the checked range.
        self.assertEqual((self.window._range_start.value(), self.window._range_end.value()), (0.0, 0.0))
        self.assertEqual(self.window._intent_range(), (2.0, 8.0))
        self.assertFalse(self.window._run_button.isEnabled())

        self.window._make_plan()

        self.assertEqual(len(self.window._session.plan.sample_timestamps_sec), 12)
        self.assertAlmostEqual(self.window._session.plan.sampling_interval_sec, 0.25)
        self.assertIn("최대 변화 시각 중심", self.window._plan_details_view.toPlainText())
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
        self.window._select_mode(AnalysisMode.LOCAL)
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
        self.window._make_plan()

        plan = self.window._session.plan
        self.assertEqual(plan.sampling_strategy, SamplingStrategy.TEMPORAL_SEQUENCE)
        self.assertEqual(len(plan.sample_timestamps_sec), 12)
        self.assertLess(plan.sample_timestamps_sec[0], 2.5)
        self.assertGreater(plan.sample_timestamps_sec[-1], 7.5)
        self.assertTrue(plan.observation_guidance)
        plan_text = self.window._plan_details_view.toPlainText()
        self.assertIn("각 구간 전체에 분산한 시간순 비교", plan_text)
        self.assertIn("관찰 기준", plan_text)

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
        self.window._select_mode(AnalysisMode.LOCAL)
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

        self.window._motion_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(len(self.window._checked_motion_proposals()), 2)
        self.assertIn("2/3", self.window._motion_selection_label.text())
        self.window._motion_list.item(1).setCheckState(Qt.CheckState.Checked)
        # No separate apply step: making the plan uses the checked candidates.
        self.window._make_plan()

        plan = self.window._session.plan
        self.assertEqual((plan.range_start_sec, plan.range_end_sec), (0.0, 8.0))
        self.assertEqual(len(plan.sample_timestamps_sec), 12)
        self.assertEqual(sum(0.0 <= value < 2.0 for value in plan.sample_timestamps_sec), 4)
        self.assertEqual(sum(3.0 <= value < 5.0 for value in plan.sample_timestamps_sec), 4)
        self.assertEqual(sum(6.0 <= value < 8.0 for value in plan.sample_timestamps_sec), 4)
        # The plan names the ranges in minutes:seconds; sampling detail is under "세부 조건".
        self.assertIn("움직임 구간 3개: 0:00–0:02, 0:03–0:05, 0:06–0:08", self.window._plan_view.toPlainText())
        details = self.window._plan_details_view.toPlainText()
        self.assertIn("등록된 분석 구간", details)
        self.assertIn("1. 0.000~2.000초", details)
        self.assertIn("2. 3.000~5.000초", details)
        self.assertIn("3. 6.000~8.000초", details)

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
        self.assertEqual(self.window._checked_motion_proposals(), ())

    def finish_run(
        self,
        start: float = 2.0,
        end: float = 5.0,
        *,
        status: JobStatus = JobStatus.COMPLETED,
        origin: ResultOrigin = ResultOrigin.TEST_DOUBLE,
        segments: tuple[Segment, ...] = (),
    ) -> None:
        """Approve a plan for the range and deliver a crafted result for it."""
        window = self.window
        window._range_start.setValue(start)
        window._range_end.setValue(end)
        window._purpose_edit.setPlainText("출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기")
        window._make_plan()
        window._approve_plan()
        request = window._session.create_request(str(self.video), f"job-{start}-{end}")
        sample = origin is ResultOrigin.SAMPLE
        job = AnalysisJob(
            job_id=request.job_id,
            plan_id=request.plan_id,
            status=status,
            progress=None if status is JobStatus.PARTIAL else 100,
            video_id="video-ui",
            mode=AnalysisMode.SAMPLE if sample else AnalysisMode.LOCAL,
            model_id=None if sample else "fake-vlm",
            is_sample=sample,
            origin=origin,
            purpose=request.purpose,
            range_start_sec=start,
            range_end_sec=end,
            requested_frame_count=12,
            analyzed_frame_count=6 if status is JobStatus.PARTIAL else 12,
            error="2번째 배치 시간 초과" if status is JobStatus.PARTIAL else None,
            segments=segments,
        )
        window._on_analysis_result(job)

    def expansion_shown(self) -> bool:
        return not self.window._expansion_frame.isHidden()

    def test_empty_result_over_part_of_video_proposes_expansion_without_running(self) -> None:
        self.finish_run(2.0, 5.0)

        self.assertTrue(self.expansion_shown())
        text = self.window._expansion_label.text()
        self.assertIn("분석 범위 00:02–00:05(영상 00:10 중 00:03)에서 후보를 찾지 못했습니다.", text)
        self.assertIn("프레임 사이 간격이 넓어집니다", text)
        self.assertEqual(self.window._session.state, WorkflowState.EXPANSION_PROPOSED)

        self.window._expansion_apply_button.click()

        self.assertFalse(self.expansion_shown())
        self.assertEqual(self.window._range_start.value(), 0)
        self.assertEqual(self.window._range_end.value(), 0)  # "영상 끝"
        self.assertIsNone(self.window._session.plan)
        self.assertIsNone(self.window._session.analysis_approval)
        self.assertFalse(self.window._approve_button.isEnabled())
        self.assertFalse(self.window._run_button.isEnabled())
        self.assertIsNone(self.window._worker)
        self.assertIsNone(self.window._session.active_job_id)
        self.assertEqual(self.window._step_stack.currentIndex(), 1)
        self.assertIn("다시 만들고 승인해야 실행됩니다", self.window._status_label.text())

    def test_empty_result_from_motion_windows_proposes_expansion(self) -> None:
        self.window._applied_motion_proposals = (
            MotionProposal(
                proposal_id="motion-1",
                video_id="video-ui",
                start_sec=2.0,
                end_sec=8.0,
                peak_timestamp_sec=5.0,
                motion_score=4.2,
                recommended_sampling_interval_sec=0.5,
            ),
        )
        # Set after the range change, which clears applied proposals; no checks to sync.
        with (
            patch.object(self.window, "_range_changed", self.window._inputs_changed),
            patch.object(self.window, "_sync_motion_selection", return_value=True),
        ):
            self.finish_run(0.0, 10.0)

        self.assertTrue(self.expansion_shown())
        self.assertIn("선택한 움직임 구간 1개(", self.window._expansion_label.text())

    def test_no_expansion_when_widening_would_not_help(self) -> None:
        candidate = Segment(
            video_id="video-ui",
            start_sec=3.0,
            end_sec=4.0,
            summary="사람이 문 앞에 서 있음",
            evidence=("3.5초 프레임에서 관찰됨",),
            uncertainty="원본 확인 필요",
            source_frame_ids=("video-ui:3500",),
            source_timestamps_sec=(3.5,),
        )
        cases = {
            "whole video": dict(start=0.0, end=10.0),
            "candidates found": dict(segments=(candidate,)),
            "partial": dict(status=JobStatus.PARTIAL),
            "sample": dict(origin=ResultOrigin.SAMPLE),
        }
        for name, changes in cases.items():
            with self.subTest(name):
                self.window._session = type(self.window._session)()
                with patch.object(self.window, "_show_error"):
                    self.finish_run(**{"start": 2.0, "end": 5.0, **changes})
                self.assertFalse(self.expansion_shown())

    def test_expansion_closes_on_dismiss_or_input_change_only(self) -> None:
        self.finish_run(2.0, 5.0)
        self.window._inputs_changed()  # unrelated refresh with the same inputs
        self.assertTrue(self.expansion_shown())
        self.window._expansion_close_button.click()
        self.assertFalse(self.expansion_shown())

        self.finish_run(3.0, 6.0)
        self.assertTrue(self.expansion_shown())
        self.window._purpose_edit.setPlainText("주차 차량 주변의 쓰러짐 장면 후보 찾기")
        self.assertFalse(self.expansion_shown())


if __name__ == "__main__":
    unittest.main()
