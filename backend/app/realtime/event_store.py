import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.interview import InterviewRealtimeEvent, InterviewSession
from app.realtime.events import ServerEvent


def to_server_event(row: InterviewRealtimeEvent) -> ServerEvent:
    return ServerEvent(
        event_id=row.event_id,
        session_id=row.session_id,
        type=row.event_type,
        sequence=row.sequence,
        timestamp=row.created_at,
        payload=row.payload,
    )


async def append_event(
    session: AsyncSession,
    interview: InterviewSession,
    *,
    event_type: str,
    payload: dict,
    client_event_id: str | None = None,
) -> ServerEvent:
    await session.refresh(interview, with_for_update=True)
    if client_event_id:
        existing = await session.scalar(
            select(InterviewRealtimeEvent).where(
                InterviewRealtimeEvent.session_id == interview.id,
                InterviewRealtimeEvent.client_event_id == client_event_id,
                InterviewRealtimeEvent.deleted_at.is_(None),
            )
        )
        if existing:
            await session.commit()
            return to_server_event(existing)
    interview.last_event_sequence += 1
    interview.touch()
    row = InterviewRealtimeEvent(
        session_id=interview.id,
        sequence=interview.last_event_sequence,
        event_type=event_type,
        payload=payload,
        client_event_id=client_event_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return to_server_event(row)


async def replay_events(
    session: AsyncSession,
    session_id: uuid.UUID,
    after_sequence: int,
) -> list[ServerEvent]:
    rows = list(
        (
            await session.scalars(
                select(InterviewRealtimeEvent)
                .where(
                    InterviewRealtimeEvent.session_id == session_id,
                    InterviewRealtimeEvent.sequence > after_sequence,
                    InterviewRealtimeEvent.deleted_at.is_(None),
                )
                .order_by(InterviewRealtimeEvent.sequence)
                .limit(500)
            )
        ).all()
    )
    return [to_server_event(row) for row in rows]


async def find_client_event(
    session: AsyncSession,
    session_id: uuid.UUID,
    client_event_id: str,
) -> ServerEvent | None:
    row = await session.scalar(
        select(InterviewRealtimeEvent).where(
            InterviewRealtimeEvent.session_id == session_id,
            InterviewRealtimeEvent.client_event_id == client_event_id,
        )
    )
    return to_server_event(row) if row else None
