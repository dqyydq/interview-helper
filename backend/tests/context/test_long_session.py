from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.context.builder import build_interviewer_context
from app.context.summarizer import summarize_segment
from app.db.models.common import (
    JobType,
    MemoryStatus,
    MemoryType,
    MessageRole,
    PlanStatus,
    ProviderType,
    SegmentStatus,
    SessionStatus,
    SummaryValidationStatus,
    utc_now,
)
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.context import (
    ContextSnapshot,
    ContextSummary,
    ConversationSegment,
    InterviewContextState,
)
from app.db.models.interview import (
    AnswerAttachment,
    InterviewConfig,
    InterviewMessage,
    InterviewPlan,
    InterviewRealtimeEvent,
    InterviewSession,
    PlanQuestion,
)
from app.db.models.job import BackgroundJob
from app.db.models.memory import MemoryConflict, MemoryItem, MemorySource, MemoryUsage
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.main import app
from app.providers.base import ChatProvider, StructuredOutputError
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
    Usage,
)
from app.services.interview_orchestrator import (
    prepare_turn,
    save_restatement,
    save_user_answer,
)
from app.services.interview_sessions import finish_session
from app.services.memories import delete_memory


@dataclass(slots=True)
class SessionFixture:
    profile: UserProfile
    plan: InterviewPlan
    interview: InterviewSession
    context: InterviewContextState
    connection: ModelConnection
    questions: list[PlanQuestion]
    segments: list[ConversationSegment]
    user_messages: list[InterviewMessage]


class InvalidSummaryProvider(ChatProvider):
    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content="{}",
            usage=Usage(input_tokens=100, output_tokens=4),
            provider_request_id="invalid-summary",
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)

    async def aclose(self) -> None:
        return None


async def _clear_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(MemoryUsage))
        await session.execute(delete(ContextSnapshot))
        await session.execute(delete(ContextSummary))
        await session.execute(delete(MemoryConflict))
        await session.execute(delete(MemorySource))
        await session.execute(delete(MemoryItem))
        await session.execute(delete(AnswerAttachment))
        await session.execute(delete(InterviewRealtimeEvent))
        await session.execute(delete(InterviewMessage))
        await session.execute(delete(InterviewContextState))
        await session.execute(delete(ConversationSegment))
        await session.execute(delete(InterviewSession))
        await session.execute(delete(BackgroundJob))
        await session.execute(delete(PlanQuestion))
        await session.execute(delete(InterviewPlan))
        await session.execute(delete(InterviewConfig))
        await session.execute(delete(RoundProfile))
        await session.execute(delete(CompanyStylePack))
        await session.execute(delete(Company))
        await session.execute(delete(ModelRoleBinding))
        await session.execute(delete(ModelConnection))
        await session.execute(delete(UserProfile))
        await session.commit()


async def _seed_session(
    *,
    question_count: int,
    context_window_tokens: int = 4_096,
    total_minutes: int = 60,
) -> SessionFixture:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="长会话验收")
        session.add(profile)
        await session.flush()
        company = Company(profile_id=profile.id, name="验收公司", slug="long-session-company")
        connection = ModelConnection(
            profile_id=profile.id,
            name="小上下文模型",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://example.test/v1",
            model_name="small-context-model",
            context_window_tokens=context_window_tokens,
            max_output_tokens=512,
        )
        session.add_all([company, connection])
        await session.flush()
        style = CompanyStylePack(company_id=company.id, name="长会话风格")
        session.add(style)
        await session.flush()
        round_profile = RoundProfile(
            style_pack_id=style.id,
            round_key="round_2",
            name="二面",
            pressure_level=2,
            duration_minutes=total_minutes,
        )
        session.add(round_profile)
        await session.flush()
        config = InterviewConfig(
            profile_id=profile.id,
            company_id=company.id,
            round_profile_id=round_profile.id,
            role_name="大模型应用开发",
            duration_minutes=total_minutes,
            target_question_count=question_count,
        )
        session.add(config)
        await session.flush()
        plan = InterviewPlan(
            config_id=config.id,
            style_pack_id=style.id,
            status=PlanStatus.FROZEN,
            total_minutes=total_minutes,
            plan_snapshot={"duration_minutes": total_minutes},
        )
        session.add(plan)
        await session.flush()
        questions = [
            PlanQuestion(
                plan_id=plan.id,
                sequence=index,
                prompt_snapshot=f"第 {index} 题：如何设计长对话上下文压缩？",
                capability_tags=["context_engineering"],
                follow_up_budget=2,
                selection_reason="长会话验收",
            )
            for index in range(1, question_count + 1)
        ]
        session.add_all(questions)
        await session.flush()
        interview = InterviewSession(
            profile_id=profile.id,
            plan_id=plan.id,
            status=SessionStatus.INTERVIEWING,
            current_question_sequence=question_count,
        )
        session.add(interview)
        await session.flush()
        context = InterviewContextState(
            session_id=interview.id,
            current_plan_question_id=questions[-1].id,
            completed_question_ids=[str(item.id) for item in questions[:-1]],
        )
        session.add(context)
        await session.flush()

        segments: list[ConversationSegment] = []
        user_messages: list[InterviewMessage] = []
        message_sequence = 1
        for index, question in enumerate(questions):
            current = index == len(questions) - 1
            segment = ConversationSegment(
                session_id=interview.id,
                plan_question_id=question.id,
                sequence=index + 1,
                status=SegmentStatus.OPEN if current else SegmentStatus.CLOSED,
                start_message_sequence=message_sequence,
                end_message_sequence=None if current else message_sequence + 1,
                token_count=0 if current else 120,
            )
            session.add(segment)
            await session.flush()
            assistant = InterviewMessage(
                session_id=interview.id,
                plan_question_id=question.id,
                segment_id=segment.id,
                role=MessageRole.ASSISTANT,
                sequence=message_sequence,
                content=question.prompt_snapshot,
            )
            session.add(assistant)
            message_sequence += 1
            if not current:
                user = InterviewMessage(
                    session_id=interview.id,
                    plan_question_id=question.id,
                    segment_id=segment.id,
                    role=MessageRole.USER,
                    sequence=message_sequence,
                    content=f"第 {index + 1} 题原始回答：保留证据边界并按层压缩。",
                )
                session.add(user)
                user_messages.append(user)
                message_sequence += 1
            segments.append(segment)
        await session.commit()
        entities = [
            profile,
            plan,
            interview,
            context,
            connection,
            *questions,
            *segments,
            *user_messages,
        ]
        for item in entities:
            await session.refresh(item)
        return SessionFixture(
            profile=profile,
            plan=plan,
            interview=interview,
            context=context,
            connection=connection,
            questions=questions,
            segments=segments,
            user_messages=user_messages,
        )


@pytest_asyncio.fixture(autouse=True)
async def isolated_long_session_data():
    await _clear_data()
    yield
    await _clear_data()
    await engine.dispose()


async def test_sixty_minute_small_context_triggers_repeated_auditable_compaction() -> None:
    seeded = await _seed_session(question_count=9)
    snapshots: list[ContextSnapshot] = []
    async with async_session_factory() as session:
        interview = await session.get(InterviewSession, seeded.interview.id)
        context = await session.get(InterviewContextState, seeded.context.id)
        connection = await session.get(ModelConnection, seeded.connection.id)
        current = await session.get(PlanQuestion, seeded.questions[-1].id)
        assert interview and context and connection and current

        for batch_start, batch_end in ((0, 2), (2, 5), (5, 8)):
            for index in range(batch_start, batch_end):
                segment = await session.get(ConversationSegment, seeded.segments[index].id)
                user = await session.get(InterviewMessage, seeded.user_messages[index].id)
                assert segment and user
                session.add(
                    ContextSummary(
                        segment_id=segment.id,
                        content={
                            "user_core_answer": {
                                "text": "结构化压缩证据" * 450,
                                "evidence_message_ids": [str(user.id)],
                            }
                        },
                        evidence_message_ids=[str(user.id)],
                        validation_status=SummaryValidationStatus.VALID,
                        token_count=1_500,
                    )
                )
            await session.commit()
            built = await build_interviewer_context(
                session,
                interview=interview,
                current=current,
                context=context,
                connection=connection,
            )
            snapshot = await session.get(ContextSnapshot, built.snapshot_id)
            assert snapshot
            snapshots.append(snapshot)

    assert seeded.plan.total_minutes == 60
    assert len(snapshots) == 3
    assert all(item.compaction_level >= 2 for item in snapshots)
    assert all(item.token_by_layer["tokens_removed"] > 0 for item in snapshots)
    assert all(
        item.input_tokens <= item.token_by_layer["effective_input_budget"] for item in snapshots
    )
    assert all(0 < item.token_by_layer["compression_ratio"] < 1 for item in snapshots)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/api/interview-sessions/{seeded.interview.id}/context/diagnostics"
        )
    assert response.status_code == 200
    diagnostics = response.json()
    assert diagnostics["summary"]["snapshot_count"] == 3
    assert diagnostics["summary"]["max_compaction_level"] >= 2
    assert "unresolved_points" not in diagnostics["current_state"]
    assert diagnostics["snapshots"][0]["token_by_layer"]["tokens_removed"] > 0


async def test_second_session_uses_only_active_memory_and_deletion_removes_it() -> None:
    seeded = await _seed_session(question_count=1, context_window_tokens=8_000)
    async with async_session_factory() as session:
        origin = InterviewSession(
            profile_id=seeded.profile.id,
            plan_id=seeded.plan.id,
            status=SessionStatus.COMPLETED,
        )
        session.add(origin)
        await session.flush()
        active = MemoryItem(
            profile_id=seeded.profile.id,
            memory_type=MemoryType.PROJECT_FACT,
            canonical_key="project.context.active",
            content="长对话上下文压缩项目由我负责证据追踪。",
            status=MemoryStatus.ACTIVE,
            confidence=0.95,
        )
        proposed = MemoryItem(
            profile_id=seeded.profile.id,
            memory_type=MemoryType.PROJECT_FACT,
            canonical_key="project.context.proposed",
            content="长对话上下文压缩可能使用未经确认的策略。",
            status=MemoryStatus.PROPOSED,
            confidence=0.95,
        )
        session.add_all([active, proposed])
        await session.flush()
        session.add_all(
            [
                MemorySource(
                    memory_id=active.id,
                    session_id=origin.id,
                    source_type="interview_evaluation",
                ),
                MemorySource(
                    memory_id=proposed.id,
                    session_id=origin.id,
                    source_type="interview_evaluation",
                ),
            ]
        )
        await session.commit()
        interview = await session.get(InterviewSession, seeded.interview.id)
        context = await session.get(InterviewContextState, seeded.context.id)
        connection = await session.get(ModelConnection, seeded.connection.id)
        current = await session.get(PlanQuestion, seeded.questions[0].id)
        assert interview and context and connection and current

        first = await build_interviewer_context(
            session,
            interview=interview,
            current=current,
            context=context,
            connection=connection,
        )
        first_snapshot = await session.get(ContextSnapshot, first.snapshot_id)
        assert first_snapshot
        assert str(active.id) in first_snapshot.included_refs["memories"]
        assert str(proposed.id) not in first_snapshot.included_refs["memories"]

        await delete_memory(session, active)
        second = await build_interviewer_context(
            session,
            interview=interview,
            current=current,
            context=context,
            connection=connection,
        )
        second_snapshot = await session.get(ContextSnapshot, second.snapshot_id)
        assert second_snapshot
        assert second_snapshot.included_refs["memories"] == []


async def test_summary_failure_preserves_raw_transcript_for_future_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed_session(question_count=2, context_window_tokens=8_000)
    async with async_session_factory() as session:
        interview = await session.get(InterviewSession, seeded.interview.id)
        context = await session.get(InterviewContextState, seeded.context.id)
        connection = await session.get(ModelConnection, seeded.connection.id)
        current = await session.get(PlanQuestion, seeded.questions[-1].id)
        previous_segment = await session.get(ConversationSegment, seeded.segments[0].id)
        raw_answer = await session.get(InterviewMessage, seeded.user_messages[0].id)
        assert interview and context and connection and current and previous_segment and raw_answer

        async def resolve_connection(*args, **kwargs) -> ModelConnection:
            return connection

        monkeypatch.setattr("app.context.summarizer.resolve_role_connection", resolve_connection)
        monkeypatch.setattr(
            "app.context.summarizer.build_provider",
            lambda configured: InvalidSummaryProvider(),
        )
        with pytest.raises(StructuredOutputError):
            await summarize_segment(session, segment_id=previous_segment.id)

        summary = await session.scalar(
            select(ContextSummary).where(ContextSummary.segment_id == previous_segment.id)
        )
        persisted_answer = await session.get(InterviewMessage, raw_answer.id)
        assert summary is None
        assert persisted_answer and persisted_answer.content == raw_answer.content

        built = await build_interviewer_context(
            session,
            interview=interview,
            current=current,
            context=context,
            connection=connection,
        )
        snapshot = await session.get(ContextSnapshot, built.snapshot_id)
        assert snapshot
        assert str(raw_answer.id) in snapshot.included_refs["messages"]
        assert raw_answer.content in [item.content for item in built.request.messages]


async def test_time_budget_ends_naturally_and_current_question_can_be_restated() -> None:
    seeded = await _seed_session(question_count=1, context_window_tokens=8_000)
    async with async_session_factory() as session:
        interview = await session.get(InterviewSession, seeded.interview.id)
        assert interview
        restated = await save_restatement(session, interview)
        assert restated.content == seeded.questions[0].prompt_snapshot
        assert restated.message_metadata["kind"] == "restatement"

        interview.started_at = utc_now() - timedelta(minutes=61)
        interview.touch()
        await session.commit()
        turn = await prepare_turn(session, interview)

    assert turn.should_finish is True
    assert turn.provider is None
    assert "时间已经结束" in (turn.static_prompt or "")


async def test_finish_is_idempotent_and_enqueues_one_evaluation_job() -> None:
    seeded = await _seed_session(question_count=1)
    async with async_session_factory() as session:
        interview = await session.get(InterviewSession, seeded.interview.id)
        assert interview
        first = await finish_session(session, interview)
        second = await finish_session(session, interview)
        evaluation_jobs = await session.scalar(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.job_type == JobType.INTERVIEW_EVALUATION,
                BackgroundJob.idempotency_key == f"interview-evaluation:{interview.id}:v1",
            )
        )

    assert first.status == SessionStatus.COMPLETED
    assert second.status == SessionStatus.COMPLETED
    assert evaluation_jobs == 1


async def test_replayed_answer_event_is_persisted_only_once() -> None:
    seeded = await _seed_session(question_count=1)
    async with async_session_factory() as session:
        interview = await session.get(InterviewSession, seeded.interview.id)
        assert interview
        first = await save_user_answer(
            session,
            interview,
            "同一个客户端事件的原始回答",
            client_event_id="answer-event-1",
        )
        replayed = await save_user_answer(
            session,
            interview,
            "这段重放内容不应覆盖原始回答",
            client_event_id="answer-event-1",
        )
        answer_count = await session.scalar(
            select(func.count(InterviewMessage.id)).where(
                InterviewMessage.session_id == interview.id,
                InterviewMessage.role == MessageRole.USER,
            )
        )

    assert replayed.id == first.id
    assert replayed.content == "同一个客户端事件的原始回答"
    assert answer_count == 1
