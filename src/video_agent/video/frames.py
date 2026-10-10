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
    width: int | None = None
    height: int | None = None


class FFmpegFrameExtractor:
    _PTS_TIME = re.compile(rb"pts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
    _FRAME_SIZE = re.compile(rb"\bs:(\d+)x(\d+)\b")

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
                    f"scale=min({max_width}\\,iw):-2,showinfo",
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
                width, height = self._frame_size(result.stderr)
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
                        width=width,
                        height=height,
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

    def _frame_size(self, stderr: bytes) -> tuple[int, int]:
        match = self._FRAME_SIZE.search(stderr)
        if match is None:
            raise FrameExtractionError("추출 프레임의 실제 해상도를 확인할 수 없습니다.")
        return int(match.group(1)), int(match.group(2))


class FFmpegStoryboardComposer:
    """Compose up to four decoded source frames into one bounded row-major storyboard."""

    def __init__(self, executable: str | None = None) -> None:
        configured = executable or os.environ.get("VIDEO_AGENT_FFMPEG") or "ffmpeg"
        self._executable = _resolve_tool(configured, "ffmpeg")

    def compose(
        self,
        frames: tuple[ExtractedFrame, ...],
        output_dir: Path,
        *,
        batch_index: int,
        timeout_seconds: float,
        cancel_event: Event | None = None,
    ) -> ExtractedFrame:
        if not 1 <= len(frames) <= 4:
            raise ValueError("a temporal storyboard requires 1 to 4 source frames")
        if batch_index <= 0 or timeout_seconds <= 0:
            raise ValueError("storyboard batch index and timeout must be positive")
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCanceled("스토리보드 구성이 취소되었습니다.")
        if any(not frame.path.is_file() for frame in frames):
            raise FrameExtractionError("스토리보드 원본 프레임 파일이 없습니다.")

        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / f"storyboard_{batch_index:03d}.jpg"
        temporary_path = output_dir / f".{final_path.name}.partial.jpg"
        inputs = [item for frame in frames for item in ("-i", str(frame.path))]
        filters: list[str] = []
        labels: list[str] = []
        for index in range(len(frames)):
            label = f"v{index}"
            labels.append(f"[{label}]")
            filters.append(
                f"[{index}:v]scale=480:270:force_original_aspect_ratio=decrease,"
                f"pad=480:270:(ow-iw)/2:(oh-ih)/2:black[{label}]"
            )
        if len(frames) == 1:
            output_label = "v0"
            width, height = 480, 270
        else:
            layouts = {
                2: "0_0|480_0",
                3: "0_0|480_0|0_270",
                4: "0_0|480_0|0_270|480_270",
            }
            output_label = "storyboard"
            filters.append(
                f"{''.join(labels)}xstack=inputs={len(frames)}:layout={layouts[len(frames)]}"
                f"[{output_label}]"
            )
            width = 960
            height = 270 if len(frames) == 2 else 540
        command = [
            self._executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{output_label}]",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-y",
            str(temporary_path),
        ]
        try:
            run_bounded_process(
                command,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
            )
            if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
                raise FrameExtractionError("시간순 스토리보드 파일이 생성되지 않았습니다.")
            temporary_path.replace(final_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        identity = hashlib.sha256(
            "|".join(frame.frame_id for frame in frames).encode("utf-8")
        ).hexdigest()[:16]
        return ExtractedFrame(
            frame_id=f"storyboard:{identity}",
            path=final_path,
            requested_timestamp_sec=frames[0].requested_timestamp_sec,
            source_timestamp_sec=frames[0].source_timestamp_sec,
            timestamp_accuracy="storyboard_of_decoded_pts",
            mime_type="image/jpeg",
            sha256=_sha256(final_path),
            width=width,
            height=height,
        )


class FFmpegFineDetailComposer:
    """Keep temporal frames separate while pairing an overview with a central detail crop."""

    def __init__(self, executable: str | None = None) -> None:
        configured = executable or os.environ.get("VIDEO_AGENT_FFMPEG") or "ffmpeg"
        self._executable = _resolve_tool(configured, "ffmpeg")

    def compose(
        self,
        frames: tuple[ExtractedFrame, ...],
        output_dir: Path,
        *,
        batch_index: int,
        timeout_seconds: float,
        cancel_event: Event | None = None,
    ) -> tuple[ExtractedFrame, ...]:
        if not 1 <= len(frames) <= 4:
            raise ValueError("a fine-detail batch requires 1 to 4 source frames")
        if batch_index <= 0 or timeout_seconds <= 0:
            raise ValueError("fine-detail batch index and timeout must be positive")
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCanceled("정밀 확대 이미지 구성이 취소되었습니다.")
        if any(not frame.path.is_file() for frame in frames):
            raise FrameExtractionError("정밀 확대 원본 프레임 파일이 없습니다.")

        output_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        created: list[Path] = []
        composed: list[ExtractedFrame] = []
        try:
            for frame_index, frame in enumerate(frames, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise OperationCanceled("정밀 확대 이미지 구성이 취소되었습니다.")
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("정밀 확대 이미지 구성 시간 제한을 초과했습니다.")
                final_path = output_dir / (
                    f"fine_detail_{batch_index:03d}_{frame_index:02d}.jpg"
                )
                temporary_path = output_dir / f".{final_path.name}.partial.jpg"
                filter_graph = (
                    "[0:v]split=2[overview_src][detail_src];"
                    "[overview_src]scale=640:360:force_original_aspect_ratio=decrease,"
                    "pad=1280:360:(ow-iw)/2:(oh-ih)/2:black[overview];"
                    "[detail_src]crop=iw*0.6:ih*0.6:(iw-ow)/2:(ih-oh)/2,"
                    "scale=1280:720:force_original_aspect_ratio=decrease,"
                    "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black[detail];"
                    "[overview][detail]vstack=inputs=2[output]"
                )
                command = [
                    self._executable,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-i",
                    str(frame.path),
                    "-filter_complex",
                    filter_graph,
                    "-map",
                    "[output]",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    "-y",
                    str(temporary_path),
                ]
                run_bounded_process(
                    command,
                    timeout_seconds=remaining,
                    cancel_event=cancel_event,
                )
                if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
                    raise FrameExtractionError("정밀 확대 이미지 파일이 생성되지 않았습니다.")
                temporary_path.replace(final_path)
                created.append(final_path)
                composed.append(
                    ExtractedFrame(
                        frame_id=f"fine-detail:{frame.frame_id}",
                        path=final_path,
                        requested_timestamp_sec=frame.requested_timestamp_sec,
                        source_timestamp_sec=frame.source_timestamp_sec,
                        timestamp_accuracy="fine_detail_overview_center_crop",
                        mime_type="image/jpeg",
                        sha256=_sha256(final_path),
                        width=1280,
                        height=1080,
                    )
                )
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            for path in output_dir.glob(".fine_detail_*.partial.jpg"):
                path.unlink(missing_ok=True)
            raise
        return tuple(composed)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
