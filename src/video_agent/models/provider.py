from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from threading import Event
from typing import Any, Protocol

from video_agent.models.config import ModelConfigurationError, ProviderConfig
from video_agent.video.frames import ExtractedFrame
from video_agent.video.process import OperationCanceled


class ModelConnectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider_id: str
    model_id: str
    content: str
    is_test_double: bool = False


class VisionProvider(Protocol):
    config: ProviderConfig

    def analyze(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
        *,
        timeout_seconds: float,
        cancel_event: Event,
    ) -> ProviderResponse: ...


class JsonTransport(Protocol):
    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class HttpVisionProvider:
    """HTTP adapter for Ollama-native or OpenAI-compatible vision endpoints."""

    def __init__(self, config: ProviderConfig, transport: JsonTransport | None = None) -> None:
        config.validate()
        self.config = config
        self._transport = transport or _urlopen_json

    def analyze(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
        *,
        timeout_seconds: float,
        cancel_event: Event,
    ) -> ProviderResponse:
        if cancel_event.is_set():
            raise OperationCanceled("모델 호출이 취소되었습니다.")
        if not frames:
            raise ModelConnectionError("영상 모델 입력 프레임이 없습니다.")

        headers = {"Content-Type": "application/json"}
        if self.config.api_key_env:
            api_key = os.environ.get(self.config.api_key_env)
            if not api_key:
                raise ModelConfigurationError(
                    f"외부 API 키 환경 변수 {self.config.api_key_env}가 설정되지 않았습니다."
                )
            headers["Authorization"] = f"Bearer {api_key}"

        payload = self.build_payload(prompt, frames)
        try:
            response = self._transport(self.config.endpoint, headers, payload, timeout_seconds)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelConnectionError(f"모델 endpoint 연결에 실패했습니다: {exc}") from exc
        if cancel_event.is_set():
            raise OperationCanceled("모델 호출 결과가 도착했지만 작업이 이미 취소되었습니다.")
        content = self._response_content(response)
        return ProviderResponse(self.config.provider_id, self.config.model_id, content)

    def build_payload(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
    ) -> dict[str, Any]:
        encoded = [base64.b64encode(frame.path.read_bytes()).decode("ascii") for frame in frames]
        if self.config.protocol == "ollama_native":
            return {
                "model": self.config.model_id,
                "messages": [{"role": "user", "content": prompt, "images": encoded}],
                "format": response_schema(),
                "stream": False,
            }

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{frame.mime_type};base64,{image}"},
            }
            for frame, image in zip(frames, encoded, strict=True)
        )
        return {
            "model": self.config.model_id,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }

    def _response_content(self, response: dict[str, Any]) -> str:
        try:
            if self.config.protocol == "ollama_native":
                content = response["message"]["content"]
            else:
                content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelConnectionError("모델 응답에 텍스트 content가 없습니다.") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelConnectionError("모델 응답 content가 비어 있습니다.")
        return content


class FakeVisionProvider:
    """Test double only; never presented as a real model connection."""

    def __init__(self, config: ProviderConfig, content: str) -> None:
        config.validate()
        self.config = config
        self._content = content
        self.calls = 0

    def analyze(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
        *,
        timeout_seconds: float,
        cancel_event: Event,
    ) -> ProviderResponse:
        if cancel_event.is_set():
            raise OperationCanceled("가짜 공급자 호출이 취소되었습니다.")
        self.calls += 1
        return ProviderResponse(
            provider_id="test-fake-provider",
            model_id=self.config.model_id,
            content=self._content,
            is_test_double=True,
        )


def response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_sec": {"type": "number"},
                        "end_sec": {"type": "number"},
                        "summary": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "source_frame_ids": {"type": "array", "items": {"type": "string"}},
                        "uncertainty": {"type": "string"},
                    },
                    "required": [
                        "start_sec",
                        "end_sec",
                        "summary",
                        "evidence",
                        "source_frame_ids",
                        "uncertainty",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["segments"],
        "additionalProperties": False,
    }


def _urlopen_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise ModelConnectionError("모델 응답이 허용 크기를 초과했습니다.")
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelConnectionError("모델 endpoint가 유효한 JSON을 반환하지 않았습니다.") from exc
        if not isinstance(result, dict):
            raise ModelConnectionError("모델 endpoint 응답의 최상위 형식이 객체가 아닙니다.")
        return result
