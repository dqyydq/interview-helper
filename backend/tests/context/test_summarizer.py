import uuid

import pytest

from app.api.errors import AppError
from app.context.summarizer import validate_summary_payload
from app.db.models.common import MessageRole
from app.db.models.interview import InterviewMessage, PlanQuestion
from app.schemas.context import ContextSummaryPayload, SummaryEvidence


def _question() -> PlanQuestion:
    return PlanQuestion(
        plan_id=uuid.uuid4(),
        sequence=1,
        prompt_snapshot="如何设计长对话压缩？",
        capability_tags=["context_engineering"],
        selection_reason="覆盖岗位核心能力",
    )


def _messages(question: PlanQuestion) -> tuple[InterviewMessage, InterviewMessage]:
    session_id = uuid.uuid4()
    assistant = InterviewMessage(
        session_id=session_id,
        plan_question_id=question.id,
        role=MessageRole.ASSISTANT,
        sequence=1,
        content=question.prompt_snapshot,
    )
    user = InterviewMessage(
        session_id=session_id,
        plan_question_id=question.id,
        role=MessageRole.USER,
        sequence=2,
        content="我会保留当前题链，对已关闭的题目生成带证据的摘要。",
    )
    return assistant, user


def test_summary_validation_accepts_traceable_user_claims() -> None:
    question = _question()
    assistant, user = _messages(question)
    evidence = SummaryEvidence(text="保留当前题链", evidence_message_ids=[user.id])
    payload = ContextSummaryPayload(
        question_id=question.id,
        capability_tags=question.capability_tags,
        asked_question=question.prompt_snapshot,
        user_core_answer=evidence,
        explicit_claims=[evidence],
        evidence_message_ids=[user.id],
    )

    evidence_ids = validate_summary_payload(
        payload,
        question=question,
        messages=[assistant, user],
        attachment_ids=set(),
    )

    assert evidence_ids == {user.id}


def test_summary_validation_rejects_claims_attributed_to_interviewer() -> None:
    question = _question()
    assistant, user = _messages(question)
    invalid_claim = SummaryEvidence(
        text="候选人会进行分层压缩",
        evidence_message_ids=[assistant.id],
    )
    payload = ContextSummaryPayload(
        question_id=question.id,
        capability_tags=question.capability_tags,
        asked_question=question.prompt_snapshot,
        user_core_answer=SummaryEvidence(
            text="保留当前题链",
            evidence_message_ids=[user.id],
        ),
        explicit_claims=[invalid_claim],
        evidence_message_ids=[assistant.id, user.id],
    )

    with pytest.raises(AppError) as error:
        validate_summary_payload(
            payload,
            question=question,
            messages=[assistant, user],
            attachment_ids=set(),
        )

    assert error.value.code == "summary_claim_source_invalid"


def test_summary_validation_rejects_evidence_outside_segment() -> None:
    question = _question()
    assistant, user = _messages(question)
    outside_id = uuid.uuid4()
    payload = ContextSummaryPayload(
        question_id=question.id,
        capability_tags=question.capability_tags,
        asked_question=question.prompt_snapshot,
        user_core_answer=SummaryEvidence(
            text="无法验证的回答",
            evidence_message_ids=[outside_id],
        ),
        evidence_message_ids=[outside_id],
    )

    with pytest.raises(AppError) as error:
        validate_summary_payload(
            payload,
            question=question,
            messages=[assistant, user],
            attachment_ids=set(),
        )

    assert error.value.code == "summary_evidence_out_of_range"
