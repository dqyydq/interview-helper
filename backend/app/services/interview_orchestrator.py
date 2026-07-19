import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.context.builder import build_interviewer_context
from app.context.segmentation import close_current_segment, get_open_segment
from app.db.models.common import MessageRole, ModelRole, SessionStatus
from app.db.models.context import InterviewContextState
from app.db.models.interview import InterviewMessage, InterviewSession, PlanQuestion
from app.providers.base import ChatProvider
from app.providers.factory import build_provider
from app.providers.types import ChatRequest
from app.services.model_connections import resolve_role_connection


@dataclass(slots=True)
class TurnPlan:
    plan_question: PlanQuestion
    static_prompt: str | None
    provider: ChatProvider | None
    request: ChatRequest | None
    context_snapshot_id: uuid.UUID | None = None


async def _next_message_sequence(session: AsyncSession, session_id) -> int:
    current = await session.scalar(
        select(func.coalesce(func.max(InterviewMessage.sequence), 0)).where(
            InterviewMessage.session_id == session_id
        )
    )
    return int(current or 0) + 1


async def save_user_answer(
    session: AsyncSession, interview: InterviewSession, content: str
) -> InterviewMessage:
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
    context = await session.scalar(
        select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
    )
    segment = (
        await get_open_segment(session, interview.id, context.current_plan_question_id)
        if context and context.current_plan_question_id
        else None
    )
    message = InterviewMessage(
        session_id=interview.id,
        plan_question_id=context.current_plan_question_id if context else None,
        segment_id=segment.id if segment else None,
        role=MessageRole.USER,
        sequence=await _next_message_sequence(session, interview.id),
        content=text,
        message_metadata={"kind": "answer"},
    )
    session.add(message)
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
        return TurnPlan(current, "本场问题已经完成。你可以补充最后一点，或结束面试。", None, None)
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
