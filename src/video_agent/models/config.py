from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit

from video_agent.contracts import AnalysisMode


class ModelConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeInventory:
    ollama_executable: str | None
    installed_ollama_models: tuple[str, ...]
    local_model_id: str | None
    local_endpoint: str | None

    @classmethod
    def detect(cls) -> "RuntimeInventory":
        executable = shutil.which("ollama")
        model_id = os.environ.get("VIDEO_AGENT_LOCAL_MODEL") or None
        endpoint = os.environ.get("VIDEO_AGENT_LOCAL_ENDPOINT") or None
        installed_models: tuple[str, ...] = ()
        if executable:
            try:
                result = subprocess.run(
                    [executable, "list"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=True,
                )
                rows = [line.split() for line in result.stdout.splitlines()[1:] if line.split()]
                installed_models = tuple(row[0] for row in rows)
            except (OSError, subprocess.SubprocessError):
                installed_models = ()
        return cls(executable, installed_models, model_id, endpoint)

    @property
    def ready_for_local_connection(self) -> bool:
        return bool(
            self.ollama_executable
            and self.local_model_id
            and self.local_endpoint
            and self.local_model_id in self.installed_ollama_models
        )


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    mode: AnalysisMode
    provider_id: str
    endpoint: str
    model_id: str
    protocol: str
    vision_capability_confirmed: bool
    api_key_env: str | None = None

    def validate(self) -> None:
        if self.mode is AnalysisMode.SAMPLE:
            raise ModelConfigurationError("샘플 모드에는 모델 공급자 설정을 사용할 수 없습니다.")
        if not self.provider_id.strip() or not self.model_id.strip():
            raise ModelConfigurationError("공급자 ID와 모델 ID를 명시해야 합니다.")
        if not self.vision_capability_confirmed:
            raise ModelConfigurationError("영상 입력 지원이 명시적으로 확인되지 않은 모델입니다.")
        if self.protocol not in {"ollama_native", "openai_chat_completions"}:
            raise ModelConfigurationError(f"지원하지 않는 공급자 프로토콜입니다: {self.protocol}")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelConfigurationError("유효한 HTTP(S) endpoint가 필요합니다.")
        if self.mode is AnalysisMode.LOCAL:
            if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ModelConfigurationError("로컬 모드는 loopback endpoint만 허용하며 외부로 전환하지 않습니다.")
        elif parsed.scheme != "https":
            raise ModelConfigurationError("외부 API endpoint는 HTTPS여야 합니다.")
        if self.api_key_env and not self.api_key_env.replace("_", "").isalnum():
            raise ModelConfigurationError("api_key_env 이름이 올바르지 않습니다.")


def config_from_environment(
    mode: AnalysisMode,
    model_override: str | None = None,
    provider_override: str | None = None,
    endpoint_override: str | None = None,
) -> ProviderConfig:
    if mode is AnalysisMode.LOCAL:
        config = ProviderConfig(
            mode=mode,
            provider_id=provider_override or os.environ.get("VIDEO_AGENT_LOCAL_PROVIDER", "ollama-local"),
            endpoint=endpoint_override
            or os.environ.get("VIDEO_AGENT_LOCAL_ENDPOINT", "http://127.0.0.1:11434/api/chat"),
            model_id=model_override or os.environ.get("VIDEO_AGENT_LOCAL_MODEL", ""),
            protocol=os.environ.get("VIDEO_AGENT_LOCAL_PROTOCOL", "ollama_native"),
            vision_capability_confirmed=_true(os.environ.get("VIDEO_AGENT_LOCAL_MODEL_SUPPORTS_VISION")),
        )
    elif mode is AnalysisMode.EXTERNAL:
        config = ProviderConfig(
            mode=mode,
            provider_id=provider_override or os.environ.get("VIDEO_AGENT_EXTERNAL_PROVIDER", ""),
            endpoint=endpoint_override or os.environ.get("VIDEO_AGENT_EXTERNAL_ENDPOINT", ""),
            model_id=model_override or os.environ.get("VIDEO_AGENT_EXTERNAL_MODEL", ""),
            protocol=os.environ.get("VIDEO_AGENT_EXTERNAL_PROTOCOL", "openai_chat_completions"),
            vision_capability_confirmed=_true(os.environ.get("VIDEO_AGENT_EXTERNAL_MODEL_SUPPORTS_VISION")),
            api_key_env=os.environ.get("VIDEO_AGENT_EXTERNAL_API_KEY_ENV", "VIDEO_AGENT_EXTERNAL_API_KEY"),
        )
    else:
        raise ModelConfigurationError("샘플 모드에는 실제 모델 설정이 없습니다.")
    config.validate()
    return config


def _true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
