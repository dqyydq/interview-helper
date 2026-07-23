"""Schema-constrained, source-grounded interview plan orchestration."""

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.agents.input_budget import structured_request_budget_validator
from app.core.security import UNTRUSTED_DATA_BOUNDARY
from app.db.models.common import MessageRole
from app.providers.base import ChatProvider, StructuredOutputRunner
from app.providers.types import ChatMessage, ChatRequest
from app.schemas.interview_plan import PlannerDraft, PlannerQuestionDraft
from app.services.question_retrieval import PlanCandidate
from app.services.role_matrix import RoleMatrix

SYSTEM_PROMPT = f"""You are the planning agent for a practice interview application.
Create an ordered, time-boxed interview plan from the supplied candidate pool only.

{UNTRUSTED_DATA_BOUNDARY}

Candidate keys are the only allowed source references. Never invent, rename, merge, or
omit a candidate key. Use every supplied candidate exactly once. Allocate exactly the
requested duration, retain each candidate's maximum follow-up budget, and report the
complete capability coverage represented by the chosen candidates. The company/round
style is guidance for ordering and timing, not a claim of an official hiring standard.
Return only JSON matching the provided schema.
"""


class PlannerSemanticError(ValueError):
    """A syntactically valid planner response violated its source-grounding contract."""


@dataclass(frozen=True, slots=True)
class PlannerResult:
    questions: list[PlannerQuestionDraft]
    rationale: str
    capability_coverage: dict[str, int]


def _candidate_payload(candidate: PlanCandidate) -> dict[str, Any]:
    return {
        "candidate_key": candidate.stable_key,
        "prompt": candidate.prompt,
        "source_type": candidate.source_type.value,
        "capability_tags": list(candidate.capability_tags),
        "max_follow_up_budget": candidate.follow_up_budget,
        "selection_context": candidate.selection_reason,
    }


def build_planner_payload(
    *,
    candidates: list[PlanCandidate],
    round_context: dict[str, Any],
    role_name: str,
    role_matrix: RoleMatrix,
    resume_summary: dict[str, Any] | None,
    duration_seconds: int,
) -> dict[str, Any]:
    """Build the bounded planning context without exposing the wider database."""

    return {
        "contract": {
            "duration_seconds": duration_seconds,
            "target_question_count": len(candidates),
            "candidate_keys": [candidate.stable_key for candidate in candidates],
        },
        "role": {
            "name": role_name,
            "matrix_key": role_matrix.role_key,
            "matrix_schema_version": role_matrix.schema_version,
            "capabilities": [
                {
                    "key": capability.key,
                    "name": capability.name,
                    "weight": capability.weight,
                }
                for capability in role_matrix.capabilities
            ],
        },
        "round": round_context,
        "resume_summary": resume_summary,
        "candidate_pool": [_candidate_payload(candidate) for candidate in candidates],
    }


def _candidate_map(candidates: list[PlanCandidate]) -> dict[str, PlanCandidate]:
    by_key = {candidate.stable_key: candidate for candidate in candidates}
    if len(by_key) != len(candidates):
        raise PlannerSemanticError("candidate pool contains duplicate stable keys")
    return by_key


def validate_planner_draft(
    draft: PlannerDraft,
    *,
    candidates: list[PlanCandidate],
    duration_seconds: int,
) -> dict[str, int]:
    """Validate source, ordering, time and coverage before persistence.

    The model cannot provide prompt text, source IDs or tags directly. Those fields are
    rehydrated from the deterministic candidate pool after this validation succeeds.
    """

    candidate_by_key = _candidate_map(candidates)
    expected_keys = set(candidate_by_key)
    actual_keys = [item.candidate_key for item in draft.questions]
    if len(actual_keys) != len(set(actual_keys)):
        raise PlannerSemanticError("planner output contains duplicate candidate keys")
    if set(actual_keys) != expected_keys:
        raise PlannerSemanticError("planner output does not exactly cover the candidate pool")

    sequences = [item.sequence for item in draft.questions]
    expected_sequences = list(range(1, len(candidates) + 1))
    if sorted(sequences) != expected_sequences:
        raise PlannerSemanticError("planner question sequences must be contiguous and unique")
    if sum(item.allocated_seconds for item in draft.questions) != duration_seconds:
        raise PlannerSemanticError("planner time allocation does not match the interview duration")

    for item in draft.questions:
        candidate = candidate_by_key[item.candidate_key]
        if item.follow_up_budget > candidate.follow_up_budget:
            raise PlannerSemanticError("planner follow-up budget exceeds the candidate limit")

    coverage = Counter(
        tag for candidate in candidates for tag in candidate.capability_tags if tag.strip()
    )
    if any(not tag.strip() for tag in draft.capability_coverage):
        raise PlannerSemanticError("planner capability coverage contains a blank tag")
    reported_coverage = [tag.strip() for tag in draft.capability_coverage]
    if len(reported_coverage) != len(set(reported_coverage)):
        raise PlannerSemanticError("planner capability coverage contains duplicates")
    if set(reported_coverage) != set(coverage):
        raise PlannerSemanticError("planner capability coverage is not source-grounded")
    return dict(sorted(coverage.items()))


async def run_planner(
    provider: ChatProvider,
    *,
    candidates: list[PlanCandidate],
    round_context: dict[str, Any],
    role_name: str,
    role_matrix: RoleMatrix,
    resume_summary: dict[str, Any] | None,
    duration_seconds: int,
    context_window_tokens: int,
    max_output_tokens: int,
    tokenizer_type: str,
    max_semantic_repairs: int = 1,
) -> PlannerResult:
    """Ask the configured Planner role to arrange a preselected candidate pool."""

    if not candidates:
        raise PlannerSemanticError("planner requires at least one candidate")
    if duration_seconds < len(candidates) * 30:
        raise PlannerSemanticError("duration is too small for the selected candidate pool")
    if max_semantic_repairs < 0 or max_semantic_repairs > 3:
        raise ValueError("max_semantic_repairs must be between 0 and 3")

    payload = build_planner_payload(
        candidates=candidates,
        round_context=round_context,
        role_name=role_name,
        role_matrix=role_matrix,
        resume_summary=resume_summary,
        duration_seconds=duration_seconds,
    )
    user_content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    reserved_output_tokens, request_validator = structured_request_budget_validator(
        agent_name="planner",
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        tokenizer_type=tokenizer_type,
    )
    request = ChatRequest(
        system=SYSTEM_PROMPT,
        messages=[
            ChatMessage(
                role=MessageRole.USER,
                content=user_content,
            )
        ],
        temperature=0,
        max_tokens=reserved_output_tokens,
    )
    runner = StructuredOutputRunner(
        provider,
        max_repairs=1,
        request_validator=request_validator,
    )
    last_error: PlannerSemanticError | None = None
    for attempt in range(max_semantic_repairs + 1):
        # Semantic repairs append a previous model response to this request.  Validate
        # again before delegating to StructuredOutputRunner, whose schema retries do the
        # same immediately before each provider call.
        request_validator(request)
        draft, response = await runner.run_with_response(request, PlannerDraft)
        try:
            coverage = validate_planner_draft(
                draft,
                candidates=candidates,
                duration_seconds=duration_seconds,
            )
            return PlannerResult(
                questions=sorted(draft.questions, key=lambda item: item.sequence),
                rationale=draft.rationale,
                capability_coverage=coverage,
            )
        except PlannerSemanticError as exc:
            last_error = exc
            if attempt >= max_semantic_repairs:
                break
            request.messages.extend(
                [
                    ChatMessage(role=MessageRole.ASSISTANT, content=response.content),
                    ChatMessage(
                        role=MessageRole.USER,
                        content=(
                            "The previous JSON violated the planning contract. Return only a "
                            f"corrected JSON response. Validation error: {exc}"
                        ),
                    ),
                ]
            )
    raise last_error or PlannerSemanticError("planner semantic validation failed")
