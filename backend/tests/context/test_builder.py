import json
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete

from app.context.builder import build_interviewer_context
from app.context.summarizer import summarize_segment
from app.db.models.common import (
    MessageRole,
    PlanStatus,
    ProviderType,
    SegmentStatus,
    SessionStatus,
    SummaryValidationStatus,
)
from app.db.models.company import Company, CompanyStylePack, RoundProfile
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
from app.db.models.model_connection import ModelConnection
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
    Usage,
)


class SummaryProvider(ChatProvider):
    def __init__(self, content: str) -> None:
        self.content = content

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content=self.content,
            usage=Usage(input_tokens=120, output_tokens=40),
            provider_request_id="summary-request-1",
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)

    async def aclose(self) -> None:
        return None


async def _clear_context_test_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(ContextSnapshot))
        await session.execute(delete(ContextSummary))
        await session.execute(delete(InterviewMessage))
        await session.execute(delete(InterviewContextState))
        await session.execute(delete(ConversationSegment))
        await session.execute(delete(InterviewSession))
        await session.execute(delete(PlanQuestion))
        await session.execute(delete(InterviewPlan))
        await session.execute(delete(InterviewConfig))
        await session.execute(delete(RoundProfile))
        await session.execute(delete(CompanyStylePack))
        await session.execute(delete(Company))
        await session.execute(delete(ModelConnection))
        await session.execute(delete(UserProfile))
        await session.commit()


async def test_builder_keeps_current_chain_and_persists_explainable_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _clear_context_test_data()
    try:
        async with async_session_factory() as session:
            profile = UserProfile(display_name="上下文测试")
            session.add(profile)
            await session.flush()
            company = Company(profile_id=profile.id, name="测试公司", slug="context-builder-test")
            connection = ModelConnection(
                profile_id=profile.id,
                name="上下文模型",
                provider_type=ProviderType.OPENAI_COMPATIBLE,
                base_url="https://example.test/v1",
                model_name="test-model",
                context_window_tokens=16_000,
                max_output_tokens=2_048,
            )
            session.add_all([company, connection])
            await session.flush()
            style = CompanyStylePack(
                company_id=company.id,
                name="高压技术面",
                default_interviewer_behavior={"tone": "direct", "depth": "deep"},
            )
            session.add(style)
            await session.flush()
            round_profile = RoundProfile(
                style_pack_id=style.id,
                round_key="round_2",
                name="二面",
                follow_up_patterns=["tradeoff", "failure"],
                pressure_level=3,
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
                status=PlanStatus.READY,
                total_minutes=45,
                plan_snapshot={"role_matrix": "llm_application"},
            )
            session.add(plan)
            await session.flush()
            question = PlanQuestion(
                plan_id=plan.id,
                sequence=1,
                prompt_snapshot="如何设计长对话的上下文压缩？",
                selection_reason="覆盖核心岗位能力",
                follow_up_budget=3,
            )
            session.add(question)
            await session.flush()
            interview = InterviewSession(
                profile_id=profile.id,
                plan_id=plan.id,
                status=SessionStatus.INTERVIEWING,
                current_question_sequence=1,
            )
            session.add(interview)
            await session.flush()
            context = InterviewContextState(
                session_id=interview.id,
                current_plan_question_id=question.id,
            )
            session.add(context)
            await session.flush()
            segment = ConversationSegment(
                session_id=interview.id,
                plan_question_id=question.id,
                sequence=1,
                start_message_sequence=1,
            )
            session.add(segment)
            await session.flush()
            answer = InterviewMessage(
                session_id=interview.id,
                plan_question_id=question.id,
                segment_id=segment.id,
                role=MessageRole.USER,
                sequence=1,
                content="我会保留当前题链，并对已关闭分段做结构化摘要。",
            )
            session.add(answer)
            await session.commit()

            built = await build_interviewer_context(
                session,
                interview=interview,
                current=question,
                context=context,
                connection=connection,
            )
            snapshot = await session.get(ContextSnapshot, built.snapshot_id)

            assert built.request.max_tokens == 512
            assert built.request.messages[-1].content == answer.content
            assert "大模型应用开发" in (built.request.system or "")
            assert "高压" not in (built.request.system or "")
            assert snapshot is not None
            assert str(answer.id) in snapshot.included_refs["messages"]
            assert snapshot.count_method.startswith("conservative_estimate")
            assert snapshot.input_tokens > 0
            assert snapshot.token_by_layer["safety_margin"] > 0

            segment.status = SegmentStatus.CLOSED
            segment.end_message_sequence = 1
            segment.touch()
            await session.commit()

            async def resolve_connection(*args, **kwargs) -> ModelConnection:
                return connection

            summary_content = json.dumps(
                {
                    "question_id": str(question.id),
                    "capability_tags": question.capability_tags,
                    "asked_question": question.prompt_snapshot,
                    "user_core_answer": {
                        "text": "保留当前题链并压缩已关闭分段",
                        "evidence_message_ids": [str(answer.id)],
                    },
                    "explicit_claims": [],
                    "decisions_and_tradeoffs": [],
                    "caveats_and_failures": [],
                    "unresolved_points": [],
                    "attachment_refs": [],
                    "evidence_message_ids": [str(answer.id)],
                },
                ensure_ascii=False,
            )
            monkeypatch.setattr(
                "app.context.summarizer.resolve_role_connection", resolve_connection
            )
            monkeypatch.setattr(
                "app.context.summarizer.build_provider",
                lambda configured: SummaryProvider(summary_content),
            )

            summary = await summarize_segment(session, segment_id=segment.id)

            assert summary.validation_status == SummaryValidationStatus.VALID
            assert summary.evidence_message_ids == [str(answer.id)]
            assert summary.token_count > 0
    finally:
        await _clear_context_test_data()
        await engine.dispose()
