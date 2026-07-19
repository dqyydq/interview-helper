import json
import uuid
from collections.abc import AsyncIterator

import pytest

from app.agents.evaluator import SYSTEM_PROMPT, run_evaluator
from app.db.models.common import EvaluationAnchor
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
)


class FakeProvider(ChatProvider):
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request.model_copy(deep=True))
        return ChatResponse(content=json.dumps(self.responses.pop(0)))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)


def _draft(question_id: uuid.UUID, message_id: uuid.UUID) -> dict:
    return {
        "overall_anchor": "solid",
        "overview": "回答有清晰证据。",
        "strengths": ["结构清晰"],
        "gaps": ["容量估算不足"],
        "action_plan": [
            {
                "title": "补充容量估算",
                "instruction": "练习量级推导。",
                "success_criteria": "三分钟内完成 QPS 与存储估算。",
                "priority": 1,
            }
        ],
        "questions": [
            {
                "plan_question_id": str(question_id),
                "anchor": "solid",
                "summary": "能够解释核心取舍。",
                "evidence": [{"message_id": str(message_id), "claim": "解释了取舍"}],
                "gaps": [],
                "actions": ["补充数字"],
                "confidence": 0.8,
            }
        ],
        "dimensions": [
            {
                "dimension": "technical_depth",
                "anchor": "solid",
                "evidence": [{"message_id": str(message_id), "claim": "解释了取舍"}],
                "gaps": [],
                "action": "继续练习边界条件。",
                "confidence": 0.8,
            }
        ],
    }


@pytest.mark.asyncio
async def test_evaluator_repairs_an_invalid_message_reference_once() -> None:
    question_id = uuid.uuid4()
    valid_message_id = uuid.uuid4()
    invalid = _draft(question_id, uuid.uuid4())
    valid = _draft(question_id, valid_message_id)
    provider = FakeProvider([invalid, valid])

    result = await run_evaluator(
        provider,
        evaluation_payload={"interview_messages": []},
        question_message_ids={question_id: {valid_message_id}},
        expected_dimensions=["technical_depth"],
    )

    assert result.overall_anchor == EvaluationAnchor.SOLID
    assert result.questions[0].evidence[0].message_id == valid_message_id
    assert len(provider.requests) == 2
    assert "校验错误" in provider.requests[1].messages[-1].content


def test_evaluator_prompt_forbids_summary_evidence_and_offer_probability() -> None:
    assert "摘要不能作为评分证据" in SYSTEM_PROMPT
    assert "不要给 Offer 概率" in SYSTEM_PROMPT
