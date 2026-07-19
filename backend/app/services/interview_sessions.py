import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.context.segmentation import close_current_segment
from app.db.models.common import (
    MessageRole,
    PlanStatus,
    SessionStatus,
    SummaryValidationStatus,
    utc_now,
)
from app.db.models.context import (
    ContextSnapshot,
    ContextSummary,
    ConversationSegment,
    InterviewContextState,
)
from app.db.models.interview import (
    InterviewConfig,
    InterviewMessage,
    InterviewPlan,
    InterviewSession,
    PlanQuestion,
)
from app.realtime.state_machine import ensure_transition
from app.schemas.context import (
    ContextDiagnosticsPublic,
    ContextSnapshotPublic,
    SegmentDiagnostic,
)
from app.schemas.interview_session import InterviewMessagePublic, InterviewSessionPublic
from app.services.interview_planning import plan_public


async def get_session(
    session: AsyncSession,
    profile_id: uuid.UUID,
    session_id: uuid.UUID,
) -> InterviewSession:
    interview = await session.scalar(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.profile_id == profile_id,
            InterviewSession.deleted_at.is_(None),
        )
    )
    if not interview:
        raise AppError(
            code="interview_session_not_found",
            message="面试会话不存在",
            status_code=404,
        )
    return interview


async def _messages(session: AsyncSession, session_id: uuid.UUID) -> list[InterviewMessage]:
    return list(
        (
            await session.scalars(
                select(InterviewMessage)
                .where(
                    InterviewMessage.session_id == session_id,
                    InterviewMessage.deleted_at.is_(None),
                )
                .order_by(InterviewMessage.sequence)
            )
        ).all()
    )


async def session_public(
    session: AsyncSession,
    interview: InterviewSession,
) -> InterviewSessionPublic:
    plan = await session.get(InterviewPlan, interview.plan_id)
    if not plan:
        raise AppError(code="interview_plan_not_found", message="面试计划不存在", status_code=404)
    messages = await _messages(session, interview.id)
    return InterviewSessionPublic(
        id=interview.id,
        created_at=interview.created_at,
        updated_at=interview.updated_at,
        version=interview.version,
        plan_id=interview.plan_id,
        status=interview.status,
        started_at=interview.started_at,
        ended_at=interview.ended_at,
        current_question_sequence=interview.current_question_sequence,
        last_event_sequence=interview.last_event_sequence,
        failure_code=interview.failure_code,
        plan=await plan_public(session, plan),
        messages=[InterviewMessagePublic.model_validate(message) for message in messages],
    )


async def context_diagnostics(
    session: AsyncSession,
    interview: InterviewSession,
) -> ContextDiagnosticsPublic:
    context = await session.scalar(
        select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
    )
    snapshots = list(
        (
            await session.scalars(
                select(ContextSnapshot)
                .where(
                    ContextSnapshot.session_id == interview.id,
                    ContextSnapshot.deleted_at.is_(None),
                )
                .order_by(ContextSnapshot.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    segments = list(
        (
            await session.scalars(
                select(ConversationSegment)
                .where(
                    ConversationSegment.session_id == interview.id,
                    ConversationSegment.deleted_at.is_(None),
                )
                .order_by(ConversationSegment.sequence)
            )
        ).all()
    )
    summary_rows = (
        await session.execute(
            select(ContextSummary.segment_id, ContextSummary.id).where(
                ContextSummary.segment_id.in_([item.id for item in segments]),
                ContextSummary.validation_status == SummaryValidationStatus.VALID,
                ContextSummary.deleted_at.is_(None),
            )
        )
    ).all()
    summaries_by_segment: dict[uuid.UUID, list[uuid.UUID]] = {}
    for segment_id, summary_id in summary_rows:
        summaries_by_segment.setdefault(segment_id, []).append(summary_id)
    current_state = (
        {
            "current_plan_question_id": str(context.current_plan_question_id)
            if context.current_plan_question_id
            else None,
            "current_follow_up_index": context.current_follow_up_index,
            "completed_question_ids": context.completed_question_ids,
            "unresolved_points": context.unresolved_points,
            "token_count": context.token_count,
        }
        if context
        else {}
    )
    return ContextDiagnosticsPublic(
        session_id=interview.id,
        current_state=current_state,
        snapshots=[ContextSnapshotPublic.model_validate(item) for item in snapshots],
        segments=[
            SegmentDiagnostic(
                id=item.id,
                plan_question_id=item.plan_question_id,
                sequence=item.sequence,
                status=item.status,
                start_message_sequence=item.start_message_sequence,
                end_message_sequence=item.end_message_sequence,
                token_count=item.token_count,
                valid_summary_ids=summaries_by_segment.get(item.id, []),
            )
            for item in segments
        ],
    )


async def create_session(
    session: AsyncSession,
    profile_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> InterviewSession:
    plan = await session.scalar(
        select(InterviewPlan)
        .join(InterviewConfig, InterviewConfig.id == InterviewPlan.config_id)
        .where(
            InterviewPlan.id == plan_id,
            InterviewConfig.profile_id == profile_id,
            InterviewPlan.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if not plan:
        raise AppError(code="interview_plan_not_found", message="面试计划不存在", status_code=404)
    if plan.status not in {PlanStatus.READY, PlanStatus.FROZEN}:
        raise AppError(code="interview_plan_not_ready", message="面试计划尚未就绪", status_code=409)
    questions = list(
        (
            await session.scalars(
                select(PlanQuestion)
                .where(PlanQuestion.plan_id == plan.id, PlanQuestion.deleted_at.is_(None))
                .order_by(PlanQuestion.sequence)
            )
        ).all()
    )
    if not questions:
        raise AppError(code="interview_plan_empty", message="面试计划没有题目", status_code=409)
    if plan.status == PlanStatus.READY:
        plan.status = PlanStatus.FROZEN
        plan.frozen_at = utc_now()
        plan.touch()
    interview = InterviewSession(
        profile_id=profile_id,
        plan_id=plan.id,
        status=SessionStatus.READY,
    )
    session.add(interview)
    await session.flush()
    session.add(
        InterviewContextState(
            session_id=interview.id,
            current_plan_question_id=questions[0].id,
            state_payload={"phase": "ready", "plan_version": plan.version},
        )
    )
    await session.commit()
    await session.refresh(interview)
    return interview


async def start_session(session: AsyncSession, interview: InterviewSession) -> InterviewSession:
    if interview.status == SessionStatus.INTERVIEWING:
        return interview
    ensure_transition(SessionStatus(interview.status), SessionStatus.INTERVIEWING)
    first_question = await session.scalar(
        select(PlanQuestion).where(
            PlanQuestion.plan_id == interview.plan_id,
            PlanQuestion.sequence == 1,
            PlanQuestion.deleted_at.is_(None),
        )
    )
    if not first_question:
        raise AppError(code="interview_plan_empty", message="面试计划没有题目", status_code=409)
    existing = await session.scalar(
        select(InterviewMessage.id).where(InterviewMessage.session_id == interview.id).limit(1)
    )
    if not existing:
        segment = ConversationSegment(
            session_id=interview.id,
            plan_question_id=first_question.id,
            sequence=1,
            start_message_sequence=1,
        )
        session.add(segment)
        await session.flush()
        session.add(
            InterviewMessage(
                session_id=interview.id,
                plan_question_id=first_question.id,
                segment_id=segment.id,
                role=MessageRole.ASSISTANT,
                sequence=1,
                content=first_question.prompt_snapshot,
                message_metadata={"kind": "main_question", "plan_question_sequence": 1},
            )
        )
    interview.status = SessionStatus.INTERVIEWING
    interview.started_at = interview.started_at or utc_now()
    interview.current_question_sequence = 1
    interview.touch()
    await session.commit()
    await session.refresh(interview)
    return interview


async def pause_session(session: AsyncSession, interview: InterviewSession) -> InterviewSession:
    if interview.status == SessionStatus.PAUSED:
        return interview
    ensure_transition(SessionStatus(interview.status), SessionStatus.PAUSED)
    interview.status = SessionStatus.PAUSED
    interview.touch()
    await session.commit()
    return interview


async def resume_session(session: AsyncSession, interview: InterviewSession) -> InterviewSession:
    if interview.status == SessionStatus.INTERVIEWING:
        return interview
    ensure_transition(SessionStatus(interview.status), SessionStatus.INTERVIEWING)
    interview.status = SessionStatus.INTERVIEWING
    interview.touch()
    await session.commit()
    return interview


async def finish_session(session: AsyncSession, interview: InterviewSession) -> InterviewSession:
    if interview.status == SessionStatus.COMPLETED:
        return interview
    context = await session.scalar(
        select(InterviewContextState).where(InterviewContextState.session_id == interview.id)
    )
    if context and context.current_plan_question_id:
        await close_current_segment(
            session,
            interview,
            context.current_plan_question_id,
        )
    current = SessionStatus(interview.status)
    if current != SessionStatus.COMPLETING:
        ensure_transition(current, SessionStatus.COMPLETING)
        interview.status = SessionStatus.COMPLETING
        interview.touch()
    ensure_transition(SessionStatus.COMPLETING, SessionStatus.COMPLETED)
    interview.status = SessionStatus.COMPLETED
    interview.ended_at = interview.ended_at or utc_now()
    interview.touch()
    await session.commit()
    return interview
