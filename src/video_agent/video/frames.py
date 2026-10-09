from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from video_agent.video.files import validate_video_path
from video_agent.video.probe import VideoMetadata, _resolve_tool
from video_agent.video.process import OperationCanceled, run_bounded_process


class FrameExtractionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractedFrame:
    frame_id: str
    path: Path
    requested_timestamp_sec: float
    source_timestamp_sec: float
    timestamp_accuracy: str
    mime_type: str
    sha256: str


class FFmpegFrameExtractor:
    _PTS_TIME = re.compile(rb"pts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))")

    def __init__(self, executable: str | None = None, per_frame_timeout_seconds: float = 15.0) -> None:
        configured = executable or os.environ.get("VIDEO_AGENT_FFMPEG") or "ffmpeg"
        self._executable = _resolve_tool(configured, "ffmpeg")
        self._per_frame_timeout_seconds = per_frame_timeout_seconds

    @property
    def executable(self) -> str:
        return self._executable

    def is_available(self) -> bool:
        return Path(self._executable).is_file() or shutil.which(self._executable) is not None

    def extract(
        self,
        video_path: Path,
        metadata: VideoMetadata,
        timestamps_sec: tuple[float, ...],
        output_dir: Path,
        *,
        max_frames: int,
        cancel_event: Event | None = None,
        max_width: int = 960,
        total_timeout_seconds: float | None = None,
    ) -> tuple[ExtractedFrame, ...]:
        validate_video_path(video_path)
        if not self.is_available():
            raise FrameExtractionError(
                "ffmpeg를 찾을 수 없습니다. VIDEO_AGENT_FFMPEG에 실행 파일 경로를 설정해 주세요."
            )
        if max_frames <= 0:
            raise ValueError("max_frames must be positive")
        if len(timestamps_sec) > max_frames:
            raise FrameExtractionError(f"요청 프레임 {len(timestamps_sec)}개가 한도 {max_frames}개를 초과했습니다.")
        if max_width <= 0:
            raise ValueError("max_width must be positive")
        for timestamp in timestamps_sec:
            if not 0 <= timestamp < metadata.duration_seconds:
                raise FrameExtractionError(
                    f"프레임 시각 {timestamp:g}초가 영상 범위 0~{metadata.duration_seconds:g}초를 벗어났습니다."
                )

        output_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        created: list[Path] = []
        frames: list[ExtractedFrame] = []
        try:
            for index, timestamp in enumerate(timestamps_sec):
                if cancel_event is not None and cancel_event.is_set():
                    raise OperationCanceled("프레임 추출이 취소되었습니다.")
                final_path = output_dir / f"frame_{index:03d}_{round(timestamp * 1000):010d}.jpg"
                temporary_path = output_dir / f".{final_path.name}.partial.jpg"
                command = [
                    self._executable,
                    "-hide_banner",
                    "-loglevel",
                    "info",
                    "-nostdin",
                    "-copyts",
                    "-ss",
                    f"{timestamp:.6f}",
                    "-i",
                    str(video_path),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-vf",
                    f"showinfo,scale={max_width}:-2:force_original_aspect_ratio=decrease",
                    "-q:v",
                    "3",
                    "-y",
                    str(temporary_path),
                ]
                if total_timeout_seconds is None:
                    process_timeout = self._per_frame_timeout_seconds
                else:
                    remaining = total_timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError("승인된 프레임 추출 시간 제한을 초과했습니다.")
                    process_timeout = min(self._per_frame_timeout_seconds, remaining)
                result = run_bounded_process(
                    command,
                    timeout_seconds=process_timeout,
                    cancel_event=cancel_event,
                )
                if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
                    raise FrameExtractionError(f"{timestamp:g}초 프레임 파일이 생성되지 않았습니다.")
                source_timestamp = self._source_timestamp(result.stderr, metadata, timestamp)
                temporary_path.replace(final_path)
                created.append(final_path)
                frames.append(
                    ExtractedFrame(
                        frame_id=f"{metadata.video_id}:{round(source_timestamp * 1000)}",
                        path=final_path,
                        requested_timestamp_sec=timestamp,
                        source_timestamp_sec=source_timestamp,
                        timestamp_accuracy="decoded_pts",
                        mime_type="image/jpeg",
                        sha256=_sha256(final_path),
                    )
                )
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            for path in output_dir.glob(".*.partial.jpg"):
                path.unlink(missing_ok=True)
            raise
        return tuple(frames)

    def _source_timestamp(
        self,
        stderr: bytes,
        metadata: VideoMetadata,
        requested_timestamp: float,
    ) -> float:
        match = self._PTS_TIME.search(stderr)
        if match is None:
            raise FrameExtractionError(
                f"{requested_timestamp:g}초 프레임의 원본 PTS를 확인할 수 없습니다."
            )
        presentation_timestamp = float(match.group(1)) - metadata.start_time_seconds
        if presentation_timestamp < -0.001:
            raise FrameExtractionError("추출 프레임의 원본 PTS가 영상 시작보다 앞섭니다.")
        return max(0.0, presentation_timestamp)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
