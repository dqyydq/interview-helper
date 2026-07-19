import json
import uuid
from collections.abc import AsyncIterator

import pytest

from app.agents.coach import SYSTEM_PROMPT, run_coach
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
)


class FakeCoachProvider(ChatProvider):
    def __init__(self, response: dict) -> None:
        self.response = response
        self.request: ChatRequest | None = None

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.request = request
        return ChatResponse(content=json.dumps(self.response))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)


@pytest.mark.asyncio
async def test_rewrite_keeps_original_and_suggestion_separate() -> None:
    message_id = uuid.uuid4()
    provider = FakeCoachProvider(
        {
            "mode": "rewrite",
            "title": "更完整的系统设计回答",
            "explanation": "保留原答案的主线并补充量级。",
            "original_answer": "我会使用消息队列。",
            "suggested_answer": "原回答可以扩展为：先估算吞吐，再选择消息队列。",
            "practice_prompts": [],
            "source_message_ids": [str(message_id)],
        }
    )

    result = await run_coach(
        provider,
        mode="rewrite",
        report_context={"original_answers": [{"message_id": str(message_id)}]},
        allowed_message_ids={message_id},
    )

    assert result.original_answer != result.suggested_answer
    assert result.source_message_ids == [message_id]


def test_coach_prompt_has_strict_context_boundary() -> None:
    assert "只使用输入中的评估报告和必要的原始回答片段" in SYSTEM_PROMPT
    assert "建议答案不能伪装成用户说过的话" in SYSTEM_PROMPT
