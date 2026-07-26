import json

import httpx
import pytest

from app.db.models.common import MessageRole
from app.providers.anthropic_compatible import AnthropicCompatibleProvider
from app.providers.base import ProviderError
from app.providers.types import (
    ChatImage,
    ChatMessage,
    ChatRequest,
    ImageMediaType,
    StreamEventType,
    ToolCall,
)


@pytest.mark.asyncio
async def test_anthropic_chat_converts_system_and_tool_blocks() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "anthropic-key"
        assert payload["system"] == "system instruction"
        assert payload["messages"][0]["content"] == "question"
        assert payload["messages"][1]["content"][1]["type"] == "tool_use"
        return httpx.Response(
            200,
            json={
                "id": "message-1",
                "content": [
                    {"type": "text", "text": "result"},
                    {"type": "tool_use", "id": "tool-2", "name": "lookup", "input": {"q": "x"}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicCompatibleProvider(
            base_url="https://anthropic.test/v1",
            api_key="anthropic-key",
            model="claude-test",
            client=client,
        )
        response = await provider.chat(
            ChatRequest(
                system="system instruction",
                messages=[
                    ChatMessage(role=MessageRole.USER, content="question"),
                    ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content="checking",
                        tool_calls=[ToolCall(id="tool-1", name="lookup", arguments={"q": "x"})],
                    ),
                ],
            )
        )

    assert response.content == "result"
    assert response.tool_calls[0].name == "lookup"
    assert response.usage.total_tokens == 14


@pytest.mark.asyncio
async def test_anthropic_chat_converts_url_and_base64_images_to_content_blocks() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["messages"] == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请阅读这两张资料图"},
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": "https://assets.example.org/interview.png",
                        },
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "aGVsbG8=",
                        },
                    },
                ],
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "message-vision-1",
                "content": [{"type": "text", "text": "已解析"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicCompatibleProvider(
            base_url="https://anthropic.test/v1",
            api_key="anthropic-key",
            model="vision-test",
            client=client,
        )
        response = await provider.chat(
            ChatRequest(
                messages=[
                    ChatMessage(
                        role=MessageRole.USER,
                        content="请阅读这两张资料图",
                        images=[
                            ChatImage(
                                source_type="url",
                                url="https://assets.example.org/interview.png",
                            ),
                            ChatImage(
                                source_type="base64",
                                media_type=ImageMediaType.PNG,
                                data="aGVsbG8=",
                            ),
                        ],
                    )
                ]
            )
        )

    assert response.content == "已解析"


@pytest.mark.asyncio
async def test_anthropic_stream_normalizes_events() -> None:
    stream_body = "\n\n".join(
        [
            'event: message_start\ndata: {"message":{"usage":{"input_tokens":8}}}',
            'event: content_block_delta\ndata: {"delta":{"type":"text_delta","text":"Hello"}}',
            "event: content_block_start\ndata: "
            '{"index":1,"content_block":{"type":"tool_use","id":"t1",'
            '"name":"lookup","input":{}}}',
            'event: content_block_delta\ndata: {"index":1,"delta":'
            '{"type":"input_json_delta","partial_json":"{\\"q\\":\\"x\\"}"}}',
            'event: content_block_stop\ndata: {"index":1}',
            'event: message_delta\ndata: {"usage":{"output_tokens":3}}',
            "event: message_stop\ndata: {}",
        ]
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=stream_body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicCompatibleProvider(
            base_url="https://anthropic.test/v1",
            api_key="anthropic-key",
            model="claude-test",
            client=client,
        )
        events = [
            event
            async for event in provider.stream_chat(
                ChatRequest(messages=[ChatMessage(role=MessageRole.USER, content="hi")])
            )
        ]

    assert [event.type for event in events] == [
        StreamEventType.USAGE,
        StreamEventType.TEXT_DELTA,
        StreamEventType.TOOL_CALL,
        StreamEventType.USAGE,
        StreamEventType.COMPLETED,
    ]
    assert events[2].tool_call is not None
    assert events[2].tool_call.arguments == {"q": "x"}


@pytest.mark.asyncio
async def test_anthropic_rate_limit_is_retryable_and_sanitized() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="internal quota account detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicCompatibleProvider(
            base_url="https://anthropic.test/v1",
            api_key="anthropic-key",
            model="claude-test",
            client=client,
        )
        with pytest.raises(ProviderError) as exc_info:
            await provider.chat(
                ChatRequest(messages=[ChatMessage(role=MessageRole.USER, content="hi")])
            )

    assert exc_info.value.code == "provider_rate_limited"
    assert exc_info.value.retryable is True
    assert "account detail" not in str(exc_info.value)
