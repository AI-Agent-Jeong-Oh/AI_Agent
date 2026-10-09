from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from threading import Event
from typing import Any

from video_agent.video.files import build_video_id, validate_video_path
from video_agent.video.process import run_bounded_process


class VideoProbeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    video_id: str
    duration_seconds: float
    coded_width: int
    coded_height: int
    display_width: int
    display_height: int
    rotation_degrees: int
    average_frame_rate: float | None
    nominal_frame_rate: float | None
    variable_frame_rate_detected: bool | None
    video_codec: str
    has_audio: bool
    start_time_seconds: float
    stream_time_base: str | None
    file_size_bytes: int

    def __post_init__(self) -> None:
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if min(self.coded_width, self.coded_height, self.display_width, self.display_height) <= 0:
            raise ValueError("video dimensions must be positive")


class FFprobeVideoProbe:
    def __init__(self, executable: str | None = None, timeout_seconds: float = 15.0) -> None:
        configured = executable or os.environ.get("VIDEO_AGENT_FFPROBE") or "ffprobe"
        self._executable = _resolve_tool(configured, "ffprobe")
        self._timeout_seconds = timeout_seconds

    @property
    def executable(self) -> str:
        return self._executable

    def is_available(self) -> bool:
        return Path(self._executable).is_file() or shutil.which(self._executable) is not None

    def probe(self, path: Path, cancel_event: Event | None = None) -> VideoMetadata:
        validate_video_path(path)
        if not self.is_available():
            raise VideoProbeError(
                "ffprobe를 찾을 수 없습니다. VIDEO_AGENT_FFPROBE에 실행 파일 경로를 설정해 주세요."
            )
        command = [
            self._executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
        try:
            result = run_bounded_process(
                command,
                timeout_seconds=self._timeout_seconds,
                cancel_event=cancel_event,
            )
            payload = json.loads(result.stdout.decode("utf-8"))
            return self._parse(path, payload)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            if isinstance(exc, VideoProbeError):
                raise
            raise VideoProbeError(f"영상 메타데이터를 읽을 수 없습니다: {exc}") from exc

    @staticmethod
    def _parse(path: Path, payload: dict[str, Any]) -> VideoMetadata:
        streams = payload.get("streams")
        if not isinstance(streams, list):
            raise VideoProbeError("ffprobe 응답에 streams 배열이 없습니다.")
        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        if not isinstance(video, dict):
            raise VideoProbeError("영상 스트림이 없습니다.")
        format_data = payload.get("format") if isinstance(payload.get("format"), dict) else {}

        duration = _positive_float(video.get("duration")) or _positive_float(format_data.get("duration"))
        if duration is None:
            raise VideoProbeError("영상 길이를 확인할 수 없습니다.")
        width = _positive_int(video.get("width"))
        height = _positive_int(video.get("height"))
        if width is None or height is None:
            raise VideoProbeError("영상 해상도를 확인할 수 없습니다.")

        rotation = _rotation(video)
        display_width, display_height = (height, width) if rotation in {90, 270} else (width, height)
        average_rate = _frame_rate(video.get("avg_frame_rate"))
        nominal_rate = _frame_rate(video.get("r_frame_rate"))
        if average_rate is None or nominal_rate is None:
            vfr_detected: bool | None = None
        else:
            vfr_detected = abs(average_rate - nominal_rate) > 0.001

        return VideoMetadata(
            video_id=build_video_id(path),
            duration_seconds=duration,
            coded_width=width,
            coded_height=height,
            display_width=display_width,
            display_height=display_height,
            rotation_degrees=rotation,
            average_frame_rate=average_rate,
            nominal_frame_rate=nominal_rate,
            variable_frame_rate_detected=vfr_detected,
            video_codec=str(video.get("codec_name") or "unknown"),
            has_audio=any(item.get("codec_type") == "audio" for item in streams),
            start_time_seconds=_float(video.get("start_time"))
            or _float(format_data.get("start_time"))
            or 0.0,
            stream_time_base=str(video.get("time_base")) if video.get("time_base") else None,
            file_size_bytes=path.stat().st_size,
        )


def _frame_rate(value: Any) -> float | None:
    if not isinstance(value, str) or value in {"", "0/0", "N/A"}:
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _rotation(stream: dict[str, Any]) -> int:
    candidates: list[Any] = []
    tags = stream.get("tags")
    if isinstance(tags, dict):
        candidates.append(tags.get("rotate"))
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        candidates.extend(item.get("rotation") for item in side_data if isinstance(item, dict))
    for value in candidates:
        try:
            rotation = round(float(value)) % 360
        except (TypeError, ValueError):
            continue
        if rotation in {0, 90, 180, 270}:
            return rotation
    return 0


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_float(value: Any) -> float | None:
    result = _float(value)
    return result if result is not None and result > 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _resolve_tool(configured: str, name: str) -> str:
    resolved = shutil.which(configured)
    if resolved:
        return resolved
    if configured == name:
        for candidate in (Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name):
            if candidate.is_file():
                return str(candidate)
    return configured
