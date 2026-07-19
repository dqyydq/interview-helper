import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context.token_counter import UnifiedTokenCounter
from app.db.models.common import JobStatus, JobType, SegmentStatus, utc_now
from app.db.models.context import ConversationSegment
from app.db.models.interview import InterviewMessage, InterviewSession
from app.db.models.job import BackgroundJob
from app.providers.types import ChatMessage


async def get_open_segment(
    session: AsyncSession,
    session_id: uuid.UUID,
    plan_question_id: uuid.UUID,
) -> ConversationSegment | None:
    return await session.scalar(
        select(ConversationSegment).where(
            ConversationSegment.session_id == session_id,
            ConversationSegment.plan_question_id == plan_question_id,
            ConversationSegment.status == SegmentStatus.OPEN,
            ConversationSegment.deleted_at.is_(None),
        )
    )


async def _next_segment_sequence(session: AsyncSession, session_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(func.coalesce(func.max(ConversationSegment.sequence), 0)).where(
            ConversationSegment.session_id == session_id
        )
    )
    return int(value or 0) + 1


async def _next_message_sequence(session: AsyncSession, session_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(func.coalesce(func.max(InterviewMessage.sequence), 0)).where(
            InterviewMessage.session_id == session_id
        )
    )
    return int(value or 0) + 1


async def ensure_open_segment(
    session: AsyncSession,
    interview: InterviewSession,
    plan_question_id: uuid.UUID,
) -> ConversationSegment:
    existing = await get_open_segment(session, interview.id, plan_question_id)
    if existing:
        return existing
    segment = ConversationSegment(
        session_id=interview.id,
        plan_question_id=plan_question_id,
        sequence=await _next_segment_sequence(session, interview.id),
        start_message_sequence=await _next_message_sequence(session, interview.id),
    )
    session.add(segment)
    await session.flush()
    return segment


async def _enqueue_summary_job(
    session: AsyncSession,
    interview: InterviewSession,
    segment: ConversationSegment,
) -> BackgroundJob:
    idempotency_key = f"context-summary:{segment.id}:v1"
    existing = await session.scalar(
        select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key)
    )
    if existing:
        return existing
    job = BackgroundJob(
        profile_id=interview.profile_id,
        job_type=JobType.CONTEXT_SUMMARY,
        status=JobStatus.QUEUED,
        payload={"session_id": str(interview.id), "segment_id": str(segment.id)},
        idempotency_key=idempotency_key,
        max_attempts=3,
    )
    session.add(job)
    await session.flush()
    return job


async def close_current_segment(
    session: AsyncSession,
    interview: InterviewSession,
    plan_question_id: uuid.UUID,
    *,
    next_question_id: uuid.UUID | None = None,
) -> tuple[ConversationSegment | None, BackgroundJob | None]:
    segment = await get_open_segment(session, interview.id, plan_question_id)
    if not segment:
        return None, None
    messages = list(
        (
            await session.scalars(
                select(InterviewMessage)
                .where(
                    InterviewMessage.session_id == interview.id,
                    InterviewMessage.plan_question_id == plan_question_id,
                    InterviewMessage.deleted_at.is_(None),
                )
                .order_by(InterviewMessage.sequence)
            )
        ).all()
    )
    counter = UnifiedTokenCounter("estimated")
    total_tokens = 0
    for message in messages:
        if message.segment_id is None:
            message.segment_id = segment.id
            message.touch()
        if message.token_count is None:
            message.token_count = counter.count_messages(
                [ChatMessage(role=message.role, content=message.content)]
            ).tokens
        total_tokens += message.token_count
    segment.end_message_sequence = (
        messages[-1].sequence if messages else segment.start_message_sequence
    )
    segment.token_count = total_tokens
    segment.status = SegmentStatus.CLOSED
    segment.closed_at = utc_now()
    segment.touch()
    job = await _enqueue_summary_job(session, interview, segment)
    if next_question_id:
        await ensure_open_segment(session, interview, next_question_id)
    await session.commit()
    return segment, job
