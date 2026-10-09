from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from video_agent.contracts import AnalysisJob, AnalysisMode, JobStatus
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
        self.env.stop()
        self.temporary.cleanup()

    def test_analysis_cannot_run_before_approval_and_change_invalidates_it(self) -> None:
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 오래 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.assertFalse(self.window._run_button.isEnabled())

        self.window._approve_plan()
        self.assertTrue(self.window._run_button.isEnabled())

        self.window._purpose_edit.setPlainText("주차 차량 주변의 쓰러짐 장면 후보 찾기")
        self.assertFalse(self.window._run_button.isEnabled())
        self.assertIsNone(self.window._session.analysis_approval)

    def test_external_transfer_approval_is_separate(self) -> None:
        self.window._mode_combo.setCurrentIndex(self.window._mode_combo.findData(AnalysisMode.EXTERNAL))
        self.window._model_edit.setText("vision-model")
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 오래 머무르는 장면 후보 찾기")
        self.window._make_plan()
        self.window._approve_plan()

        self.assertFalse(self.window._run_button.isEnabled())
        self.assertTrue(self.window._transfer_button.isEnabled())

        self.window._approve_transfer()
        self.window._update_post_run_buttons()
        self.assertTrue(self.window._run_button.isEnabled())
        self.assertFalse(self.window._transfer_button.isEnabled())

    def test_late_result_after_cancel_is_ignored(self) -> None:
        self.window._purpose_edit.setPlainText("출입문 앞에 사람이 오래 머무르는 장면 후보 찾기")
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
        )

        self.window._on_analysis_result(late)

        self.assertEqual(self.window._result_list.count(), 0)
        self.assertEqual(self.window._progress_label.text(), "대기 중")

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


if __name__ == "__main__":
    unittest.main()
