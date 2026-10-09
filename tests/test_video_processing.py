from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from threading import Event

from video_agent.video.files import milliseconds_to_seconds, seconds_to_milliseconds
from video_agent.video.frames import FFmpegFrameExtractor, FrameExtractionError
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

    def test_frame_count_and_time_range_are_enforced(self) -> None:
        metadata = FFprobeVideoProbe().probe(self.video_path)
        extractor = FFmpegFrameExtractor()
        with self.assertRaises(FrameExtractionError):
            extractor.extract(self.video_path, metadata, (0.1, 0.2), self.directory / "many", max_frames=1)
        with self.assertRaises(FrameExtractionError):
            extractor.extract(self.video_path, metadata, (metadata.duration_seconds,), self.directory / "late", max_frames=1)

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


class TimeConversionTests(unittest.TestCase):
    def test_seconds_and_ui_milliseconds_round_trip(self) -> None:
        self.assertEqual(seconds_to_milliseconds(1.2345), 1234)
        self.assertEqual(milliseconds_to_seconds(1234), 1.234)

    def test_negative_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            seconds_to_milliseconds(-0.001)
        with self.assertRaises(ValueError):
            milliseconds_to_seconds(-1)


if __name__ == "__main__":
    unittest.main()
