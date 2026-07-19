import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.context.builder import build_interviewer_context
from app.context.segmentation import close_current_segment, get_open_segment
from app.db.models.common import AttachmentType, MessageRole, ModelRole, SessionStatus, utc_now
from app.db.models.context import InterviewContextState
from app.db.models.interview import (
    AnswerAttachment,
    InterviewMessage,
    InterviewPlan,
    InterviewSession,
    PlanQuestion,
)
from app.providers.base import ChatProvider
from app.providers.factory import build_provider
from app.providers.types import ChatRequest
from app.schemas.attachments import CodeAttachmentInput
from app.services.model_connections import resolve_role_connection


@dataclass(slots=True)
class TurnPlan:
    plan_question: PlanQuestion
    static_prompt: str | None
    provider: ChatProvider | None
    request: ChatRequest | None
    context_snapshot_id: uuid.UUID | None = None
    should_finish: bool = False


async def _next_message_sequence(session: AsyncSession, session_id) -> int:
    current = await session.scalar(
        select(func.coalesce(func.max(InterviewMessage.sequence), 0)).where(
            InterviewMessage.session_id == session_id
        )
    )
    return int(current or 0) + 1


async def save_user_answer(
    session: AsyncSession,
    interview: InterviewSession,
    content: str,
    *,
    client_event_id: str | None = None,
    attachments: list[CodeAttachmentInput] | None = None,
) -> InterviewMessage:
    await session.refresh(interview, with_for_update=True)
    if interview.status != SessionStatus.INTERVIEWING:
        raise AppError(
            code="session_not_interviewing",
            message="当前会话不能提交回答",
            status_code=409,
        )
    text = content.strip()
    if not text:
        raise AppError(code="answer_empty", message="回答不能为空", status_code=422)
    if len(text) > 50_000:
        raise AppError(
            code="answer_too_large",
            message="单次回答不能超过 50000 字符",
            status_code=413,
        )
    if client_event_id:
        existing = await session.scalar(
            select(InterviewMessage).where(
                InterviewMessage.session_id == interview.id,
                InterviewMessage.message_metadata["client_event_id"].as_string()
                == client_event_id,
                InterviewMessage.deleted_at.is_(None),
            )
        )
        if existing:
            await session.commit()
            return existing
    context = await session.scalar(
        select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
    )
    segment = (
        await get_open_segment(session, interview.id, context.current_plan_question_id)
        if context and context.current_plan_question_id
        else None
    )
    submitted_attachments = attachments or []
    message = InterviewMessage(
        session_id=interview.id,
        plan_question_id=context.current_plan_question_id if context else None,
        segment_id=segment.id if segment else None,
        role=MessageRole.USER,
        sequence=await _next_message_sequence(session, interview.id),
        content=text,
        message_metadata={
            "kind": "answer",
            "attachment_count": len(submitted_attachments),
            **({"client_event_id": client_event_id} if client_event_id else {}),
        },
    )
    session.add(message)
    await session.flush()
    for attachment in submitted_attachments:
        session.add(
            AnswerAttachment(
                message_id=message.id,
                attachment_type=AttachmentType.CODE,
                filename=attachment.filename,
                mime_type="text/plain",
                language=attachment.language,
                content=attachment.content,
                size_bytes=len(attachment.content.encode("utf-8")),
                attachment_metadata={"execution_allowed": False},
            )
        )
    await session.commit()
    await session.refresh(message)
    return message


async def prepare_turn(session: AsyncSession, interview: InterviewSession) -> TurnPlan:
    context = await session.scalar(
        select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
    )
    if not context or not context.current_plan_question_id:
        raise AppError(code="session_context_missing", message="会话上下文不存在", status_code=409)
    current = await session.get(PlanQuestion, context.current_plan_question_id)
    if not current:
        raise AppError(
            code="plan_question_not_found",
            message="当前计划题目不存在",
            status_code=409,
        )
    plan = await session.get(InterviewPlan, interview.plan_id)
    if (
        plan
        and interview.started_at
        and utc_now() >= interview.started_at + timedelta(minutes=plan.total_minutes)
    ):
        return TurnPlan(
            current,
            "本场时间已经结束。感谢你的回答，面试记录已完整保存。",
            None,
            None,
            should_finish=True,
        )
    if context.current_follow_up_index >= current.follow_up_budget:
        next_question = await session.scalar(
            select(PlanQuestion).where(
                PlanQuestion.plan_id == interview.plan_id,
                PlanQuestion.sequence == current.sequence + 1,
                PlanQuestion.deleted_at.is_(None),
            )
        )
        if next_question:
            await close_current_segment(
                session,
                interview,
                current.id,
                next_question_id=next_question.id,
            )
            context.completed_question_ids = [*context.completed_question_ids, str(current.id)]
            context.current_plan_question_id = next_question.id
            context.current_follow_up_index = 0
            context.touch()
            interview.current_question_sequence = next_question.sequence
            interview.touch()
            await session.commit()
            return TurnPlan(next_question, next_question.prompt_snapshot, None, None)
        await close_current_segment(session, interview, current.id)
        return TurnPlan(
            current,
            "本场问题已经完成。感谢你的回答，面试记录已完整保存。",
            None,
            None,
            should_finish=True,
        )
    connection = await resolve_role_connection(session, interview.profile_id, ModelRole.INTERVIEWER)
    built = await build_interviewer_context(
        session,
        interview=interview,
        current=current,
        context=context,
        connection=connection,
    )
    return TurnPlan(
        current,
        None,
        build_provider(connection),
        built.request,
        built.snapshot_id,
    )


async def save_restatement(
    session: AsyncSession,
    interview: InterviewSession,
) -> InterviewMessage:
    if interview.status not in {SessionStatus.INTERVIEWING, SessionStatus.PAUSED}:
        raise AppError(
            code="session_restatement_unavailable",
            message="当前会话不能重述问题",
            status_code=409,
        )
    context = await session.scalar(
        select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
    )
    if not context or not context.current_plan_question_id:
        raise AppError(code="session_context_missing", message="会话上下文不存在", status_code=409)
    latest = await session.scalar(
        select(InterviewMessage)
        .where(
            InterviewMessage.session_id == interview.id,
            InterviewMessage.plan_question_id == context.current_plan_question_id,
            InterviewMessage.role == MessageRole.ASSISTANT,
            InterviewMessage.deleted_at.is_(None),
        )
        .order_by(InterviewMessage.sequence.desc())
        .limit(1)
    )
    question = await session.get(PlanQuestion, context.current_plan_question_id)
    content = latest.content if latest else (question.prompt_snapshot if question else "")
    if not content:
        raise AppError(
            code="session_restatement_unavailable",
            message="当前没有可以重述的问题",
            status_code=409,
        )
    segment = await get_open_segment(session, interview.id, context.current_plan_question_id)
    message = InterviewMessage(
        session_id=interview.id,
        plan_question_id=context.current_plan_question_id,
        segment_id=segment.id if segment else None,
        role=MessageRole.ASSISTANT,
        sequence=await _next_message_sequence(session, interview.id),
        content=content,
        message_metadata={"kind": "restatement"},
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def save_assistant_message(
    session: AsyncSession, interview: InterviewSession, turn: TurnPlan, content: str
) -> InterviewMessage:
    segment = await get_open_segment(session, interview.id, turn.plan_question.id)
    message = InterviewMessage(
        session_id=interview.id,
        plan_question_id=turn.plan_question.id,
        segment_id=segment.id if segment else None,
        role=MessageRole.ASSISTANT,
        sequence=await _next_message_sequence(session, interview.id),
        content=content.strip(),
        message_metadata={
            "kind": "main_question" if turn.static_prompt else "follow_up",
            "plan_question_sequence": turn.plan_question.sequence,
        },
    )
    session.add(message)
    if not turn.static_prompt:
        context = await session.scalar(
            select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
        )
        if context:
            context.current_follow_up_index += 1
            context.touch()
    await session.commit()
    await session.refresh(message)
    return message
