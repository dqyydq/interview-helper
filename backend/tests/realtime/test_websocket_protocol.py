import uuid

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import delete

from app.db.models.common import PlanStatus, SessionStatus
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.interview import (
    InterviewConfig,
    InterviewPlan,
    InterviewRealtimeEvent,
    InterviewSession,
)
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.realtime.connection_manager import ConnectionManager
from app.realtime.event_store import append_event, find_client_event, replay_events
from app.realtime.events import ClientEvent


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


async def _clear_realtime_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(InterviewRealtimeEvent))
        await session.execute(delete(InterviewSession))
        await session.execute(delete(InterviewPlan))
        await session.execute(delete(InterviewConfig))
        await session.execute(delete(RoundProfile))
        await session.execute(delete(CompanyStylePack))
        await session.execute(delete(Company))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_realtime_data():
    await _clear_realtime_data()
    yield
    await _clear_realtime_data()
    await engine.dispose()


def test_client_protocol_declares_resume_stt_commit_and_session_controls() -> None:
    event_types = [
        "session.resume",
        "session.restate",
        "user.transcript.partial",
        "user.answer.commit",
        "user.text.submit",
        "session.pause",
        "session.finish",
    ]
    for sequence, event_type in enumerate(event_types, start=1):
        event = ClientEvent(
            event_id=uuid.uuid4(),
            type=event_type,
            sequence=sequence,
            payload={},
        )
        assert event.type == event_type

    with pytest.raises(ValidationError):
        ClientEvent(
            event_id=uuid.uuid4(),
            type="unknown.event",
            sequence=1,
            payload={},
        )
    with pytest.raises(ValidationError):
        ClientEvent(
            event_id=uuid.uuid4(),
            type="session.pause",
            sequence=0,
            payload={},
        )


async def test_connection_manager_limits_parallel_sockets_per_session() -> None:
    manager = ConnectionManager()
    session_id = uuid.uuid4()
    sockets = [FakeWebSocket() for _ in range(4)]

    connected = [await manager.connect(session_id, item) for item in sockets[:3]]
    assert all(connected)
    assert await manager.connect(session_id, sockets[3]) is False
    assert sockets[3].closed == (1013, "too many session connections")

    await manager.disconnect(session_id, sockets[0])
    replacement = FakeWebSocket()
    assert await manager.connect(session_id, replacement) is True


async def test_persisted_events_are_idempotent_and_replay_in_sequence() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="实时协议测试")
        session.add(profile)
        await session.flush()
        company = Company(profile_id=profile.id, name="协议公司", slug="protocol-company")
        session.add(company)
        await session.flush()
        style = CompanyStylePack(company_id=company.id, name="协议风格")
        session.add(style)
        await session.flush()
        round_profile = RoundProfile(
            style_pack_id=style.id,
            round_key="round_1",
            name="一面",
        )
        session.add(round_profile)
        await session.flush()
        config = InterviewConfig(
            profile_id=profile.id,
            company_id=company.id,
            round_profile_id=round_profile.id,
            role_name="大模型应用开发",
        )
        session.add(config)
        await session.flush()
        plan = InterviewPlan(
            config_id=config.id,
            style_pack_id=style.id,
            status=PlanStatus.FROZEN,
            total_minutes=45,
        )
        session.add(plan)
        await session.flush()
        interview = InterviewSession(
            profile_id=profile.id,
            plan_id=plan.id,
            status=SessionStatus.INTERVIEWING,
        )
        session.add(interview)
        await session.commit()
        await session.refresh(interview)

        client_event_id = str(uuid.uuid4())
        first = await append_event(
            session,
            interview,
            event_type="input.ack",
            payload={"message": {"id": "message-1"}},
            client_event_id=client_event_id,
        )
        duplicate = await find_client_event(session, interview.id, client_event_id)
        duplicate_append = await append_event(
            session,
            interview,
            event_type="input.ack",
            payload={"message": {"id": "should-not-replace"}},
            client_event_id=client_event_id,
        )
        second = await append_event(
            session,
            interview,
            event_type="assistant.message",
            payload={"message": {"id": "message-2"}},
        )
        replayed = await replay_events(session, interview.id, first.sequence)

    assert duplicate and duplicate.event_id == first.event_id
    assert duplicate_append.event_id == first.event_id
    assert second.sequence == first.sequence + 1
    assert [item.event_id for item in replayed] == [second.event_id]
