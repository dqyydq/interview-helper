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


class AnthropicCompatibleProvider(ChatProvider):
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
        return f"{self.base_url}/messages"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self.extra_headers,
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    @staticmethod
    def _content(message: ChatMessage) -> str | list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        blocks.extend(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
            for call in message.tool_calls
        )
        if message.role is MessageRole.TOOL:
            return [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "unknown",
                    "content": message.content,
                }
            ]
        return blocks if message.tool_calls else message.content

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        system_parts = [request.system] if request.system else []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is MessageRole.SYSTEM:
                system_parts.append(message.content)
                continue
            role = "user" if message.role is MessageRole.TOOL else message.role.value
            messages.append({"role": role, "content": self._content(message)})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n\n".join(part for part in system_parts if part)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]
        return payload

    @staticmethod
    def _usage(payload: dict[str, Any] | None) -> Usage:
        payload = payload or {}
        return Usage(
            input_tokens=payload.get("input_tokens", 0),
            output_tokens=payload.get("output_tokens", 0),
            cached_input_tokens=payload.get("cache_read_input_tokens", 0),
        )

    @staticmethod
    def _content_response(blocks: list[dict[str, Any]]) -> tuple[str, list[ToolCall]]:
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        calls = [
            ToolCall(
                id=block.get("id") or "unknown",
                name=block.get("name") or "unknown",
                arguments=block.get("input") or {},
            )
            for block in blocks
            if block.get("type") == "tool_use"
        ]
        return text, calls

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
        text, calls = self._content_response(payload.get("content") or [])
        return ChatResponse(
            content=text,
            tool_calls=calls,
            usage=self._usage(payload.get("usage")),
            finish_reason=payload.get("stop_reason"),
            provider_request_id=payload.get("id") or response.headers.get("request-id"),
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        current_event = ""
        tool_buffers: dict[int, dict[str, Any]] = {}
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
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = json.loads(line[5:].strip())
                    if current_event == "content_block_delta":
                        delta = payload.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield StreamEvent(
                                type=StreamEventType.TEXT_DELTA,
                                text=delta.get("text", ""),
                            )
                        elif delta.get("type") == "input_json_delta":
                            index = int(payload.get("index", 0))
                            if index in tool_buffers:
                                tool_buffers[index]["arguments"] += delta.get("partial_json", "")
                    elif current_event == "content_block_start":
                        block = payload.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            index = int(payload.get("index", 0))
                            tool_buffers[index] = {
                                "id": block.get("id") or "unknown",
                                "name": block.get("name") or "unknown",
                                "arguments": "",
                                "initial_input": block.get("input") or {},
                            }
                    elif current_event == "content_block_stop":
                        index = int(payload.get("index", 0))
                        buffer = tool_buffers.pop(index, None)
                        if buffer:
                            raw_arguments = buffer["arguments"]
                            try:
                                arguments = (
                                    json.loads(raw_arguments)
                                    if raw_arguments
                                    else buffer["initial_input"]
                                )
                            except json.JSONDecodeError:
                                arguments = {"_raw": raw_arguments}
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL,
                                tool_call=ToolCall(
                                    id=buffer["id"],
                                    name=buffer["name"],
                                    arguments=arguments,
                                ),
                            )
                    elif current_event == "message_start":
                        usage = (payload.get("message") or {}).get("usage")
                        if usage:
                            yield StreamEvent(
                                type=StreamEventType.USAGE,
                                usage=self._usage(usage),
                            )
                    elif current_event == "message_delta":
                        usage = payload.get("usage")
                        if usage:
                            yield StreamEvent(
                                type=StreamEventType.USAGE,
                                usage=self._usage(usage),
                            )
                    elif current_event == "message_stop":
                        yield StreamEvent(type=StreamEventType.COMPLETED)
                        return
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
