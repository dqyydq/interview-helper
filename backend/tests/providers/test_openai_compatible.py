import json

import httpx
import pytest

from app.db.models.common import MessageRole
from app.providers.base import ProviderError
from app.providers.http import trust_environment_for_provider_url
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.types import ChatMessage, ChatRequest, StreamEventType, ToolDefinition


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:8010/v1", False),
        ("http://[::1]:8010/v1", False),
        ("http://localhost:8010/v1", False),
        ("https://api.openai.com/v1", True),
    ],
)
def test_loopback_provider_urls_bypass_environment_proxy(base_url: str, expected: bool) -> None:
    assert trust_environment_for_provider_url(base_url) is expected


@pytest.mark.asyncio
async def test_openai_chat_converts_internal_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload["model"] == "gpt-test"
        assert payload["messages"][0] == {"role": "system", "content": "be precise"}
        assert payload["tools"][0]["function"]["name"] == "lookup"
        return httpx.Response(
            200,
            json={
                "id": "response-1",
                "choices": [
                    {
                        "message": {"content": "hello", "tool_calls": []},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="gpt-test",
            client=client,
        )
        response = await provider.chat(
            ChatRequest(
                system="be precise",
                messages=[ChatMessage(role=MessageRole.USER, content="hi")],
                tools=[ToolDefinition(name="lookup", input_schema={"type": "object"})],
            )
        )

    assert response.content == "hello"
    assert response.usage.total_tokens == 15
    assert response.provider_request_id == "response-1"


@pytest.mark.asyncio
async def test_openai_stream_normalizes_text_usage_and_completion() -> None:
    stream_body = "\n\n".join(
        [
            'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2}}',
            "data: [DONE]",
        ]
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=stream_body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="gpt-test",
            client=client,
        )
        events = [
            event
            async for event in provider.stream_chat(
                ChatRequest(messages=[ChatMessage(role=MessageRole.USER, content="hi")])
            )
        ]

    assert [event.type for event in events] == [
        StreamEventType.TEXT_DELTA,
        StreamEventType.TEXT_DELTA,
        StreamEventType.USAGE,
        StreamEventType.COMPLETED,
    ]
    assert "".join(event.text or "" for event in events) == "你好"


@pytest.mark.asyncio
async def test_openai_stream_assembles_tool_argument_fragments() -> None:
    stream_body = "\n\n".join(
        [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t1",'
            '"function":{"name":"lookup","arguments":"{\\"q\\":"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"\\"python\\"}"}}]},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ]
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=stream_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="gpt-test",
            client=client,
        )
        events = [
            event
            async for event in provider.stream_chat(
                ChatRequest(messages=[ChatMessage(role=MessageRole.USER, content="hi")])
            )
        ]

    assert events[-2].tool_call is not None
    assert events[-2].tool_call.arguments == {"q": "python"}
    assert events[-1].type is StreamEventType.COMPLETED


@pytest.mark.asyncio
async def test_openai_errors_do_not_expose_provider_body_or_key() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret provider detail test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="gpt-test",
            client=client,
        )
        with pytest.raises(ProviderError) as exc_info:
            await provider.chat(
                ChatRequest(messages=[ChatMessage(role=MessageRole.USER, content="hi")])
            )

    assert exc_info.value.code == "provider_authentication_failed"
    assert "test-key" not in str(exc_info.value)
    assert "provider detail" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_stream_emits_sanitized_failed_event() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="private account detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="gpt-test",
            client=client,
        )
        events = [
            event
            async for event in provider.stream_chat(
                ChatRequest(messages=[ChatMessage(role=MessageRole.USER, content="hi")])
            )
        ]

    assert len(events) == 1
    assert events[0].type is StreamEventType.FAILED
    assert events[0].error_code == "provider_rate_limited"
    assert events[0].retryable is True
