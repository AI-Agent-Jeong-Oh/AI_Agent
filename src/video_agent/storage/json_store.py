from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from video_agent.contracts import AnalysisJob, AnalysisMode
from video_agent.storage.paths import AppPaths


_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True, slots=True)
class AppSettings:
    mode: AnalysisMode = AnalysisMode.SAMPLE
    local_model_id: str | None = None
    external_provider_id: str | None = None
    external_model_id: str | None = None
    external_endpoint: str | None = None
    api_key_env_name: str = "OPENAI_API_KEY"

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AnalysisMode):
            object.__setattr__(self, "mode", AnalysisMode(self.mode))
        if "KEY" not in self.api_key_env_name.upper():
            raise ValueError("api_key_env_name must name an environment variable, not contain a key")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        allowed = {
            "mode",
            "local_model_id",
            "external_provider_id",
            "external_model_id",
            "external_endpoint",
            "api_key_env_name",
        }
        if not isinstance(data, dict) or not set(data).issubset(allowed):
            raise ValueError("settings contain unknown fields")
        return cls(**data)


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, settings: AppSettings) -> None:
        _atomic_json_write(self.path, settings.to_dict())

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        return AppSettings.from_dict(_read_json_object(self.path))


class ResultStore:
    def __init__(self, paths: AppPaths) -> None:
        self._directory = paths.results_dir

    def save(self, job: AnalysisJob) -> Path:
        if not _SAFE_JOB_ID.fullmatch(job.job_id):
            raise ValueError("job_id contains unsafe path characters")
        path = self._directory / f"{job.job_id}.json"
        _atomic_json_write(path, job.to_dict())
        return path

    def load(self, job_id: str) -> AnalysisJob:
        if not _SAFE_JOB_ID.fullmatch(job_id):
            raise ValueError("job_id contains unsafe path characters")
        return AnalysisJob.from_dict(_read_json_object(self._directory / f"{job_id}.json"))


def _read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("stored JSON must be an object")
    return data


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
