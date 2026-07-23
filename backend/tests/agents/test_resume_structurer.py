import json
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from app.agents.input_budget import AgentInputBudgetError
from app.agents.resume_structurer import ResumeStructureError, structure_resume_with_planner
from app.db.models.resume import Resume
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
)
from app.services.resume_parser import ParsedSection
from app.workers import resume_jobs


class FakeProvider(ChatProvider):
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request.model_copy(deep=True))
        return ChatResponse(content=json.dumps(self.payload))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)


class SequenceProvider(ChatProvider):
    """Return distinct responses so structured-output repair paths can be exercised."""

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


def _source_sections() -> list[ParsedSection]:
    return [
        ParsedSection(
            section_type="general",
            heading="Experience",
            content="- Built a FastAPI interview application\n- Used PostgreSQL for jobs",
            sequence=1,
        )
    ]


@pytest.mark.asyncio
async def test_resume_structurer_persists_only_source_grounded_sections_and_claims() -> None:
    provider = FakeProvider(
        {
            "sections": [
                {"source_sequence": 1, "section_type": "experience", "heading": "Experience"}
            ],
            "claims": [
                {
                    "source_sequence": 1,
                    "line": 1,
                    "claim_type": "project_achievement",
                    "content": "Built a FastAPI interview application",
                    "confidence": 0.9,
                }
            ],
        }
    )

    result = await structure_resume_with_planner(
        provider,
        _source_sections(),
        context_window_tokens=32_768,
        max_output_tokens=2_048,
        tokenizer_type="estimated",
    )

    assert result.sections[0].section_type == "experience"
    assert result.sections[0].content.startswith("- Built")
    assert result.claims[0].content == "Built a FastAPI interview application"
    assert result.claims[0].source_span == {"line": 1}
    assert provider.requests[0].response_schema
    assert provider.requests[0].max_tokens == 2_048
    assert "untrusted reference data" in (provider.requests[0].system or "")


@pytest.mark.asyncio
async def test_resume_structurer_rejects_hallucinated_claims() -> None:
    provider = FakeProvider(
        {
            "sections": [
                {"source_sequence": 1, "section_type": "experience", "heading": "Experience"}
            ],
            "claims": [
                {
                    "source_sequence": 1,
                    "line": 1,
                    "claim_type": "project_achievement",
                    "content": "Led a team of 20 engineers",
                    "confidence": 0.9,
                }
            ],
        }
    )

    with pytest.raises(ResumeStructureError, match="verbatim source line"):
        await structure_resume_with_planner(
            provider,
            _source_sections(),
            context_window_tokens=32_768,
            max_output_tokens=2_048,
            tokenizer_type="estimated",
        )


@pytest.mark.asyncio
async def test_resume_structurer_allows_one_schema_repair_when_full_retry_fits() -> None:
    provider = SequenceProvider(
        [
            {"sections": [], "claims": []},
            {
                "sections": [
                    {
                        "source_sequence": 1,
                        "section_type": "experience",
                        "heading": "Experience",
                    }
                ],
                "claims": [
                    {
                        "source_sequence": 1,
                        "line": 1,
                        "claim_type": "project_achievement",
                        "content": "Built a FastAPI interview application",
                        "confidence": 0.9,
                    }
                ],
            },
        ]
    )

    result = await structure_resume_with_planner(
        provider,
        _source_sections(),
        context_window_tokens=32_768,
        max_output_tokens=2_048,
        tokenizer_type="estimated",
    )

    assert result.claims[0].content == "Built a FastAPI interview application"
    assert len(provider.requests) == 2
    assert provider.requests[1].messages[-2].role == "assistant"


@pytest.mark.asyncio
async def test_resume_structurer_blocks_schema_repair_that_exceeds_context_budget() -> None:
    provider = SequenceProvider(
        [
            {"sections": [], "claims": [], "padding": "x" * 10_000},
            {
                "sections": [
                    {
                        "source_sequence": 1,
                        "section_type": "experience",
                        "heading": "Experience",
                    }
                ],
                "claims": [],
            },
        ]
    )

    with pytest.raises(AgentInputBudgetError, match="resume_structurer input requires") as exc_info:
        await structure_resume_with_planner(
            provider,
            _source_sections(),
            context_window_tokens=4_096,
            max_output_tokens=512,
            tokenizer_type="estimated",
        )

    assert exc_info.value.code == "resume_structurer_context_budget_exceeded"
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_resume_worker_prefers_bound_planner_over_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        {
            "sections": [
                {"source_sequence": 1, "section_type": "experience", "heading": "Experience"}
            ],
            "claims": [
                {
                    "source_sequence": 1,
                    "line": 2,
                    "claim_type": "engineering_practice",
                    "content": "Used PostgreSQL for jobs",
                    "confidence": 0.8,
                }
            ],
        }
    )

    async def resolve_bound_planner(*_args: object) -> object:
        return SimpleNamespace(
            context_window_tokens=32_768,
            max_output_tokens=2_048,
            tokenizer_type="estimated",
        )

    monkeypatch.setattr(resume_jobs, "resolve_role_connection", resolve_bound_planner)
    monkeypatch.setattr(resume_jobs, "build_provider", lambda _connection: provider)
    resume = Resume(
        profile_id=uuid.uuid4(),
        filename="resume.txt",
        mime_type="text/plain",
        content_hash="a" * 64,
    )

    sections, claims, parser, fallback_reason = await resume_jobs._structure_resume(
        object(),
        resume,
        _source_sections(),
    )

    assert parser == "planner-v1"
    assert fallback_reason is None
    assert sections[0].section_type == "experience"
    assert claims[0].content == "Used PostgreSQL for jobs"


@pytest.mark.asyncio
async def test_resume_structurer_fails_closed_without_sending_an_oversized_resume() -> None:
    provider = FakeProvider({"sections": [], "claims": []})
    oversized = [
        ParsedSection(
            section_type="general",
            heading=None,
            content="x" * 20_000,
            sequence=1,
        )
    ]

    with pytest.raises(AgentInputBudgetError, match="resume_structurer input requires") as exc_info:
        await structure_resume_with_planner(
            provider,
            oversized,
            context_window_tokens=1_024,
            max_output_tokens=256,
            tokenizer_type="estimated",
        )

    assert exc_info.value.code == "resume_structurer_context_budget_exceeded"
    assert provider.requests == []


@pytest.mark.asyncio
async def test_resume_worker_falls_back_locally_when_the_bound_model_window_is_too_small(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider({"sections": [], "claims": []})

    async def resolve_small_window(*_args: object) -> object:
        return SimpleNamespace(
            context_window_tokens=1_024,
            max_output_tokens=256,
            tokenizer_type="estimated",
        )

    monkeypatch.setattr(resume_jobs, "resolve_role_connection", resolve_small_window)
    monkeypatch.setattr(resume_jobs, "build_provider", lambda _connection: provider)
    resume = Resume(
        profile_id=uuid.uuid4(),
        filename="resume.txt",
        mime_type="text/plain",
        content_hash="b" * 64,
    )
    oversized = [
        ParsedSection(
            section_type="general",
            heading=None,
            content="x" * 20_000,
            sequence=1,
        )
    ]

    sections, claims, parser, fallback_reason = await resume_jobs._structure_resume(
        object(),
        resume,
        oversized,
    )

    assert parser == "deterministic-v1"
    assert fallback_reason == "resume_structurer_context_budget_exceeded"
    assert sections == oversized
    assert claims
    assert provider.requests == []
