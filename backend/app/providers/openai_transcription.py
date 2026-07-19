from typing import Any

import httpx

from app.providers.base import ProviderError
from app.providers.http import provider_error_from_response, provider_transport_error
from app.providers.speech_base import (
    SpeechToTextProvider,
    TranscriptionRequest,
    TranscriptionResult,
)


class OpenAICompatibleTranscriptionProvider(SpeechToTextProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        extra_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.extra_headers = extra_headers or {}
        self.client = client or httpx.AsyncClient()
        self._owns_client = client is None

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/audio/transcriptions"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            **self.extra_headers,
            "Authorization": f"Bearer {self.api_key}",
        }

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        if not request.audio:
            raise ProviderError(code="transcription_audio_empty", message="录音内容为空")
        data: dict[str, str] = {"model": self.model, "response_format": "json"}
        if request.language:
            data["language"] = request.language
        if request.prompt:
            data["prompt"] = request.prompt
        try:
            response = await self.client.post(
                self.endpoint,
                headers=self._headers(),
                files={
                    "file": (
                        request.filename,
                        request.audio,
                        request.content_type,
                    )
                },
                data=data,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise provider_transport_error(exc) from exc
        if response.is_error:
            raise provider_error_from_response(response)
        try:
            payload: dict[str, Any] = response.json()
            text = str(payload.get("text") or "").strip()
            if not text:
                raise ValueError("missing transcription text")
            duration = payload.get("duration")
            return TranscriptionResult(
                text=text,
                language=payload.get("language") or request.language,
                duration_seconds=float(duration) if duration is not None else None,
                provider_request_id=payload.get("id") or response.headers.get("x-request-id"),
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                code="provider_invalid_response",
                message="转写服务返回了无效结果",
            ) from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
