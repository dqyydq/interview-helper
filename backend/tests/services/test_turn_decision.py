import uuid

from app.agents.turn_controller import TurnDecision
from app.db.models.common import SourceType
from app.db.models.interview import PlanQuestion
from app.services.interview_orchestrator import _guard_decision, _stable_decision


def _question(*, sequence: int, follow_up_budget: int) -> PlanQuestion:
    return PlanQuestion(
        plan_id=uuid.uuid4(),
        sequence=sequence,
        source_type=SourceType.GENERATED,
        source_ref={},
        prompt_snapshot=f"question {sequence}",
        capability_tags=[f"capability_{sequence}"],
        allocated_seconds=300,
        follow_up_budget=follow_up_budget,
        selection_reason="test",
    )


def test_early_finish_cannot_skip_remaining_planned_coverage() -> None:
    current = _question(sequence=1, follow_up_budget=2)
    next_question = _question(sequence=2, follow_up_budget=1)

    decision = _guard_decision(
        TurnDecision(action="finish", focus="impact", confidence=0.9),
        current=current,
        next_question=next_question,
        current_follow_up_index=0,
        remaining_seconds=420,
    )

    assert decision.action == "advance"
    assert decision.reason == "coverage_guard"


def test_follow_up_budget_and_time_force_stable_progression() -> None:
    current = _question(sequence=1, follow_up_budget=1)
    next_question = _question(sequence=2, follow_up_budget=1)

    budget_exhausted = _guard_decision(
        TurnDecision(action="follow_up", focus="evidence"),
        current=current,
        next_question=next_question,
        current_follow_up_index=1,
        remaining_seconds=300,
    )
    nearly_finished = _guard_decision(
        TurnDecision(action="advance", focus="clarification"),
        current=current,
        next_question=next_question,
        current_follow_up_index=0,
        remaining_seconds=20,
    )

    assert budget_exhausted.action == "advance"
    assert nearly_finished.action == "finish"
    assert _stable_decision(current=current, next_question=next_question).action == "follow_up"
