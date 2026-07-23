import json
from collections.abc import AsyncIterator

import pytest

from app.agents.input_budget import AgentInputBudgetError
from app.agents.planner import (
    SYSTEM_PROMPT,
    PlannerSemanticError,
    run_planner,
    validate_planner_draft,
)
from app.db.models.common import SourceType
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
)
from app.schemas.interview_plan import PlannerDraft
from app.services.question_retrieval import PlanCandidate
from app.services.role_matrix import load_role_matrix


class FakeProvider(ChatProvider):
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request.model_copy(deep=True))
        return ChatResponse(content=json.dumps(self.responses.pop(0), ensure_ascii=False))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)


def _candidates() -> list[PlanCandidate]:
    return [
        PlanCandidate(
            stable_key="question:11111111-1111-4111-8111-111111111111",
            prompt="Describe a retrieval evaluation plan.",
            source_type=SourceType.MANUAL,
            source_ref={"question_id": "11111111-1111-4111-8111-111111111111"},
            capability_tags=("rag_and_retrieval",),
            follow_up_budget=2,
            selection_reason="Manual question bank candidate.",
        ),
        PlanCandidate(
            stable_key="template:agent_reliability",
            prompt="Design a reliable agent tool-calling workflow.",
            source_type=SourceType.GENERATED,
            source_ref={"template_key": "agent_reliability"},
            capability_tags=("agent_engineering", "system_design"),
            follow_up_budget=3,
            selection_reason="Role-matrix scenario candidate.",
        ),
    ]


def _draft(*, first_key: str | None = None, seconds: tuple[int, int] = (300, 300)) -> dict:
    candidates = _candidates()
    return {
        "questions": [
            {
                "candidate_key": first_key or candidates[1].stable_key,
                "sequence": 1,
                "allocated_seconds": seconds[0],
                "follow_up_budget": 2,
                "selection_reason": "Start with the system-level scenario.",
            },
            {
                "candidate_key": candidates[0].stable_key,
                "sequence": 2,
                "allocated_seconds": seconds[1],
                "follow_up_budget": 1,
                "selection_reason": "Then verify retrieval measurement depth.",
            },
        ],
        "rationale": "Cover agent reliability first, then validate retrieval rigor.",
        "capability_coverage": ["rag_and_retrieval", "agent_engineering", "system_design"],
    }


@pytest.mark.asyncio
async def test_planner_receives_only_bounded_inputs_and_orders_valid_candidates() -> None:
    provider = FakeProvider([_draft()])
    result = await run_planner(
        provider,
        candidates=_candidates(),
        round_context={"round": {"name": "Technical round", "pressure_level": 3}},
        role_name="llm_application_engineer",
        role_matrix=load_role_matrix("llm_application_engineer"),
        resume_summary={"resume_id": "resume-1", "claims": [{"content": "Built RAG systems."}]},
        duration_seconds=600,
        context_window_tokens=32_768,
        max_output_tokens=2_048,
        tokenizer_type="estimated",
    )

    assert [item.sequence for item in result.questions] == [1, 2]
    assert [item.candidate_key for item in result.questions] == [
        "template:agent_reliability",
        "question:11111111-1111-4111-8111-111111111111",
    ]
    assert result.capability_coverage == {
        "agent_engineering": 1,
        "rag_and_retrieval": 1,
        "system_design": 1,
    }
    request = provider.requests[0]
    payload = json.loads(request.messages[0].content)
    assert set(payload) == {"contract", "role", "round", "resume_summary", "candidate_pool"}
    assert all("source_ref" not in candidate for candidate in payload["candidate_pool"])
    assert request.response_schema is not None
    assert request.max_tokens == 2_048
    assert "Candidate keys are the only allowed source references" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("draft", "error"),
    [
        (_draft(first_key="question:unknown"), "candidate pool"),
        (
            {
                **_draft(),
                "questions": [
                    {**_draft()["questions"][0], "candidate_key": _candidates()[0].stable_key},
                    {**_draft()["questions"][1], "candidate_key": _candidates()[0].stable_key},
                ],
            },
            "duplicate candidate",
        ),
        (_draft(seconds=(299, 300)), "time allocation"),
        ({**_draft(), "capability_coverage": ["agent_engineering"]}, "capability coverage"),
    ],
)
def test_planner_rejects_invalid_source_time_deduplication_and_coverage(
    draft: dict,
    error: str,
) -> None:
    with pytest.raises(PlannerSemanticError, match=error):
        validate_planner_draft(
            PlannerDraft.model_validate(draft),
            candidates=_candidates(),
            duration_seconds=600,
        )


@pytest.mark.asyncio
async def test_planner_repairs_one_semantically_invalid_response() -> None:
    invalid = _draft(seconds=(299, 300))
    provider = FakeProvider([invalid, _draft()])

    result = await run_planner(
        provider,
        candidates=_candidates(),
        round_context={"round": {"name": "Technical round"}},
        role_name="llm_application_engineer",
        role_matrix=load_role_matrix("llm_application_engineer"),
        resume_summary=None,
        duration_seconds=600,
        context_window_tokens=32_768,
        max_output_tokens=2_048,
        tokenizer_type="estimated",
    )

    assert result.rationale.startswith("Cover agent reliability")
    assert len(provider.requests) == 2
    assert "Validation error" in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_planner_blocks_semantic_repair_that_exceeds_context_budget() -> None:
    provider = FakeProvider(
        [
            {
                **_draft(first_key="question:unknown"),
                "rationale": "x" * 10_000,
            },
            _draft(),
        ]
    )

    with pytest.raises(AgentInputBudgetError, match="planner input requires") as exc_info:
        await run_planner(
            provider,
            candidates=_candidates(),
            round_context={"round": {"name": "Technical round"}},
            role_name="llm_application_engineer",
            role_matrix=load_role_matrix("llm_application_engineer"),
            resume_summary=None,
            duration_seconds=600,
            context_window_tokens=4_096,
            max_output_tokens=512,
            tokenizer_type="estimated",
        )

    assert exc_info.value.code == "planner_context_budget_exceeded"
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_planner_fails_closed_before_sending_an_oversized_candidate_pool() -> None:
    provider = FakeProvider([_draft()])
    oversized_candidates = [
        PlanCandidate(
            stable_key="question:oversized",
            prompt="x" * 20_000,
            source_type=SourceType.MANUAL,
            source_ref={"question_id": "11111111-1111-4111-8111-111111111111"},
            capability_tags=("system_design",),
            follow_up_budget=1,
            selection_reason="Oversized source fixture.",
        )
    ]

    with pytest.raises(AgentInputBudgetError, match="planner input requires") as exc_info:
        await run_planner(
            provider,
            candidates=oversized_candidates,
            round_context={"round": {"name": "Technical round"}},
            role_name="llm_application_engineer",
            role_matrix=load_role_matrix("llm_application_engineer"),
            resume_summary=None,
            duration_seconds=600,
            context_window_tokens=1_024,
            max_output_tokens=256,
            tokenizer_type="estimated",
        )

    assert exc_info.value.code == "planner_context_budget_exceeded"
    assert provider.requests == []
