import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.context.builder import build_summary_context
from app.context.snapshot import finalize_context_snapshot
from app.context.token_counter import UnifiedTokenCounter
from app.db.models.common import MessageRole, ModelRole, SummaryValidationStatus
from app.db.models.context import ContextSummary, ConversationSegment
from app.db.models.interview import (
    AnswerAttachment,
    InterviewMessage,
    InterviewSession,
    PlanQuestion,
)
from app.providers.base import StructuredOutputRunner
from app.providers.factory import build_provider
from app.schemas.context import ContextSummaryPayload, SummaryEvidence
from app.services.model_connections import resolve_role_connection


def _statements(payload: ContextSummaryPayload) -> list[SummaryEvidence]:
    values = [
        *payload.explicit_claims,
        *payload.decisions_and_tradeoffs,
        *payload.caveats_and_failures,
        *payload.unresolved_points,
    ]
    if payload.user_core_answer:
        values.append(payload.user_core_answer)
    return values


def validate_summary_payload(
    payload: ContextSummaryPayload,
    *,
    question: PlanQuestion,
    messages: list[InterviewMessage],
    attachment_ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    if payload.question_id != question.id:
        raise AppError(
            code="summary_question_mismatch",
            message="摘要引用了错误的计划题目",
            status_code=422,
        )
    if (
        payload.asked_question.strip() != question.prompt_snapshot.strip()
        or payload.capability_tags != question.capability_tags
    ):
        raise AppError(
            code="summary_question_content_mismatch",
            message="摘要改变了题目原文或能力标签",
            status_code=422,
        )
    message_ids = {item.id for item in messages}
    user_message_ids = {item.id for item in messages if item.role == MessageRole.USER}
    if user_message_ids and payload.user_core_answer is None:
        raise AppError(
            code="summary_core_answer_missing",
            message="摘要遗漏了候选人的核心回答",
            status_code=422,
        )
    evidence_ids = {
        evidence_id
        for statement in _statements(payload)
        for evidence_id in statement.evidence_message_ids
    }
    declared_ids = set(payload.evidence_message_ids)
    if not evidence_ids.issubset(message_ids) or not declared_ids.issubset(message_ids):
        raise AppError(
            code="summary_evidence_out_of_range",
            message="摘要证据不在当前分段范围内",
            status_code=422,
        )
    if evidence_ids != declared_ids:
        raise AppError(
            code="summary_evidence_incomplete",
            message="摘要的证据清单与结构化字段不一致",
            status_code=422,
        )
    explicit_claim_ids = {
        evidence_id
        for statement in payload.explicit_claims
        for evidence_id in statement.evidence_message_ids
    }
    if not explicit_claim_ids.issubset(user_message_ids):
        raise AppError(
            code="summary_claim_source_invalid",
            message="显式事实只能引用候选人的原始回答",
            status_code=422,
        )
    if not set(payload.attachment_refs).issubset(attachment_ids):
        raise AppError(
            code="summary_attachment_out_of_range",
            message="摘要附件引用不在当前分段范围内",
            status_code=422,
        )
    return evidence_ids


async def summarize_segment(
    session: AsyncSession,
    *,
    segment_id: uuid.UUID,
) -> ContextSummary:
    segment = await session.get(ConversationSegment, segment_id)
    if not segment or segment.deleted_at is not None:
        raise AppError(code="segment_not_found", message="会话分段不存在", status_code=404)
    if segment.end_message_sequence is None:
        raise AppError(code="segment_not_closed", message="当前分段尚未关闭", status_code=409)
    existing = await session.scalar(
        select(ContextSummary)
        .where(
            ContextSummary.segment_id == segment.id,
            ContextSummary.validation_status == SummaryValidationStatus.VALID,
            ContextSummary.deleted_at.is_(None),
        )
        .order_by(ContextSummary.summary_version.desc())
        .limit(1)
    )
    if existing:
        return existing
    interview = await session.get(InterviewSession, segment.session_id)
    question = (
        await session.get(PlanQuestion, segment.plan_question_id)
        if segment.plan_question_id
        else None
    )
    if not interview or not question:
        raise AppError(
            code="segment_source_missing",
            message="会话分段缺少面试或题目来源",
            status_code=409,
        )
    messages = list(
        (
            await session.scalars(
                select(InterviewMessage)
                .where(
                    InterviewMessage.session_id == interview.id,
                    InterviewMessage.sequence >= segment.start_message_sequence,
                    InterviewMessage.sequence <= segment.end_message_sequence,
                    InterviewMessage.deleted_at.is_(None),
                )
                .order_by(InterviewMessage.sequence)
            )
        ).all()
    )
    if not messages:
        raise AppError(code="segment_empty", message="会话分段没有可摘要消息", status_code=409)
    message_ids = [item.id for item in messages]
    attachments = list(
        (
            await session.scalars(
                select(AnswerAttachment).where(
                    AnswerAttachment.message_id.in_(message_ids),
                    AnswerAttachment.deleted_at.is_(None),
                )
            )
        ).all()
    )
    attachment_payload = [
        {
            "id": str(item.id),
            "message_id": str(item.message_id),
            "type": str(item.attachment_type),
            "filename": item.filename,
            "language": item.language,
            "content": item.content,
        }
        for item in attachments
    ]
    connection = await resolve_role_connection(
        session, interview.profile_id, ModelRole.CONTEXT_SUMMARIZER
    )
    built = await build_summary_context(
        session,
        interview=interview,
        segment=segment,
        question=question,
        messages=messages,
        attachments=attachment_payload,
        connection=connection,
    )
    provider = build_provider(connection)
    try:
        payload, response = await StructuredOutputRunner(provider, max_repairs=1).run_with_response(
            built.request,
            ContextSummaryPayload,
        )
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()
    await finalize_context_snapshot(
        session,
        built.snapshot_id,
        response.usage,
        provider_request_id=response.provider_request_id,
    )
    evidence_ids = validate_summary_payload(
        payload,
        question=question,
        messages=messages,
        attachment_ids={item.id for item in attachments},
    )
    latest_version = await session.scalar(
        select(func.coalesce(func.max(ContextSummary.summary_version), 0)).where(
            ContextSummary.segment_id == segment.id
        )
    )
    content = payload.model_dump(mode="json")
    summary = ContextSummary(
        segment_id=segment.id,
        summary_version=int(latest_version or 0) + 1,
        content=content,
        evidence_message_ids=[str(item) for item in sorted(evidence_ids, key=str)],
        schema_version="1",
        summarizer_model_connection_id=connection.id,
        token_count=UnifiedTokenCounter(connection.tokenizer_type)
        .count_text(json.dumps(content, ensure_ascii=False))
        .tokens,
        validation_status=SummaryValidationStatus.VALID,
    )
    session.add(summary)
    await session.commit()
    await session.refresh(summary)
    return summary
