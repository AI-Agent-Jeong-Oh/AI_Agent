from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from threading import Event

from video_agent.contracts import MotionProposal, MotionScanProfile
from video_agent.video.files import milliseconds_to_seconds, seconds_to_milliseconds
from video_agent.video.frames import (
    FFmpegFineDetailComposer,
    FFmpegFrameExtractor,
    FFmpegStoryboardComposer,
    FrameExtractionError,
)
from video_agent.video.motion import (
    FFmpegMotionScanner,
    MotionSample,
    build_motion_proposals,
    build_motion_sample_timestamps,
    build_temporal_sample_timestamps,
    parse_localized_motion_frames,
    parse_motion_metadata,
)
from video_agent.video.probe import FFprobeVideoProbe, VideoProbeError
from video_agent.video.process import OperationCanceled


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools unavailable")
class VideoProcessingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary_directory = tempfile.TemporaryDirectory(prefix="영상 분석 test ")
        cls.directory = Path(cls._temporary_directory.name)
        cls.video_path = cls.directory / "짧은 합성 영상.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x120:rate=24",
                "-t",
                "1.0",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(cls.video_path),
            ],
            check=True,
            timeout=20,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary_directory.cleanup()

    def test_probe_reads_real_metadata_from_unicode_path(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)

        self.assertAlmostEqual(metadata.duration_seconds, 1.0, places=2)
        self.assertEqual((metadata.display_width, metadata.display_height), (160, 120))
        self.assertAlmostEqual(metadata.average_frame_rate or 0, 24.0, places=3)
        self.assertEqual(metadata.video_codec, "h264")
        self.assertFalse(metadata.has_audio)

    def test_extract_records_decoded_source_timestamp(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)
        output_dir = self.directory / "추출 프레임 결과"

        frames = FFmpegFrameExtractor().extract(
            self.video_path,
            metadata,
            (0.2, 0.7),
            output_dir,
            max_frames=2,
        )

        self.assertEqual(len(frames), 2)
        self.assertTrue(all(frame.path.is_file() for frame in frames))
        self.assertTrue(all(frame.timestamp_accuracy == "decoded_pts" for frame in frames))
        self.assertTrue(all(frame.source_timestamp_sec >= frame.requested_timestamp_sec for frame in frames))
        self.assertTrue(all(len(frame.sha256) == 64 for frame in frames))
        self.assertTrue(all((frame.width, frame.height) == (160, 120) for frame in frames))

    def test_frame_count_and_time_range_are_enforced(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)
        extractor = FFmpegFrameExtractor()
        with self.assertRaises(FrameExtractionError):
            extractor.extract(self.video_path, metadata, (0.1, 0.2), self.directory / "many", max_frames=1)
        with self.assertRaises(FrameExtractionError):
            extractor.extract(self.video_path, metadata, (metadata.duration_seconds,), self.directory / "late", max_frames=1)

    def test_temporal_storyboard_composes_decoded_frames_without_changing_source_files(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)
        output_dir = self.directory / "storyboard source"
        frames = FFmpegFrameExtractor().extract(
            self.video_path,
            metadata,
            (0.2, 0.4, 0.6, 0.8),
            output_dir,
            max_frames=4,
        )
        source_hashes = tuple(frame.sha256 for frame in frames)

        storyboard = FFmpegStoryboardComposer().compose(
            frames,
            output_dir,
            batch_index=1,
            timeout_seconds=10,
        )

        self.assertTrue(storyboard.path.is_file())
        self.assertEqual((storyboard.width, storyboard.height), (960, 540))
        self.assertEqual(storyboard.timestamp_accuracy, "storyboard_of_decoded_pts")
        self.assertEqual(tuple(frame.sha256 for frame in frames), source_hashes)

    def test_fine_detail_composer_pairs_overview_and_center_crop_without_changing_sources(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)
        output_dir = self.directory / "fine detail source"
        frames = FFmpegFrameExtractor().extract(
            self.video_path,
            metadata,
            (0.2, 0.4),
            output_dir,
            max_frames=2,
            max_width=1920,
        )
        source_hashes = tuple(frame.sha256 for frame in frames)

        detail_frames = FFmpegFineDetailComposer().compose(
            frames,
            output_dir,
            batch_index=1,
            timeout_seconds=10,
        )

        self.assertEqual(len(detail_frames), 2)
        self.assertTrue(all(frame.path.is_file() for frame in detail_frames))
        self.assertTrue(all((frame.width, frame.height) == (1280, 1080) for frame in detail_frames))
        self.assertTrue(
            all(
                frame.timestamp_accuracy == "fine_detail_overview_center_crop"
                for frame in detail_frames
            )
        )
        self.assertEqual(tuple(frame.sha256 for frame in frames), source_hashes)

    def test_pre_canceled_extraction_creates_no_frames(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)
        output_dir = self.directory / "canceled"
        canceled = Event()
        canceled.set()
        with self.assertRaises(OperationCanceled):
            FFmpegFrameExtractor().extract(
                self.video_path,
                metadata,
                (0.2,),
                output_dir,
                max_frames=1,
                cancel_event=canceled,
            )
        self.assertFalse(list(output_dir.glob("*.jpg")))

    def test_corrupt_video_is_rejected(self) -> None:
        corrupt = self.directory / "손상 영상.mp4"
        corrupt.write_bytes(b"not a video")
        with self.assertRaises(VideoProbeError):
            FFprobeVideoProbe(timeout_seconds=3).probe(corrupt)

    def test_motion_scan_is_bounded_and_returns_original_time_windows(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)

        result = FFmpegMotionScanner(max_samples=10, max_proposals=2).scan(
            "scan-test",
            self.video_path,
            metadata,
            0.0,
            metadata.duration_seconds,
        )

        self.assertLessEqual(result.sampled_frame_count, 10)
        self.assertTrue(result.proposals)
        self.assertTrue(
            all(
                0 <= item.start_sec < item.end_sec <= metadata.duration_seconds
                for item in result.proposals
            )
        )

    def test_pre_canceled_motion_scan_starts_no_result(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)
        canceled = Event()
        canceled.set()

        with self.assertRaises(OperationCanceled):
            FFmpegMotionScanner().scan(
                "scan-canceled",
                self.video_path,
                metadata,
                0.0,
                metadata.duration_seconds,
                canceled,
            )

    def test_repeated_subtle_scan_is_bounded_and_uses_requested_profile(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)

        result = FFmpegMotionScanner(max_samples=10, max_proposals=2).scan(
            "scan-temporal",
            self.video_path,
            metadata,
            0.0,
            metadata.duration_seconds,
            profile=MotionScanProfile.REPEATED_SUBTLE,
        )

        self.assertEqual(result.profile, MotionScanProfile.REPEATED_SUBTLE)
        self.assertLessEqual(result.sampled_frame_count, 10)


class TimeConversionTests(unittest.TestCase):
    def test_seconds_and_ui_milliseconds_round_trip(self) -> None:
        self.assertEqual(seconds_to_milliseconds(1.2345), 1234)
        self.assertEqual(milliseconds_to_seconds(1234), 1.234)

    def test_negative_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            seconds_to_milliseconds(-0.001)
        with self.assertRaises(ValueError):
            milliseconds_to_seconds(-1)


class MotionProposalTests(unittest.TestCase):
    @staticmethod
    def _proposal(index: int, start: float, peak: float) -> MotionProposal:
        return MotionProposal(
            proposal_id=f"motion-{index}",
            video_id="video-1",
            start_sec=start,
            end_sec=start + 6.0,
            peak_timestamp_sec=peak,
            motion_score=5.0 - index,
            recommended_sampling_interval_sec=0.5,
        )

    def test_metadata_parser_uses_original_pts_not_frame_number(self) -> None:
        output = (
            b"frame:0 pts:177 pts_time:88.5\n"
            b"lavfi.signalstats.YAVG=2.5\n"
            b"frame:1 pts:178 pts_time:89.0\n"
            b"lavfi.signalstats.YAVG=4.0\n"
        )

        samples = parse_motion_metadata(
            output,
            source_start_time_sec=0.0,
            range_start_sec=88.0,
            range_end_sec=90.0,
        )

        self.assertEqual(tuple(item.timestamp_sec for item in samples), (88.5, 89.0))
        self.assertEqual(tuple(item.score for item in samples), (2.5, 4.0))

    def test_top_windows_are_non_overlapping_and_keep_peak_time(self) -> None:
        samples = tuple(
            MotionSample(timestamp_sec=float(second), score=score)
            for second, score in ((1, 1.0), (2, 8.0), (3, 1.0), (8, 7.0), (9, 1.0))
        )

        proposals = build_motion_proposals(
            samples,
            video_id="video-1",
            range_start_sec=0.0,
            range_end_sec=10.0,
            max_proposals=2,
            window_seconds=4.0,
        )

        self.assertEqual(len(proposals), 2)
        self.assertEqual(proposals[0].peak_timestamp_sec, 2.0)
        self.assertEqual(proposals[1].peak_timestamp_sec, 8.0)
        self.assertLessEqual(proposals[0].end_sec, proposals[1].start_sec)

    def test_localized_frame_parser_uses_original_timestamps_and_top_tiles(self) -> None:
        first = bytes(40)
        second = bytearray(40)
        second[0] = 255

        samples = parse_localized_motion_frames(
            first + bytes(second),
            (3.0, 3.5),
            width=8,
            height=5,
            range_start_sec=3.0,
            range_end_sec=4.0,
            max_samples=3,
        )

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].timestamp_sec, 3.5)
        self.assertGreater(samples[0].score, 0)

    def test_repeated_subtle_ranking_deemphasizes_dominant_outlier_windows(self) -> None:
        samples = tuple(
            MotionSample(timestamp_sec=float(second), score=score)
            for second, score in (
                *((second, 0.0) for second in range(0, 10)),
                *((second, 2.0) for second in range(10, 20)),
                *((second, 0.0) for second in range(20, 30)),
                *((second, 10.0) for second in range(30, 40)),
                *((second, 0.0) for second in range(40, 50)),
            )
        )

        proposals = build_motion_proposals(
            samples,
            video_id="video-1",
            range_start_sec=0.0,
            range_end_sec=50.0,
            max_proposals=1,
            window_seconds=8.0,
            profile=MotionScanProfile.REPEATED_SUBTLE,
        )

        self.assertEqual(len(proposals), 1)
        self.assertLess(proposals[0].start_sec, 20.0)

    def test_single_motion_proposal_uses_twelve_peak_centered_timestamps(self) -> None:
        timestamps = build_motion_sample_timestamps((self._proposal(1, 10.0, 13.0),))

        self.assertEqual(len(timestamps), 12)
        self.assertAlmostEqual(timestamps[1] - timestamps[0], 0.25)
        self.assertLessEqual(timestamps[0], 13.0)
        self.assertGreaterEqual(timestamps[-1], 13.0)
        self.assertTrue(all(10.0 <= value < 16.0 for value in timestamps))

    def test_three_motion_proposals_get_four_frames_each_in_time_order(self) -> None:
        proposals = (
            self._proposal(3, 20.0, 23.0),
            self._proposal(1, 0.0, 3.0),
            self._proposal(2, 10.0, 13.0),
        )

        timestamps = build_motion_sample_timestamps(proposals)

        self.assertEqual(len(timestamps), 12)
        self.assertEqual(timestamps, tuple(sorted(timestamps)))
        self.assertEqual(sum(0.0 <= value < 6.0 for value in timestamps), 4)
        self.assertEqual(sum(10.0 <= value < 16.0 for value in timestamps), 4)
        self.assertEqual(sum(20.0 <= value < 26.0 for value in timestamps), 4)

    def test_temporal_sampling_spreads_frames_across_the_whole_window(self) -> None:
        proposal = self._proposal(1, 10.0, 13.0)

        timestamps = build_temporal_sample_timestamps((proposal,))

        self.assertEqual(len(timestamps), 12)
        self.assertLess(timestamps[0], 10.5)
        self.assertGreater(timestamps[-1], 15.5)
        self.assertAlmostEqual(timestamps[1] - timestamps[0], 0.5, places=3)

    def test_more_than_three_motion_proposals_are_rejected(self) -> None:
        proposals = tuple(self._proposal(index, index * 10.0, index * 10.0 + 3.0) for index in range(4))

        with self.assertRaisesRegex(ValueError, "at most 3"):
            build_motion_sample_timestamps(proposals)


if __name__ == "__main__":
    unittest.main()
