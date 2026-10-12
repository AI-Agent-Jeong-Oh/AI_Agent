from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event

from video_agent.analysis.batch import (
    BatchExecutionWorker,
    BatchPreparationWorker,
    BatchVideoInput,
)
from video_agent.contracts import (
    AnalysisJob,
    AnalysisLimits,
    AnalysisMode,
    JobStatus,
    MotionProposal,
    MotionScanResult,
    ResultOrigin,
)
from video_agent.video.probe import VideoMetadata


def metadata(video_id: str) -> VideoMetadata:
    return VideoMetadata(
        video_id=video_id,
        duration_seconds=30.0,
        coded_width=640,
        coded_height=360,
        display_width=640,
        display_height=360,
        rotation_degrees=0,
        average_frame_rate=24.0,
        nominal_frame_rate=24.0,
        variable_frame_rate_detected=False,
        video_codec="h264",
        has_audio=False,
        start_time_seconds=0.0,
        stream_time_base="1/12288",
        file_size_bytes=100,
    )


class BatchProbe:
    def __init__(self, ids_by_path: dict[Path, str]) -> None:
        self.ids_by_path = ids_by_path
        self.calls: list[Path] = []

    def probe(self, path: Path, _cancel: Event) -> VideoMetadata:
        self.calls.append(path)
        return metadata(self.ids_by_path[path])


class BatchScanner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def scan(
        self,
        scan_id,
        _path,
        video_metadata,
        start,
        end,
        _cancel,
        profile,
    ) -> MotionScanResult:
        self.calls.append(video_metadata.video_id)
        proposals = tuple(
            MotionProposal(
                proposal_id=f"{video_metadata.video_id}-{index}",
                video_id=video_metadata.video_id,
                start_sec=value,
                end_sec=value + 2.0,
                peak_timestamp_sec=value + 1.0,
                motion_score=10.0 - index,
                recommended_sampling_interval_sec=0.5,
            )
            for index, value in enumerate((2.0, 10.0, 20.0, 25.0))
        )
        return MotionScanResult(
            scan_id=scan_id,
            video_id=video_metadata.video_id,
            range_start_sec=start,
            range_end_sec=end,
            sample_interval_sec=0.5,
            sampled_frame_count=60,
            proposals=proposals,
            profile=profile,
        )


class CompletedAnalyzer:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    def analyze(self, request, _cancel, progress) -> AnalysisJob:
        self.order.append(request.video_id)
        progress(None, "모델 분석")
        return AnalysisJob(
            job_id=request.job_id,
            plan_id=request.plan_id,
            status=JobStatus.COMPLETED,
            progress=100,
            video_id=request.video_id,
            mode=request.mode,
            model_id=request.model_id,
            is_sample=False,
            origin=ResultOrigin.REAL_MODEL,
            purpose=request.purpose,
            range_start_sec=request.range_start_sec,
            range_end_sec=request.range_end_sec,
            sample_timestamps_sec=request.sample_timestamps_sec,
            requested_frame_count=len(request.sample_timestamps_sec),
            analyzed_frame_count=len(request.sample_timestamps_sec),
            sampling_strategy=request.sampling_strategy,
            observation_guidance=request.observation_guidance,
            case_id=request.case_id,
            case_revision=request.case_revision,
            analysis_clues=request.analysis_clues,
        )


class BatchAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.videos = tuple(
            BatchVideoInput(f"video-{index}", root / f"cam-{index}.mp4", f"카메라 {index}")
            for index in range(5)
        )
        for video in self.videos:
            video.path.write_bytes(b"video")
        self.probe = BatchProbe({item.path: item.video_id for item in self.videos})
        self.scanner = BatchScanner()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare(self):
        worker = BatchPreparationWorker(
            self.videos,
            purpose="출입문 앞 물체를 들고 이동하는 장면 후보 확인",
            mode=AnalysisMode.LOCAL,
            model_id="vision-model",
            provider_id="ollama-local",
            provider_endpoint="http://127.0.0.1:11434/api/chat",
            case_id="case-batch",
            case_revision=1,
            analysis_clues=(),
            focus_situations=(),
            limits=AnalysisLimits(
                max_runtime_seconds=300.0,
                max_provider_requests=3,
                max_frames_per_request=4,
            ),
            probe=self.probe,
            scanner=self.scanner,
        )
        prepared = []
        worker.preparation_ready.connect(prepared.append)
        worker.run()
        self.assertEqual(len(prepared), 1)
        return prepared[0]

    def test_prepares_more_than_three_videos_sequentially_without_model_calls(self) -> None:
        preparation = self.prepare()

        self.assertFalse(preparation.failures)
        self.assertEqual(len(preparation.prepared), 5)
        self.assertEqual(self.probe.calls, [item.path for item in self.videos])
        self.assertEqual(self.scanner.calls, [item.video_id for item in self.videos])
        for item in preparation.prepared:
            self.assertEqual(len(item.selected_proposals), 3)
            self.assertEqual(item.plan.model_id, "vision-model")
            self.assertTrue(item.plan.sample_timestamps_sec)
            self.assertEqual(item.plan.case_id, "case-batch")

    def test_executes_prepared_video_plans_in_queue_order(self) -> None:
        preparation = self.prepare()
        execution_order: list[str] = []

        def analyzer_factory(*_args, **_kwargs):
            return CompletedAnalyzer(execution_order)

        worker = BatchExecutionWorker(
            preparation.prepared,
            lambda job_id: Path(self.temporary.name) / job_id,
            vision_capability_confirmed=True,
            analyzer_factory=analyzer_factory,
        )
        results = []
        worker.result_ready.connect(results.append)
        worker.run()

        self.assertEqual(execution_order, [item.video_id for item in self.videos])
        self.assertEqual([item.video_id for item in results], execution_order)
        self.assertTrue(all(item.status is JobStatus.COMPLETED for item in results))

    def test_external_batch_is_rejected_before_preparation(self) -> None:
        with self.assertRaisesRegex(ValueError, "전송 승인"):
            BatchPreparationWorker(
                self.videos,
                purpose="출입문 앞 물체를 들고 이동하는 장면 후보 확인",
                mode=AnalysisMode.EXTERNAL,
                model_id="external-model",
                provider_id="external",
                provider_endpoint="https://provider.invalid/chat",
                case_id="case-batch",
                case_revision=1,
                analysis_clues=(),
                focus_situations=(),
                limits=AnalysisLimits(),
            )


if __name__ == "__main__":
    unittest.main()
