import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.turn_controller import TurnDecision, run_turn_controller
from app.api.errors import AppError
from app.context.builder import build_interviewer_context
from app.context.segmentation import close_current_segment, get_open_segment
from app.core.config import settings
from app.db.models.common import AttachmentType, MessageRole, ModelRole, SessionStatus, utc_now
from app.db.models.context import InterviewContextState
from app.db.models.interview import (
    AnswerAttachment,
    InterviewMessage,
    InterviewPlan,
    InterviewSession,
    PlanQuestion,
)
from app.providers.base import ChatProvider, ProviderError, StructuredOutputError
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
    decision_action: str = "follow_up"
    decision_source: str = "fallback"


@dataclass(frozen=True, slots=True)
class TurnDecisionResult:
    """A guard-railed decision plus whether a model policy call was usable."""

    decision: TurnDecision
    source: str
    fallback_reason: str | None = None


FINISH_MESSAGE = "本场面试已完成。感谢你的回答，面试记录已经完整保存。"
TIME_UP_MESSAGE = "本场时间已经结束。感谢你的回答，面试记录已经完整保存。"
MIN_FOLLOW_UP_SECONDS = 60
MIN_NEXT_QUESTION_SECONDS = 45


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
                InterviewMessage.message_metadata["client_event_id"].as_string() == client_event_id,
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
    # The answer has crossed the durability boundary. If the model call drops
    # or the browser reconnects, `turn.retry` reads this pointer rather than
    # accepting the answer body again, so a retry cannot duplicate the answer.
    if context:
        state_payload = dict(context.state_payload or {})
        state_payload["pending_answer_message_id"] = str(message.id)
        state_payload["pending_answer_sequence"] = message.sequence
        state_payload.pop("turn_decision", None)
        context.state_payload = state_payload
        context.touch()
    await session.commit()
    await session.refresh(message)
    return message


async def _next_plan_question(
    session: AsyncSession,
    interview: InterviewSession,
    current: PlanQuestion,
) -> PlanQuestion | None:
    return await session.scalar(
        select(PlanQuestion).where(
            PlanQuestion.plan_id == interview.plan_id,
            PlanQuestion.sequence == current.sequence + 1,
            PlanQuestion.deleted_at.is_(None),
        )
    )


def _remaining_seconds(interview: InterviewSession, plan: InterviewPlan | None) -> int:
    if not plan:
        return 0
    if not interview.started_at:
        return plan.total_minutes * 60
    deadline = interview.started_at + timedelta(minutes=plan.total_minutes)
    return max(0, int((deadline - utc_now()).total_seconds()))


def _stable_decision(
    *,
    current: PlanQuestion,
    next_question: PlanQuestion | None,
) -> TurnDecision:
    """Keep the historical deterministic progression as the fail-safe policy."""

    if current.follow_up_budget > 0:
        return TurnDecision(
            action="follow_up",
            focus="clarification",
            confidence=0.0,
            reason="stable_follow_up_budget",
        )
    if next_question:
        return TurnDecision(
            action="advance",
            focus="clarification",
            confidence=0.0,
            reason="stable_next_planned_question",
        )
    return TurnDecision(
        action="finish",
        focus="clarification",
        confidence=0.0,
        reason="stable_plan_complete",
    )


def _guard_decision(
    candidate: TurnDecision,
    *,
    current: PlanQuestion,
    next_question: PlanQuestion | None,
    current_follow_up_index: int,
    remaining_seconds: int,
) -> TurnDecision:
    """Apply non-negotiable time, follow-up and coverage constraints."""

    if remaining_seconds <= 0:
        return candidate.model_copy(update={"action": "finish", "reason": "time_exhausted"})
    if candidate.action == "follow_up":
        if (
            current_follow_up_index < current.follow_up_budget
            and remaining_seconds >= MIN_FOLLOW_UP_SECONDS
        ):
            return candidate
        action = (
            "advance"
            if next_question and remaining_seconds >= MIN_NEXT_QUESTION_SECONDS
            else "finish"
        )
        return candidate.model_copy(update={"action": action, "reason": "follow_up_guard"})
    if candidate.action == "advance":
        if next_question and remaining_seconds >= MIN_NEXT_QUESTION_SECONDS:
            return candidate
        return candidate.model_copy(update={"action": "finish", "reason": "advance_guard"})
    # Coverage wins over an early model-driven finish while there is enough
    # practical time to ask the next fixed main question.
    if next_question and remaining_seconds >= max(
        MIN_NEXT_QUESTION_SECONDS,
        next_question.allocated_seconds // 3,
    ):
        return candidate.model_copy(update={"action": "advance", "reason": "coverage_guard"})
    return candidate


async def decide_turn(
    session: AsyncSession,
    interview: InterviewSession,
    answer: InterviewMessage,
) -> TurnDecisionResult:
    """Run a small policy call after a durable answer, with a stable fallback."""

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
    next_question = await _next_plan_question(session, interview, current)
    remaining_seconds = _remaining_seconds(interview, plan)
    fallback = _guard_decision(
        _stable_decision(current=current, next_question=next_question),
        current=current,
        next_question=next_question,
        current_follow_up_index=context.current_follow_up_index,
        remaining_seconds=remaining_seconds,
    )
    selected = fallback
    source = "fallback"
    fallback_reason: str | None = None
    provider = None
    try:
        connection = await resolve_role_connection(
            session,
            interview.profile_id,
            ModelRole.INTERVIEWER,
        )
        provider = build_provider(connection)
        async with asyncio.timeout(settings.interview_turn_decision_timeout_seconds):
            proposed = await run_turn_controller(
                provider,
                current_question=current.prompt_snapshot,
                current_capabilities=list(current.capability_tags),
                answer=answer.content,
                follow_ups_remaining=max(
                    0,
                    current.follow_up_budget - context.current_follow_up_index,
                ),
                next_question=next_question.prompt_snapshot if next_question else None,
                next_capabilities=list(next_question.capability_tags) if next_question else [],
                remaining_seconds=remaining_seconds,
                remaining_main_questions=1 if next_question else 0,
                unresolved_points=list(context.unresolved_points),
                max_tokens=min(
                    settings.interview_turn_decision_output_tokens,
                    connection.max_output_tokens,
                ),
            )
        selected = _guard_decision(
            proposed,
            current=current,
            next_question=next_question,
            current_follow_up_index=context.current_follow_up_index,
            remaining_seconds=remaining_seconds,
        )
        source = "controller"
    except TimeoutError:
        fallback_reason = "controller_timeout"
    except (AppError, ProviderError, StructuredOutputError, ValueError) as exc:
        fallback_reason = getattr(exc, "code", type(exc).__name__)
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()

    state_payload = dict(context.state_payload or {})
    state_payload["turn_decision"] = {
        "action": selected.action,
        "focus": selected.focus,
        "source": source,
        "fallback_reason": fallback_reason,
    }
    context.state_payload = state_payload
    context.touch()
    await session.commit()
    return TurnDecisionResult(
        decision=selected,
        source=source,
        fallback_reason=fallback_reason,
    )


async def pending_answer_for_retry(
    session: AsyncSession,
    interview: InterviewSession,
) -> InterviewMessage:
    context = await session.scalar(
        select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
    )
    pending_id = (context.state_payload or {}).get("pending_answer_message_id") if context else None
    if not pending_id:
        raise AppError(
            code="turn_retry_unavailable",
            message="没有可重试的已保存回答",
            status_code=409,
        )
    try:
        message_id = uuid.UUID(str(pending_id))
    except (TypeError, ValueError) as exc:
        raise AppError(
            code="turn_retry_unavailable",
            message="已保存回答状态无效",
            status_code=409,
        ) from exc
    message = await session.scalar(
        select(InterviewMessage).where(
            InterviewMessage.id == message_id,
            InterviewMessage.session_id == interview.id,
            InterviewMessage.role == MessageRole.USER,
            InterviewMessage.deleted_at.is_(None),
        )
    )
    if not message:
        raise AppError(
            code="turn_retry_unavailable",
            message="已保存回答不可用",
            status_code=409,
        )
    return message


async def _advance_to_next_question(
    session: AsyncSession,
    interview: InterviewSession,
    context: InterviewContextState,
    current: PlanQuestion,
    next_question: PlanQuestion,
    *,
    decision_source: str,
) -> TurnPlan:
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
    return TurnPlan(
        next_question,
        next_question.prompt_snapshot,
        None,
        None,
        decision_action="advance",
        decision_source=decision_source,
    )


async def prepare_turn(
    session: AsyncSession,
    interview: InterviewSession,
    *,
    decision: TurnDecision | None = None,
    decision_source: str = "fallback",
) -> TurnPlan:
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
    remaining_seconds = _remaining_seconds(interview, plan)
    next_question = await _next_plan_question(session, interview, current)
    selected = _guard_decision(
        decision or _stable_decision(current=current, next_question=next_question),
        current=current,
        next_question=next_question,
        current_follow_up_index=context.current_follow_up_index,
        remaining_seconds=remaining_seconds,
    )
    if selected.action == "advance" and next_question:
        return await _advance_to_next_question(
            session,
            interview,
            context,
            current,
            next_question,
            decision_source=decision_source,
        )
    if selected.action == "finish":
        return TurnPlan(
            current,
            TIME_UP_MESSAGE if remaining_seconds <= 0 else FINISH_MESSAGE,
            None,
            None,
            should_finish=True,
            decision_action="finish",
            decision_source=decision_source,
        )
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
        decision_action="follow_up",
        decision_source=decision_source,
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
            "kind": "closing"
            if turn.should_finish
            else ("main_question" if turn.static_prompt else "follow_up"),
            "plan_question_sequence": turn.plan_question.sequence,
            "turn_decision": turn.decision_action,
            "turn_decision_source": turn.decision_source,
        },
    )
    session.add(message)
    context = await session.scalar(
        select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
    )
    if not turn.static_prompt:
        if context:
            context.current_follow_up_index += 1
            context.touch()
    if context:
        state_payload = dict(context.state_payload or {})
        state_payload.pop("pending_answer_message_id", None)
        state_payload.pop("pending_answer_sequence", None)
        state_payload.pop("turn_decision", None)
        context.state_payload = state_payload
        context.touch()
    await session.commit()
    await session.refresh(message)
    return message
