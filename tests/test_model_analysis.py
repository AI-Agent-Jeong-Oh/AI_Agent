from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from video_agent.analysis.factory import create_analyzer
from video_agent.analysis.vision import ModelResponseError, VisionAnalyzer, parse_model_response
from video_agent.contracts import AnalysisMode, AnalysisRequest, JobStatus, ResultOrigin
from video_agent.models.config import ModelConfigurationError, ProviderConfig
from video_agent.models.provider import (
    FakeVisionProvider,
    HttpVisionProvider,
    ModelConnectionError,
    ProviderResponse,
)
from video_agent.video.frames import ExtractedFrame
from video_agent.video.probe import VideoMetadata


def local_config() -> ProviderConfig:
    return ProviderConfig(
        mode=AnalysisMode.LOCAL,
        provider_id="local-test",
        endpoint="http://127.0.0.1:11434/api/chat",
        model_id="explicit-vision-model",
        protocol="ollama_native",
        vision_capability_confirmed=True,
    )


def metadata(video_id: str = "video-1") -> VideoMetadata:
    return VideoMetadata(
        video_id=video_id,
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
        file_size_bytes=100,
    )


def request(path: Path, *, mode: AnalysisMode = AnalysisMode.LOCAL) -> AnalysisRequest:
    return AnalysisRequest(
        job_id="job-real-1",
        plan_id="plan-1",
        approval_id="approval-1",
        video_id="video-1",
        video_path=path,
        purpose="출입문 앞에 사람이 오래 머무르는 장면 후보 찾기",
        duration_seconds=10.0,
        mode=mode,
        model_id="explicit-vision-model",
        range_start_sec=0.0,
        range_end_sec=10.0,
        sample_timestamps_sec=(2.0,),
        transfer_approval_id="transfer-1" if mode is AnalysisMode.EXTERNAL else None,
    )


def frame(path: Path) -> ExtractedFrame:
    return ExtractedFrame(
        frame_id="video-1:2000",
        path=path,
        requested_timestamp_sec=2.0,
        source_timestamp_sec=2.0,
        timestamp_accuracy="decoded_pts",
        mime_type="image/jpeg",
        sha256="0" * 64,
    )


class FakeProbe:
    def probe(self, _path: Path, _cancel: Event) -> VideoMetadata:
        return metadata()


class FakeExtractor:
    def __init__(self, extracted: ExtractedFrame) -> None:
        self.extracted = extracted

    def extract(self, *_args, **_kwargs) -> tuple[ExtractedFrame, ...]:
        return (self.extracted,)


class ModelResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "frame.jpg"
        self.path.write_bytes(b"jpeg")
        self.request = request(Path(self.temp.name) / "video.mp4")
        self.frames = (frame(self.path),)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _response(self, segments: list[dict[str, object]]) -> ProviderResponse:
        return ProviderResponse("test", "explicit-vision-model", json.dumps({"segments": segments}))

    def _valid_segment(self) -> dict[str, object]:
        return {
            "start_sec": 1.5,
            "end_sec": 2.5,
            "summary": "출입문 주변에 사람이 보이는 장면 후보",
            "evidence": ["2.000초 프레임에서 사람 형태가 관찰됨"],
            "source_frame_ids": ["video-1:2000"],
            "uncertainty": "단일 프레임이라 체류 시간은 확인이 필요함",
        }

    def test_valid_response_is_accepted(self) -> None:
        segments = parse_model_response(self._response([self._valid_segment()]), self.request, self.frames)
        self.assertEqual(segments[0].source_frame_ids, ("video-1:2000",))

    def test_invalid_json_is_rejected(self) -> None:
        response = ProviderResponse("test", "explicit-vision-model", "not-json")
        with self.assertRaises(ModelResponseError):
            parse_model_response(response, self.request, self.frames)

    def test_out_of_range_segment_is_rejected_not_clamped(self) -> None:
        segment = self._valid_segment()
        segment["end_sec"] = 11.0
        with self.assertRaises(ModelResponseError):
            parse_model_response(self._response([segment]), self.request, self.frames)

    def test_unknown_frame_evidence_is_rejected(self) -> None:
        segment = self._valid_segment()
        segment["source_frame_ids"] = ["invented-frame"]
        with self.assertRaises(ModelResponseError):
            parse_model_response(self._response([segment]), self.request, self.frames)

    def test_unsafe_criminal_certainty_is_rejected(self) -> None:
        segment = self._valid_segment()
        segment["summary"] = "이 사람은 범죄자입니다"
        with self.assertRaises(ModelResponseError):
            parse_model_response(self._response([segment]), self.request, self.frames)


class ProviderAdapterTests(unittest.TestCase):
    def test_ollama_payload_contains_images_and_json_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "frame.jpg"
            image.write_bytes(b"jpeg-bytes")
            captured: dict[str, object] = {}

            def transport(url, headers, payload, timeout):
                captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
                return {"message": {"content": '{"segments": []}'}}

            provider = HttpVisionProvider(local_config(), transport)
            response = provider.analyze("prompt", (frame(image),), timeout_seconds=3, cancel_event=Event())

            self.assertEqual(response.content, '{"segments": []}')
            payload = captured["payload"]
            self.assertTrue(payload["messages"][0]["images"])
            self.assertEqual(payload["format"]["type"], "object")

    def test_unconfigured_local_mode_does_not_fall_back_to_sample(self) -> None:
        keys = {
            "VIDEO_AGENT_LOCAL_MODEL": "",
            "VIDEO_AGENT_LOCAL_MODEL_SUPPORTS_VISION": "",
        }
        with patch.dict(os.environ, keys, clear=False):
            with self.assertRaises(ModelConfigurationError):
                create_analyzer(AnalysisMode.LOCAL, None, lambda job_id: Path("/tmp") / job_id)

    def test_provider_timeout_is_reported_as_connection_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "frame.jpg"
            image.write_bytes(b"jpeg-bytes")

            def timed_out_transport(*_args):
                raise TimeoutError("deadline")

            provider = HttpVisionProvider(local_config(), timed_out_transport)
            with self.assertRaisesRegex(ModelConnectionError, "연결에 실패"):
                provider.analyze("prompt", (frame(image),), timeout_seconds=0.01, cancel_event=Event())


class VisionAnalyzerTests(unittest.TestCase):
    def test_fake_provider_flow_is_labeled_as_test_not_real_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mp4"
            video.touch()
            image = root / "frame.jpg"
            image.write_bytes(b"jpeg")
            content = json.dumps(
                {
                    "segments": [
                        {
                            "start_sec": 1.5,
                            "end_sec": 2.5,
                            "summary": "사람 형태가 보이는 후보 장면",
                            "evidence": ["2.0초 프레임"],
                            "source_frame_ids": ["video-1:2000"],
                            "uncertainty": "단일 프레임 관찰",
                        }
                    ]
                }
            )
            provider = FakeVisionProvider(local_config(), content)
            progress_messages: list[str] = []
            analyzer = VisionAnalyzer(
                provider,
                lambda _job: root / "cache",
                probe=FakeProbe(),
                extractor=FakeExtractor(frame(image)),
            )

            result = analyzer.analyze(
                request(video),
                Event(),
                lambda _value, message: progress_messages.append(message),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertFalse(result.is_sample)
            self.assertEqual(result.origin, ResultOrigin.TEST_DOUBLE)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(result.model_id, "explicit-vision-model")
            self.assertEqual(progress_messages[-1], "테스트용 가짜 공급자 응답 검증 완료")

    def test_provider_mode_mismatch_is_rejected_without_fallback(self) -> None:
        provider = FakeVisionProvider(local_config(), '{"segments": []}')
        analyzer = VisionAnalyzer(provider, lambda _job: Path("/tmp"))
        with self.assertRaises(ValueError):
            analyzer.analyze(request(Path("/tmp/video.mp4"), mode=AnalysisMode.EXTERNAL), Event(), lambda *_: None)


if __name__ == "__main__":
    unittest.main()
