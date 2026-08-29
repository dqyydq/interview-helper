import json
import uuid
from collections.abc import AsyncIterator

import pytest

from app.agents.input_budget import AgentInputBudgetError
from app.agents.question_researcher import (
    QuestionResearcherError,
    ResearcherSource,
    curate_questions,
)
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
)


class FakeProvider(ChatProvider):
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request.model_copy(deep=True))
        return ChatResponse(content=json.dumps(self.payloads.pop(0), ensure_ascii=False))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)


def _source() -> ResearcherSource:
    return ResearcherSource(
        source_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        title="LLM application interview notes",
        domain="example-public-site.dev",
        source_category="community_notes",
        excerpt=(
            "Candidates should explain retrieval evaluation, offline metrics, and failure analysis."
        ),
    )


def _payload(*, source_id: uuid.UUID | None = None, evidence: str | None = None) -> dict:
    source = _source()
    return {
        "candidates": [
            {
                "prompt": "How would you evaluate a retrieval pipeline before release?",
                "question_type": "system_design",
                "difficulty": "advanced",
                "suggested_tags": ["RAG", "evaluation"],
                "suggested_roles": ["LLM application engineer"],
                "suggested_skills": ["retrieval", "evaluation"],
                "applicable_companies": [],
                "applicable_rounds": ["technical-depth"],
                "reference_points": ["Offline metrics", "Failure analysis"],
                "follow_up_suggestions": ["How would you detect regression?"],
                "matching_reason": "Tests retrieval evaluation depth.",
                "confidence": 0.9,
                "evidence": [
                    {
                        "source_id": str(source_id or source.source_id),
                        "excerpt": evidence or "explain retrieval evaluation, offline metrics",
                        "source_locator": None,
                        "confidence": 0.8,
                    }
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_researcher_bounds_source_cards_and_materialises_grounded_candidates() -> None:
    provider = FakeProvider([_payload()])

    result = await curate_questions(
        provider,
        [_source()],
        query_context={"company": "byte-dance", "keywords": ["RAG"]},
        context_window_tokens=32_768,
        max_output_tokens=4_096,
        tokenizer_type="estimated",
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].evidence[0].source_id == _source().source_id
    assert provider.requests[0].max_tokens == 2_048
    payload = json.loads(provider.requests[0].messages[0].content)
    assert set(payload) == {"contract", "query_context", "sources"}
    assert payload["sources"][0]["excerpt"].startswith("Candidates should explain")
    assert "untrusted reference data" in (provider.requests[0].system or "")


@pytest.mark.asyncio
async def test_researcher_rejects_unknown_or_ungrounded_evidence() -> None:
    provider = FakeProvider(
        [
            _payload(source_id=uuid.UUID("22222222-2222-4222-8222-222222222222")),
            _payload(evidence="invented source claim"),
        ]
    )

    with pytest.raises(QuestionResearcherError, match="unknown source"):
        await curate_questions(
            provider,
            [_source()],
            context_window_tokens=32_768,
            max_output_tokens=4_096,
            tokenizer_type="estimated",
        )
    with pytest.raises(QuestionResearcherError, match="not grounded"):
        await curate_questions(
            provider,
            [_source()],
            context_window_tokens=32_768,
            max_output_tokens=4_096,
            tokenizer_type="estimated",
        )


@pytest.mark.asyncio
async def test_researcher_bounds_excerpts_and_rejects_oversized_context() -> None:
    provider = FakeProvider([_payload(evidence="x" * 20)])
    source = _source()
    bounded = ResearcherSource(
        source_id=source.source_id,
        title=source.title,
        domain=source.domain,
        source_category=source.source_category,
        excerpt="x" * 10_000,
    )

    result = await curate_questions(
        provider,
        [bounded],
        context_window_tokens=32_768,
        max_output_tokens=4_096,
        tokenizer_type="estimated",
        per_source_characters=100,
        total_excerpt_characters=100,
    )
    assert result.input_excerpt_characters == 100
    assert len(provider.requests) == 1

    with pytest.raises(AgentInputBudgetError, match="question_researcher input requires"):
        await curate_questions(
            FakeProvider([_payload()]),
            [bounded],
            context_window_tokens=1_024,
            max_output_tokens=256,
            tokenizer_type="estimated",
            per_source_characters=1_200,
            total_excerpt_characters=8_000,
        )
