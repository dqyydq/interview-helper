from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel

from app.db.models.common import MessageRole
from app.providers.base import (
    ChatProvider,
    ConservativeTokenCounter,
    StructuredOutputRunner,
    UnsupportedCapabilityError,
)
from app.providers.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
    StreamEventType,
    Usage,
)


class Result(BaseModel):
    verdict: str
    score: int


class FakeProvider(ChatProvider):
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(content=self.responses.pop(0))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok")
        yield StreamEvent(type=StreamEventType.COMPLETED)

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)


def test_conservative_token_counter_adds_protocol_and_safety_margin() -> None:
    counter = ConservativeTokenCounter(safety_margin=1.2)
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content="You are an interviewer."),
        ChatMessage(role=MessageRole.USER, content="解释一下事件循环"),
    ]

    assert counter.count_messages(messages) > counter.count_text("解释一下事件循环")
    assert counter.count_text("") == 0


@pytest.mark.asyncio
async def test_structured_output_is_repaired_once_and_validated() -> None:
    provider = FakeProvider(["not-json", '{"verdict":"solid","score":4}'])
    runner = StructuredOutputRunner(provider, max_repairs=1)
    request = ChatRequest(messages=[ChatMessage(role=MessageRole.USER, content="evaluate")])

    result = await runner.run(request, Result)

    assert result == Result(verdict="solid", score=4)
    assert len(provider.requests) == 2
    assert provider.requests[0].response_schema is not None
    assert "只返回修复后的 JSON" in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_structured_output_can_return_provider_usage() -> None:
    provider = FakeProvider(['{"verdict":"solid","score":4}'])

    async def chat_with_usage(request: ChatRequest) -> ChatResponse:
        provider.requests.append(request)
        return ChatResponse(
            content=provider.responses.pop(0),
            usage=Usage(input_tokens=24, output_tokens=8),
            provider_request_id="request-1",
        )

    provider.chat = chat_with_usage  # type: ignore[method-assign]
    result, response = await StructuredOutputRunner(provider).run_with_response(
        ChatRequest(messages=[ChatMessage(role=MessageRole.USER, content="evaluate")]),
        Result,
    )

    assert result.verdict == "solid"
    assert response.usage.total_tokens == 32
    assert response.provider_request_id == "request-1"


@pytest.mark.asyncio
async def test_embeddings_are_explicitly_optional() -> None:
    provider = FakeProvider([])

    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        await provider.embeddings(request={"texts": ["hello"]})  # type: ignore[arg-type]

    assert exc_info.value.code == "unsupported_capability"
