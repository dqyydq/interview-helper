import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.db.models.common import MessageRole
from app.providers.base import ChatProvider
from app.providers.http import (
    elapsed_ms,
    provider_error_from_response,
    provider_transport_error,
    trust_environment_for_provider_url,
)
from app.providers.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
    StreamEventType,
    ToolCall,
    Usage,
)


class OpenAICompatibleProvider(ChatProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.extra_headers = extra_headers or {}
        self.client = client or httpx.AsyncClient(
            trust_env=trust_environment_for_provider_url(base_url)
        )
        self._owns_client = client is None

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self.extra_headers,
            "Authorization": f"Bearer {self.api_key}",
        }

    @staticmethod
    def _message(message: ChatMessage) -> dict[str, Any]:
        content: str | list[dict[str, Any]] = message.content
        if message.images:
            content = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": image.openai_image_url()},
                }
                for image in message.images
            )
        payload: dict[str, Any] = {"role": message.role.value, "content": content}
        if message.name:
            payload["name"] = message.name
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        return payload

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        messages = [self._message(message) for message in request.messages]
        if request.system:
            messages.insert(0, {"role": MessageRole.SYSTEM.value, "content": request.system})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        if request.response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "structured_output", "schema": request.response_schema},
            }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    @staticmethod
    def _usage(payload: dict[str, Any] | None) -> Usage:
        payload = payload or {}
        details = payload.get("prompt_tokens_details") or {}
        return Usage(
            input_tokens=payload.get("prompt_tokens", 0),
            output_tokens=payload.get("completion_tokens", 0),
            cached_input_tokens=details.get("cached_tokens", 0),
        )

    @staticmethod
    def _tool_calls(payload: list[dict[str, Any]] | None) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for item in payload or []:
            function = item.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": raw_arguments}
            calls.append(
                ToolCall(
                    id=item.get("id") or "unknown",
                    name=function.get("name") or "unknown",
                    arguments=arguments,
                )
            )
        return calls

    async def chat(self, request: ChatRequest) -> ChatResponse:
        try:
            response = await self.client.post(
                self.endpoint,
                headers=self._headers(),
                json=self._payload(request, stream=False),
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise provider_transport_error(exc) from exc
        if response.is_error:
            raise provider_error_from_response(response)

        payload = response.json()
        choice = payload.get("choices", [{}])[0]
        message = choice.get("message") or {}
        return ChatResponse(
            content=message.get("content") or "",
            tool_calls=self._tool_calls(message.get("tool_calls")),
            usage=self._usage(payload.get("usage")),
            finish_reason=choice.get("finish_reason"),
            provider_request_id=payload.get("id") or response.headers.get("x-request-id"),
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        finish_reason: str | None = None
        tool_buffers: dict[int, dict[str, str]] = {}
        try:
            async with self.client.stream(
                "POST",
                self.endpoint,
                headers=self._headers(),
                json=self._payload(request, stream=True),
                timeout=self.timeout_seconds,
            ) as response:
                if response.is_error:
                    error = provider_error_from_response(response)
                    yield StreamEvent(
                        type=StreamEventType.FAILED,
                        error_code=error.code,
                        retryable=error.retryable,
                    )
                    return
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        for buffer in tool_buffers.values():
                            raw_arguments = buffer.get("arguments", "") or "{}"
                            try:
                                arguments = json.loads(raw_arguments)
                            except json.JSONDecodeError:
                                arguments = {"_raw": raw_arguments}
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL,
                                tool_call=ToolCall(
                                    id=buffer.get("id") or "unknown",
                                    name=buffer.get("name") or "unknown",
                                    arguments=arguments,
                                ),
                            )
                        yield StreamEvent(
                            type=StreamEventType.COMPLETED,
                            finish_reason=finish_reason,
                        )
                        return
                    payload = json.loads(data)
                    if payload.get("usage"):
                        yield StreamEvent(
                            type=StreamEventType.USAGE,
                            usage=self._usage(payload["usage"]),
                        )
                    for choice in payload.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield StreamEvent(
                                type=StreamEventType.TEXT_DELTA,
                                text=delta["content"],
                            )
                        for call_delta in delta.get("tool_calls") or []:
                            index = int(call_delta.get("index", 0))
                            buffer = tool_buffers.setdefault(
                                index,
                                {"id": "", "name": "", "arguments": ""},
                            )
                            function = call_delta.get("function") or {}
                            buffer["id"] = call_delta.get("id") or buffer["id"]
                            buffer["name"] = function.get("name") or buffer["name"]
                            buffer["arguments"] += function.get("arguments") or ""
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                yield StreamEvent(
                    type=StreamEventType.COMPLETED,
                    finish_reason=finish_reason,
                )
        except httpx.HTTPError as exc:
            error = provider_transport_error(exc)
            yield StreamEvent(
                type=StreamEventType.FAILED,
                error_code=error.code,
                retryable=error.retryable,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            yield StreamEvent(
                type=StreamEventType.FAILED,
                error_code="provider_invalid_response",
            )

    async def health_check(self) -> ProviderHealth:
        started_at = time.perf_counter()
        try:
            await self.chat(
                ChatRequest(
                    messages=[ChatMessage(role=MessageRole.USER, content="ping")],
                    max_tokens=1,
                    temperature=0,
                )
            )
        except Exception as exc:
            error_code = getattr(exc, "code", "provider_unavailable")
            return ProviderHealth(
                status=ProviderHealthStatus.DEGRADED,
                latency_ms=elapsed_ms(started_at),
                error_code=error_code,
            )
        return ProviderHealth(
            status=ProviderHealthStatus.HEALTHY,
            latency_ms=elapsed_ms(started_at),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
