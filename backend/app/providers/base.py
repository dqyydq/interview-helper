import json
import math
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Protocol, TypeVar

from pydantic import TypeAdapter, ValidationError

from app.db.models.common import MessageRole
from app.providers.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderHealth,
    StreamEvent,
)


class ProviderError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class UnsupportedCapabilityError(ProviderError):
    def __init__(self, capability: str) -> None:
        super().__init__(
            code="unsupported_capability",
            message=f"当前模型连接不支持 {capability}",
        )


class StructuredOutputError(ProviderError):
    def __init__(self) -> None:
        super().__init__(
            code="structured_output_invalid",
            message="模型未能返回有效的结构化结果",
            retryable=False,
        )


class TokenCounter(Protocol):
    def count_messages(self, messages: list[ChatMessage]) -> int: ...

    def count_text(self, text: str) -> int: ...


class ConservativeTokenCounter:
    """Provider-neutral estimate with a configurable safety margin."""

    def __init__(self, safety_margin: float = 1.2) -> None:
        if safety_margin < 1:
            raise ValueError("safety_margin must be at least 1")
        self.safety_margin = safety_margin

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        ascii_count = sum(character.isascii() for character in text)
        non_ascii_count = len(text) - ascii_count
        raw_estimate = math.ceil(ascii_count / 4) + math.ceil(non_ascii_count / 1.5)
        return math.ceil(raw_estimate * self.safety_margin)

    def count_messages(self, messages: list[ChatMessage]) -> int:
        content_tokens = sum(self.count_text(message.content) for message in messages)
        tool_tokens = sum(
            self.count_text(json.dumps(call.model_dump(), ensure_ascii=False))
            for message in messages
            for call in message.tool_calls
        )
        return content_tokens + tool_tokens + (len(messages) * 6)


class ChatProvider(ABC):
    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse: ...

    @abstractmethod
    def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]: ...

    @abstractmethod
    async def health_check(self) -> ProviderHealth: ...

    async def embeddings(self, request: EmbeddingRequest) -> EmbeddingResponse:
        raise UnsupportedCapabilityError("embeddings")


OutputT = TypeVar("OutputT")


class StructuredOutputRunner:
    def __init__(self, provider: ChatProvider, max_repairs: int = 1) -> None:
        if max_repairs < 0 or max_repairs > 3:
            raise ValueError("max_repairs must be between 0 and 3")
        self.provider = provider
        self.max_repairs = max_repairs

    async def run(self, request: ChatRequest, output_type: type[OutputT]) -> OutputT:
        output, _ = await self.run_with_response(request, output_type)
        return output

    async def run_with_response(
        self, request: ChatRequest, output_type: type[OutputT]
    ) -> tuple[OutputT, ChatResponse]:
        adapter = TypeAdapter(output_type)
        working_request = request.model_copy(deep=True)
        working_request.response_schema = adapter.json_schema()

        for attempt in range(self.max_repairs + 1):
            response = await self.provider.chat(working_request)
            try:
                payload = json.loads(response.content)
                return adapter.validate_python(payload), response
            except (json.JSONDecodeError, ValidationError):
                if attempt >= self.max_repairs:
                    break
                working_request.messages.extend(
                    [
                        ChatMessage(role=MessageRole.ASSISTANT, content=response.content),
                        ChatMessage(
                            role=MessageRole.USER,
                            content=(
                                "上一个结果不符合 JSON Schema。只返回修复后的 JSON，不要解释。"
                            ),
                        ),
                    ]
                )

        raise StructuredOutputError
