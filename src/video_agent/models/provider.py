from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from threading import Event
from typing import Any, Protocol

from video_agent.contracts import AnalysisMode
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
    metrics: "ProviderMetrics | None" = None


@dataclass(frozen=True, slots=True)
class ProviderMetrics:
    total_seconds: float | None = None
    load_seconds: float | None = None
    prompt_evaluation_seconds: float | None = None
    generation_seconds: float | None = None
    prompt_token_count: int | None = None
    output_token_count: int | None = None


class VisionProvider(Protocol):
    config: ProviderConfig

    def analyze(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
        *,
        timeout_seconds: float,
        cancel_event: Event,
        response_format: dict[str, Any] | None = None,
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

    def __init__(
        self,
        config: ProviderConfig,
        transport: JsonTransport | None = None,
        *,
        api_key: str | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._transport = transport or _urlopen_json
        self._api_key = (api_key or "").strip() or None

    def analyze(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
        *,
        timeout_seconds: float,
        cancel_event: Event,
        response_format: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        if cancel_event.is_set():
            raise OperationCanceled("모델 호출이 취소되었습니다.")
        if not frames:
            raise ModelConnectionError("영상 모델 입력 프레임이 없습니다.")

        headers = {"Content-Type": "application/json"}
        if self.config.api_key_env:
            api_key = self._api_key or os.environ.get(self.config.api_key_env)
            if not api_key:
                raise ModelConfigurationError(
                    f"외부 API 키 환경 변수 {self.config.api_key_env}가 설정되지 않았습니다."
                )
            headers["Authorization"] = f"Bearer {api_key}"

        payload = self.build_payload(prompt, frames, response_format=response_format)
        try:
            response = self._transport(self.config.endpoint, headers, payload, timeout_seconds)
        except urllib.error.HTTPError as exc:
            detail = _local_http_error_detail(exc) if self.config.mode is AnalysisMode.LOCAL else None
            message = f"모델 endpoint가 요청을 거부했습니다(HTTP {exc.code})"
            if detail:
                message += f": {detail}"
            raise ModelConnectionError(message) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelConnectionError(f"모델 endpoint 연결에 실패했습니다: {exc}") from exc
        if cancel_event.is_set():
            raise OperationCanceled("모델 호출 결과가 도착했지만 작업이 이미 취소되었습니다.")
        content = self._response_content(response)
        return ProviderResponse(
            self.config.provider_id,
            self.config.model_id,
            content,
            metrics=self._response_metrics(response),
        )

    def build_payload(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        encoded = [base64.b64encode(frame.path.read_bytes()).decode("ascii") for frame in frames]
        if self.config.protocol == "ollama_native":
            return {
                "model": self.config.model_id,
                "messages": [{"role": "user", "content": prompt, "images": encoded}],
                "format": response_format or response_schema(),
                "stream": False,
                "think": False,
                "options": {
                    "num_ctx": self.config.context_window_tokens,
                    "num_predict": self.config.max_output_tokens,
                    "temperature": self.config.temperature,
                    # The 8B model looped on one evidence sentence until the output limit
                    # cut the JSON; this discourages repeats (with the length limits below).
                    "repeat_penalty": 1.15,
                },
            }

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{frame.mime_type};base64,{image}"},
            }
            for frame, image in zip(frames, encoded, strict=True)
        )
        payload = {
            "model": self.config.model_id,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": self.config.temperature,
        }
        if self.config.max_output_tokens is not None:
            payload["max_completion_tokens"] = self.config.max_output_tokens
        return payload

    def _response_content(self, response: dict[str, Any]) -> str:
        try:
            if self.config.protocol == "ollama_native":
                message = response["message"]
                content = message["content"]
            else:
                message = None
                content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelConnectionError("모델 응답에 텍스트 content가 없습니다.") from exc
        if not isinstance(content, str) or not content.strip():
            thinking = message.get("thinking") if isinstance(message, dict) else None
            if isinstance(thinking, str) and thinking.strip():
                raise ModelConnectionError(
                    "모델이 최종 JSON content 없이 thinking만 반환했습니다. "
                    "현재 앱은 thinking을 분석 결과로 사용하지 않으므로 비사고형 instruct 모델을 사용해 주세요."
                )
            raise ModelConnectionError("모델 응답 content가 비어 있습니다.")
        return content

    def _response_metrics(self, response: dict[str, Any]) -> ProviderMetrics | None:
        if self.config.protocol != "ollama_native":
            return None
        metrics = ProviderMetrics(
            total_seconds=_nanoseconds_to_seconds(response.get("total_duration")),
            load_seconds=_nanoseconds_to_seconds(response.get("load_duration")),
            prompt_evaluation_seconds=_nanoseconds_to_seconds(response.get("prompt_eval_duration")),
            generation_seconds=_nanoseconds_to_seconds(response.get("eval_duration")),
            prompt_token_count=_nonnegative_int(response.get("prompt_eval_count")),
            output_token_count=_nonnegative_int(response.get("eval_count")),
        )
        values = (
            metrics.total_seconds,
            metrics.load_seconds,
            metrics.prompt_evaluation_seconds,
            metrics.generation_seconds,
            metrics.prompt_token_count,
            metrics.output_token_count,
        )
        return metrics if any(value is not None for value in values) else None


class FakeVisionProvider:
    """Test double only; never presented as a real model connection."""

    def __init__(self, config: ProviderConfig, content: str) -> None:
        config.validate()
        self.config = config
        self._content = content
        self.calls = 0
        self.timeouts: list[float] = []

    def analyze(
        self,
        prompt: str,
        frames: tuple[ExtractedFrame, ...],
        *,
        timeout_seconds: float,
        cancel_event: Event,
        response_format: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        del response_format
        if cancel_event.is_set():
            raise OperationCanceled("가짜 공급자 호출이 취소되었습니다.")
        self.calls += 1
        self.timeouts.append(timeout_seconds)
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
                # Short, bounded answers: fewer candidates and capped sentences keep the
                # output well inside the token limit (a cut answer is invalid JSON).
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "start_sec": {"type": "number"},
                        "end_sec": {"type": "number"},
                        "summary": {
                            "type": "string",
                            "maxLength": 120,
                            "description": "화면에서 관찰한 행동이나 상태의 짧은 요약",
                        },
                        "evidence": {
                            "type": "array",
                            "maxItems": 3,
                            "description": (
                                "프레임 ID가 아닌, 화면에서 실제로 보이는 자세·신체 부위·물체·변화. "
                                "지속 시간이 핵심인 목적은 복수 관찰 시각과 표본 사이 경과 시간을 포함. "
                                "반복·방향 변화 목적은 서로 다른 시각에서 보이는 방향을 각각 포함"
                            ),
                            "items": {"type": "string", "maxLength": 120},
                        },
                        "source_frame_ids": {
                            "type": "array",
                            "description": "관찰 근거로 실제 사용한 입력 프레임 ID",
                            "items": {"type": "string"},
                        },
                        "uncertainty": {
                            "type": "string",
                            "maxLength": 120,
                            "description": "희소 프레임과 가려짐 등 사람이 다시 확인할 한계",
                        },
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


def _local_http_error_detail(error: urllib.error.HTTPError) -> str | None:
    """Return only Ollama's short error field; never echo an arbitrary response body."""
    try:
        payload = json.loads(error.read(8_193).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    detail = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(detail, str):
        return None
    compact = " ".join(detail.split())
    return compact[:500] or None


def _nanoseconds_to_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value) / 1_000_000_000


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
