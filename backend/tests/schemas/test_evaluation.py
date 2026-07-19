import uuid

from app.db.models.common import EvaluationAnchor
from app.schemas.evaluation import (
    DimensionEvaluationDraft,
    EvaluationDraft,
    QuestionEvaluationDraft,
)


def test_question_without_evidence_is_forced_to_evidence_insufficient() -> None:
    result = QuestionEvaluationDraft(
        plan_question_id=uuid.uuid4(),
        anchor=EvaluationAnchor.STRONG,
        summary="The answer looked strong, but no source was cited.",
        confidence=0.96,
    )

    assert result.anchor == EvaluationAnchor.EVIDENCE_INSUFFICIENT
    assert result.confidence == 0.25


def test_dimension_without_evidence_cannot_keep_a_strong_conclusion() -> None:
    result = DimensionEvaluationDraft(
        dimension="system_design",
        anchor=EvaluationAnchor.SOLID,
        action="Provide concrete evidence next time.",
        confidence=0.8,
    )

    assert result.anchor == EvaluationAnchor.EVIDENCE_INSUFFICIENT
    assert result.confidence == 0.25


def test_overall_anchor_is_evidence_insufficient_when_every_question_is() -> None:
    result = EvaluationDraft(
        overall_anchor=EvaluationAnchor.STRONG,
        overview="There is not enough traceable evidence.",
        questions=[
            QuestionEvaluationDraft(
                plan_question_id=uuid.uuid4(),
                anchor=EvaluationAnchor.STRONG,
                summary="Unsupported conclusion",
                confidence=0.9,
            )
        ],
        dimensions=[
            DimensionEvaluationDraft(
                dimension="communication",
                anchor=EvaluationAnchor.STRONG,
                action="Answer with a concrete example.",
                confidence=0.9,
            )
        ],
    )

    assert result.overall_anchor == EvaluationAnchor.EVIDENCE_INSUFFICIENT
