from __future__ import annotations

import os
import platform
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


APP_DIRECTORY_NAME = "VideoAnalysisAgent"
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True, slots=True)
class AppPaths:
    data_dir: Path
    cache_dir: Path

    @classmethod
    def default(cls) -> "AppPaths":
        home = Path.home()
        configured_data = os.environ.get("VIDEO_AGENT_DATA_DIR")
        configured_cache = os.environ.get("VIDEO_AGENT_CACHE_DIR")
        if configured_data and configured_cache:
            return cls(Path(configured_data).expanduser(), Path(configured_cache).expanduser())
        if platform.system() == "Darwin":
            data_dir = home / "Library" / "Application Support" / APP_DIRECTORY_NAME
            cache_dir = home / "Library" / "Caches" / APP_DIRECTORY_NAME
        else:
            data_dir = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / APP_DIRECTORY_NAME
            cache_dir = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / APP_DIRECTORY_NAME
        return cls(data_dir=data_dir, cache_dir=cache_dir)

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def credentials_file(self) -> Path:
        return self.data_dir / "local_account.json"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @property
    def cases_dir(self) -> Path:
        return self.data_dir / "cases"

    @property
    def reviews_dir(self) -> Path:
        return self.data_dir / "reviews"

    @property
    def comparisons_dir(self) -> Path:
        return self.data_dir / "person_comparisons"


class CacheManager:
    ROOT_MARKER = ".video-agent-cache-root"
    JOB_MARKER = ".video-agent-job-cache"

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir.resolve()

    def initialize(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / self.ROOT_MARKER).touch(exist_ok=True)

    def create_job_dir(self, job_id: str) -> Path:
        if not _SAFE_JOB_ID.fullmatch(job_id):
            raise ValueError("job_id contains unsafe path characters")
        self.initialize()
        path = self.cache_dir / job_id
        path.mkdir(parents=False, exist_ok=True)
        (path / self.JOB_MARKER).touch(exist_ok=True)
        return path

    def remove_job_cache(self, job_id: str) -> bool:
        if not _SAFE_JOB_ID.fullmatch(job_id):
            raise ValueError("job_id contains unsafe path characters")
        target = (self.cache_dir / job_id).resolve()
        if target.parent != self.cache_dir:
            raise ValueError("cache target is outside the app cache directory")
        if not self._owns(target):
            return False
        shutil.rmtree(target)
        return True

    def cleanup_older_than(self, age_seconds: float, *, now: float | None = None) -> tuple[str, ...]:
        if age_seconds < 0:
            raise ValueError("age_seconds cannot be negative")
        if not (self.cache_dir / self.ROOT_MARKER).is_file():
            return ()
        cutoff = (time.time() if now is None else now) - age_seconds
        removed: list[str] = []
        for child in self.cache_dir.iterdir():
            if child.is_dir() and child.stat().st_mtime < cutoff and self._owns(child):
                shutil.rmtree(child)
                removed.append(child.name)
        return tuple(sorted(removed))

    def _owns(self, path: Path) -> bool:
        return (
            (self.cache_dir / self.ROOT_MARKER).is_file()
            and path.is_dir()
            and (path / self.JOB_MARKER).is_file()
        )
