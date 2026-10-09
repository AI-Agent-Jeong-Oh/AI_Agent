from __future__ import annotations

import hashlib
from pathlib import Path


SUPPORTED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"})


def validate_video_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {path}")
    if not path.is_file():
        raise ValueError(f"영상 경로가 파일이 아닙니다: {path}")
    if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise ValueError(f"지원하지 않는 영상 확장자입니다. 지원 형식: {supported}")


def build_video_id(path: Path) -> str:
    """Build a stable local ID without reading or uploading video contents."""
    stat = path.stat()
    identity = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()
    return hashlib.sha256(identity).hexdigest()[:16]


def seconds_to_milliseconds(seconds: float) -> int:
    if seconds < 0:
        raise ValueError("seconds cannot be negative")
    return round(seconds * 1000)


def milliseconds_to_seconds(milliseconds: int) -> float:
    if milliseconds < 0:
        raise ValueError("milliseconds cannot be negative")
    return milliseconds / 1000.0


def format_milliseconds(value: int) -> str:
    total_seconds = max(0, value) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
