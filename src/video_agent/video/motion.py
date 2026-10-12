from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from video_agent.contracts import MotionProposal, MotionScanProfile, MotionScanResult
from video_agent.video.files import build_video_id, validate_video_path
from video_agent.video.probe import VideoMetadata, _resolve_tool
from video_agent.video.process import OperationCanceled, run_bounded_process


class MotionScanError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MotionSample:
    timestamp_sec: float
    score: float


def build_motion_sample_timestamps(
    proposals: tuple[MotionProposal, ...],
    *,
    max_frames: int = 12,
    max_proposals: int = 3,
    peak_interval_sec: float = 0.25,
) -> tuple[float, ...]:
    """Allocate a bounded, peak-centered frame budget across approved proposals."""
    if not proposals:
        raise ValueError("at least one motion proposal is required")
    if max_frames <= 0 or max_proposals <= 0 or peak_interval_sec <= 0:
        raise ValueError("motion sampling limits must be positive")
    if len(proposals) > max_proposals:
        raise ValueError(f"at most {max_proposals} motion proposals can be sampled together")
    if len({proposal.video_id for proposal in proposals}) != 1:
        raise ValueError("motion proposals must belong to the same video")

    ordered = tuple(sorted(proposals, key=lambda item: (item.start_sec, item.end_sec)))
    frames_per_proposal = max_frames if len(ordered) == 1 else min(4, max_frames // len(ordered))
    if frames_per_proposal <= 0:
        raise ValueError("frame budget cannot cover the selected motion proposals")
    timestamps = tuple(
        timestamp
        for proposal in ordered
        for timestamp in _peak_centered_timestamps(
            proposal,
            count=frames_per_proposal,
            interval_sec=peak_interval_sec,
        )
    )
    if len(timestamps) > max_frames:
        raise ValueError("motion timestamps exceed the approved frame budget")
    return timestamps


def build_temporal_sample_timestamps(
    proposals: tuple[MotionProposal, ...],
    *,
    max_frames: int = 12,
    max_proposals: int = 3,
) -> tuple[float, ...]:
    """Spread timestamps over each approved window for ordered change comparison."""
    if not proposals:
        raise ValueError("at least one motion proposal is required")
    if max_frames <= 0 or max_proposals <= 0:
        raise ValueError("temporal sampling limits must be positive")
    if len(proposals) > max_proposals:
        raise ValueError(f"at most {max_proposals} motion proposals can be sampled together")
    if len({proposal.video_id for proposal in proposals}) != 1:
        raise ValueError("motion proposals must belong to the same video")

    ordered = tuple(sorted(proposals, key=lambda item: (item.start_sec, item.end_sec)))
    frames_per_proposal = max_frames if len(ordered) == 1 else min(4, max_frames // len(ordered))
    if frames_per_proposal <= 0:
        raise ValueError("frame budget cannot cover the selected motion proposals")
    timestamps = tuple(
        timestamp
        for proposal in ordered
        for timestamp in _window_spread_timestamps(proposal, frames_per_proposal)
    )
    if len(timestamps) > max_frames:
        raise ValueError("temporal timestamps exceed the approved frame budget")
    return timestamps


def _window_spread_timestamps(
    proposal: MotionProposal,
    count: int,
) -> tuple[float, ...]:
    usable_end = proposal.end_sec - 0.001
    if count <= 0 or usable_end <= proposal.start_sec:
        raise ValueError("motion proposal is too short to sample")
    step = (usable_end - proposal.start_sec) / count
    return tuple(
        round(proposal.start_sec + (index + 0.5) * step, 6)
        for index in range(count)
    )


def _peak_centered_timestamps(
    proposal: MotionProposal,
    *,
    count: int,
    interval_sec: float,
) -> tuple[float, ...]:
    usable_end = proposal.end_sec - 0.001
    if usable_end <= proposal.start_sec:
        raise ValueError("motion proposal is too short to sample")
    usable_span = usable_end - proposal.start_sec
    desired_span = interval_sec * (count - 1)
    if desired_span <= usable_span:
        start = proposal.peak_timestamp_sec - desired_span / 2
        start = min(max(start, proposal.start_sec), usable_end - desired_span)
        values = tuple(start + index * interval_sec for index in range(count))
    else:
        step = usable_span / count
        values = tuple(proposal.start_sec + (index + 0.5) * step for index in range(count))
    return tuple(round(value, 6) for value in values)


class FFmpegMotionScanner:
    """Find high pixel-change windows without interpreting their meaning."""

    _FRAME_TIME = re.compile(rb"frame:\s*\d+.*?pts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
    _SCORE = re.compile(rb"lavfi\.signalstats\.YAVG=([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
    _SHOWINFO_TIME = re.compile(
        rb"\bn:\s*\d+.*?pts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    )

    def __init__(
        self,
        executable: str | None = None,
        *,
        timeout_seconds: float = 120.0,
        max_samples: int = 600,
        max_proposals: int = 5,
        proposal_window_seconds: float = 6.0,
        scan_width: int = 320,
        temporal_scan_width: int = 160,
    ) -> None:
        configured = executable or os.environ.get("VIDEO_AGENT_FFMPEG") or "ffmpeg"
        self._executable = _resolve_tool(configured, "ffmpeg")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_samples < 2:
            raise ValueError("max_samples must be at least 2")
        if max_proposals <= 0:
            raise ValueError("max_proposals must be positive")
        if proposal_window_seconds <= 0:
            raise ValueError("proposal_window_seconds must be positive")
        if scan_width <= 0:
            raise ValueError("scan_width must be positive")
        if temporal_scan_width <= 0:
            raise ValueError("temporal_scan_width must be positive")
        self._timeout_seconds = timeout_seconds
        self._max_samples = max_samples
        self._max_proposals = max_proposals
        self._proposal_window_seconds = proposal_window_seconds
        self._scan_width = scan_width
        self._temporal_scan_width = temporal_scan_width

    @property
    def executable(self) -> str:
        return self._executable

    def is_available(self) -> bool:
        return Path(self._executable).is_file() or shutil.which(self._executable) is not None

    def scan(
        self,
        scan_id: str,
        video_path: Path,
        metadata: VideoMetadata,
        range_start_sec: float,
        range_end_sec: float,
        cancel_event: Event | None = None,
        profile: MotionScanProfile = MotionScanProfile.GENERAL_PEAK,
    ) -> MotionScanResult:
        validate_video_path(video_path)
        if build_video_id(video_path) != metadata.video_id:
            raise MotionScanError("선택 이후 영상 파일이 변경되었습니다.")
        if not scan_id.strip():
            raise ValueError("scan_id is required")
        if not self.is_available():
            raise MotionScanError(
                "ffmpeg를 찾을 수 없습니다. VIDEO_AGENT_FFMPEG에 실행 파일 경로를 설정해 주세요."
            )
        if not 0 <= range_start_sec < range_end_sec <= metadata.duration_seconds + 1e-6:
            raise MotionScanError("움직임 탐색 범위가 영상 길이를 벗어났습니다.")
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCanceled("움직임 탐색이 취소되었습니다.")
        if not isinstance(profile, MotionScanProfile):
            profile = MotionScanProfile(profile)

        span = range_end_sec - range_start_sec
        sample_rate = min(2.0, self._max_samples / span)
        sample_interval = 1.0 / sample_rate
        if profile is MotionScanProfile.REPEATED_SUBTLE:
            samples = self._scan_localized_temporal_changes(
                video_path,
                metadata,
                range_start_sec,
                range_end_sec,
                sample_rate,
                cancel_event,
            )
            proposal_window_seconds = max(10.0, self._proposal_window_seconds)
        else:
            samples = self._scan_general_changes(
                video_path,
                metadata,
                range_start_sec,
                range_end_sec,
                sample_rate,
                cancel_event,
            )
            proposal_window_seconds = self._proposal_window_seconds
        proposals = build_motion_proposals(
            samples,
            video_id=metadata.video_id,
            range_start_sec=range_start_sec,
            range_end_sec=range_end_sec,
            max_proposals=self._max_proposals,
            window_seconds=proposal_window_seconds,
            profile=profile,
        )
        return MotionScanResult(
            scan_id=scan_id,
            video_id=metadata.video_id,
            range_start_sec=range_start_sec,
            range_end_sec=range_end_sec,
            sample_interval_sec=sample_interval,
            sampled_frame_count=len(samples),
            proposals=proposals,
            profile=profile,
        )

    def _scan_general_changes(
        self,
        video_path: Path,
        metadata: VideoMetadata,
        range_start_sec: float,
        range_end_sec: float,
        sample_rate: float,
        cancel_event: Event | None,
    ) -> tuple[MotionSample, ...]:
        span = range_end_sec - range_start_sec
        filter_graph = (
            f"fps={sample_rate:.9f},"
            f"scale=min({self._scan_width}\\,iw):-2,"
            "format=gray,tblend=all_mode=difference,signalstats,"
            "metadata=print:key=lavfi.signalstats.YAVG:file=-"
        )
        command = [
            self._executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-copyts",
            "-ss",
            f"{range_start_sec:.6f}",
            "-t",
            f"{span:.6f}",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-vf",
            filter_graph,
            "-an",
            "-f",
            "null",
            "-",
        ]
        result = run_bounded_process(
            command,
            timeout_seconds=self._timeout_seconds,
            cancel_event=cancel_event,
            max_output_bytes=1_000_000,
        )
        return parse_motion_metadata(
            result.stdout,
            source_start_time_sec=metadata.start_time_seconds,
            range_start_sec=range_start_sec,
            range_end_sec=range_end_sec,
        )

    def _scan_localized_temporal_changes(
        self,
        video_path: Path,
        metadata: VideoMetadata,
        range_start_sec: float,
        range_end_sec: float,
        sample_rate: float,
        cancel_event: Event | None,
    ) -> tuple[MotionSample, ...]:
        """Compare small grayscale tiles so local repeated changes are not lost in full-frame averages."""
        width = min(self._temporal_scan_width, metadata.display_width)
        height = max(2, round(metadata.display_height * width / metadata.display_width))
        if height % 2:
            height += 1
        span = range_end_sec - range_start_sec
        frame_bytes = width * height
        command = [
            self._executable,
            "-hide_banner",
            "-loglevel",
            "info",
            "-nostdin",
            "-copyts",
            "-ss",
            f"{range_start_sec:.6f}",
            "-t",
            f"{span:.6f}",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-vf",
            f"fps={sample_rate:.9f},scale={width}:{height},format=gray,showinfo",
            "-an",
            "-frames:v",
            str(self._max_samples + 1),
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ]
        result = run_bounded_process(
            command,
            timeout_seconds=self._timeout_seconds,
            cancel_event=cancel_event,
            max_output_bytes=frame_bytes * (self._max_samples + 1) + 1024,
        )
        timestamps = tuple(
            float(value) - metadata.start_time_seconds
            for value in self._SHOWINFO_TIME.findall(result.stderr)
        )
        return parse_localized_motion_frames(
            result.stdout,
            timestamps,
            width=width,
            height=height,
            range_start_sec=range_start_sec,
            range_end_sec=range_end_sec,
            max_samples=self._max_samples,
        )


def parse_motion_metadata(
    output: bytes,
    *,
    source_start_time_sec: float,
    range_start_sec: float,
    range_end_sec: float,
) -> tuple[MotionSample, ...]:
    current_timestamp: float | None = None
    samples: list[MotionSample] = []
    for line in output.splitlines():
        time_match = FFmpegMotionScanner._FRAME_TIME.search(line)
        if time_match is not None:
            current_timestamp = float(time_match.group(1)) - source_start_time_sec
            continue
        score_match = FFmpegMotionScanner._SCORE.search(line)
        if score_match is None or current_timestamp is None:
            continue
        if range_start_sec - 1e-3 <= current_timestamp <= range_end_sec + 1e-3:
            samples.append(
                MotionSample(
                    timestamp_sec=min(range_end_sec, max(range_start_sec, current_timestamp)),
                    score=max(0.0, float(score_match.group(1))),
                )
            )
        current_timestamp = None
    return tuple(samples)


def parse_localized_motion_frames(
    raw_frames: bytes,
    timestamps_sec: tuple[float, ...],
    *,
    width: int,
    height: int,
    range_start_sec: float,
    range_end_sec: float,
    max_samples: int,
) -> tuple[MotionSample, ...]:
    """Convert bounded grayscale frames to top-tile change scores with original PTS."""
    if width <= 0 or height <= 0 or max_samples <= 0:
        raise ValueError("localized motion dimensions and limit must be positive")
    frame_size = width * height
    if len(raw_frames) % frame_size:
        raise MotionScanError("국소 움직임 표본의 프레임 경계를 확인할 수 없습니다.")
    frame_count = len(raw_frames) // frame_size
    if frame_count != len(timestamps_sec):
        raise MotionScanError("국소 움직임 표본과 원본 시각 수가 일치하지 않습니다.")
    if frame_count < 2:
        return ()
    frames = memoryview(raw_frames)
    samples: list[MotionSample] = []
    for index in range(1, frame_count):
        timestamp = timestamps_sec[index]
        if not range_start_sec - 1e-3 <= timestamp <= range_end_sec + 1e-3:
            continue
        previous = frames[(index - 1) * frame_size : index * frame_size]
        current = frames[index * frame_size : (index + 1) * frame_size]
        samples.append(
            MotionSample(
                timestamp_sec=min(range_end_sec, max(range_start_sec, timestamp)),
                score=_localized_change_score(previous, current, width, height),
            )
        )
        if len(samples) >= max_samples:
            break
    return tuple(samples)


def _localized_change_score(
    previous: memoryview,
    current: memoryview,
    width: int,
    height: int,
    *,
    columns: int = 8,
    rows: int = 5,
) -> float:
    tile_scores: list[float] = []
    for tile_y in range(rows):
        y_start = tile_y * height // rows
        y_end = (tile_y + 1) * height // rows
        for tile_x in range(columns):
            x_start = tile_x * width // columns
            x_end = (tile_x + 1) * width // columns
            total = 0
            count = 0
            for y in range(y_start, y_end):
                offset = y * width
                for x in range(x_start, x_end):
                    position = offset + x
                    total += abs(current[position] - previous[position])
                    count += 1
            tile_scores.append(total / count if count else 0.0)
    top_count = max(1, len(tile_scores) // 5)
    strongest = sorted(tile_scores, reverse=True)[:top_count]
    return round(sum(strongest) / len(strongest), 6)


def build_motion_proposals(
    samples: tuple[MotionSample, ...],
    *,
    video_id: str,
    range_start_sec: float,
    range_end_sec: float,
    max_proposals: int = 5,
    window_seconds: float = 6.0,
    profile: MotionScanProfile = MotionScanProfile.GENERAL_PEAK,
) -> tuple[MotionProposal, ...]:
    if not video_id.strip():
        raise ValueError("video_id is required")
    if not 0 <= range_start_sec < range_end_sec:
        raise ValueError("motion proposal range is invalid")
    if max_proposals <= 0 or window_seconds <= 0:
        raise ValueError("motion proposal limits must be positive")
    if any(
        item.score < 0 or not range_start_sec <= item.timestamp_sec <= range_end_sec
        for item in samples
    ):
        raise ValueError("motion samples must stay inside the scanned range")
    if not samples:
        return ()
    if not isinstance(profile, MotionScanProfile):
        profile = MotionScanProfile(profile)

    span = range_end_sec - range_start_sec
    active_window = min(window_seconds, span)
    scores = tuple(item.score for item in samples)
    activity_floor = _percentile(scores, 0.35)
    cap = _percentile(scores, 0.65)
    dominant_threshold = _percentile(scores, 0.80)
    candidates: list[tuple[float, float, float, MotionSample]] = []
    for center in samples:
        start = max(range_start_sec, center.timestamp_sec - active_window / 2)
        end = min(range_end_sec, start + active_window)
        start = max(range_start_sec, end - active_window)
        local = tuple(item for item in samples if start <= item.timestamp_sec <= end)
        if not local:
            continue
        peak = max(local, key=lambda item: (item.score, -item.timestamp_sec))
        if profile is MotionScanProfile.REPEATED_SUBTLE:
            capped_mean = sum(min(item.score, cap) for item in local) / len(local)
            active_ratio = sum(item.score > activity_floor for item in local) / len(local)
            dominant_ratio = sum(item.score > dominant_threshold for item in local) / len(local)
            bins = tuple(
                local[index * len(local) // 4 : (index + 1) * len(local) // 4]
                for index in range(4)
            )
            covered_bins = sum(
                bool(items) and any(item.score > activity_floor for item in items)
                for items in bins
            )
            coverage_ratio = covered_bins / 4
            dominant_penalty = max(0.25, 1.0 - 0.85 * dominant_ratio)
            ranking_score = (
                capped_mean
                * (0.5 + 0.5 * active_ratio)
                * (0.6 + 0.4 * coverage_ratio)
                * dominant_penalty
            )
        else:
            average = sum(item.score for item in local) / len(local)
            ranking_score = peak.score + average * 0.25
        candidates.append((ranking_score, start, end, peak))

    candidates.sort(key=lambda item: (-item[0], item[1], item[3].timestamp_sec))
    selected: list[tuple[float, float, float, MotionSample]] = []
    for candidate in candidates:
        _, start, end, _ = candidate
        if any(
            abs(candidate[3].timestamp_sec - chosen[3].timestamp_sec) <= 1e-6
            or not (end <= chosen[1] or start >= chosen[2])
            for chosen in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= max_proposals:
            break

    proposals: list[MotionProposal] = []
    for rank, (ranking_score, start, end, peak) in enumerate(selected, start=1):
        proposal_span = end - start
        proposals.append(
            MotionProposal(
                proposal_id=f"motion-{rank}-{round(peak.timestamp_sec * 1000)}",
                video_id=video_id,
                start_sec=round(start, 6),
                end_sec=round(end, 6),
                peak_timestamp_sec=round(peak.timestamp_sec, 6),
                motion_score=round(ranking_score, 6),
                recommended_sampling_interval_sec=round(
                    max(0.1, proposal_span / 12),
                    6,
                ),
            )
        )
    return tuple(proposals)


def _percentile(values: tuple[float, ...], fraction: float) -> float:
    if not values:
        return 0.0
    if not 0 <= fraction <= 1:
        raise ValueError("percentile fraction must stay within 0..1")
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
