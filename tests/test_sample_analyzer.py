from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event

from video_agent.analysis.sample import SampleAnalyzer
from video_agent.contracts import AnalysisLimits, AnalysisRequest, JobStatus
from video_agent.video.files import validate_video_path


class SampleAnalyzerTests(unittest.TestCase):
    def _request(self, path: Path, duration: float) -> AnalysisRequest:
        return AnalysisRequest(
            job_id="sample-test",
            plan_id="plan-test",
            approval_id="approval-test",
            video_id="video-test",
            video_path=path,
            purpose="테스트 목적",
            duration_seconds=duration,
        )

    def test_generated_segments_stay_inside_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.mp4"
            path.touch()
            analyzer = SampleAnalyzer(stage_delay_seconds=0)

            for duration in (0.2, 1.9, 2.0, 10.0, 7_200.0):
                with self.subTest(duration=duration):
                    result = analyzer.analyze(self._request(path, duration), Event(), lambda *_: None)
                    self.assertEqual(result.status, JobStatus.COMPLETED)
                    self.assertTrue(result.segments)
                    self.assertTrue(all(0 <= item.start_sec < item.end_sec <= duration for item in result.segments))

    def test_pre_canceled_request_returns_canceled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.mov"
            path.touch()
            cancel_event = Event()
            cancel_event.set()

            result = SampleAnalyzer(stage_delay_seconds=0).analyze(
                self._request(path, 10.0), cancel_event, lambda *_: None
            )

            self.assertEqual(result.status, JobStatus.CANCELED)
            self.assertFalse(result.segments)

    def test_segments_stay_inside_approved_subrange(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.mp4"
            path.touch()
            request = AnalysisRequest(
                job_id="sample-range",
                plan_id="plan-range",
                approval_id="approval-range",
                video_id="video-test",
                video_path=path,
                purpose="테스트 목적",
                duration_seconds=20.0,
                range_start_sec=5.0,
                range_end_sec=8.0,
            )
            result = SampleAnalyzer(stage_delay_seconds=0).analyze(request, Event(), lambda *_: None)
            self.assertTrue(all(5.0 <= item.start_sec < item.end_sec <= 8.0 for item in result.segments))

    def test_progress_reports_completed_sample_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.mp4"
            path.touch()
            progress_values: list[int | None] = []

            SampleAnalyzer(stage_delay_seconds=0).analyze(
                self._request(path, 10.0),
                Event(),
                lambda progress, _message: progress_values.append(progress),
            )

            self.assertEqual(progress_values, [0, 20, 40, 60, 80, 100])

    def test_runtime_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.mp4"
            path.touch()
            request = AnalysisRequest(
                job_id="sample-timeout",
                plan_id="plan-timeout",
                approval_id="approval-timeout",
                video_id="video-test",
                video_path=path,
                purpose="테스트 목적",
                duration_seconds=10.0,
                limits=AnalysisLimits(max_runtime_seconds=0.001),
            )

            with self.assertRaises(TimeoutError):
                SampleAnalyzer(stage_delay_seconds=0.01).analyze(request, Event(), lambda *_: None)

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            validate_video_path(Path("missing-video.mp4"))

    def test_unsupported_extension_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.touch()
            with self.assertRaises(ValueError):
                validate_video_path(path)


if __name__ == "__main__":
    unittest.main()
