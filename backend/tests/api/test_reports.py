import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.api.routes import report_coach as coach_route
from app.db.models.common import (
    AttachmentType,
    EvaluationStatus,
    JobStatus,
    JobType,
    MessageRole,
    PlanStatus,
    SessionStatus,
    utc_now,
)
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.evaluation import (
    DimensionEvaluation,
    EvaluationReport,
    QuestionEvaluation,
)
from app.db.models.interview import (
    AnswerAttachment,
    InterviewConfig,
    InterviewMessage,
    InterviewPlan,
    InterviewSession,
    PlanQuestion,
)
from app.db.models.job import BackgroundJob
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.main import app
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
)
from app.schemas.evaluation import CoachResponse
from app.services.evaluation import evaluate_interview
from app.workers import evaluation_jobs


class ReportProvider(ChatProvider):
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request.model_copy(deep=True))
        return ChatResponse(content=json.dumps(self.response))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)


async def _clear_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(DimensionEvaluation))
        await session.execute(delete(QuestionEvaluation))
        await session.execute(delete(EvaluationReport))
        await session.execute(delete(BackgroundJob))
        await session.execute(delete(InterviewMessage))
        await session.execute(delete(InterviewSession))
        await session.execute(delete(PlanQuestion))
        await session.execute(delete(InterviewPlan))
        await session.execute(delete(InterviewConfig))
        await session.execute(delete(RoundProfile))
        await session.execute(delete(CompanyStylePack))
        await session.execute(delete(Company))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def clean_database():
    await _clear_data()
    yield
    await _clear_data()
    await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client


async def _seed_finished_interview():
    async with async_session_factory() as session:
        profile = UserProfile(display_name="报告测试用户")
        session.add(profile)
        await session.flush()
        company = Company(
            profile_id=profile.id,
            name="证据公司",
            slug="evidence-report-company",
        )
        session.add(company)
        await session.flush()
        style = CompanyStylePack(
            company_id=company.id,
            name="工程面试风格",
            pack_version=3,
        )
        session.add(style)
        await session.flush()
        round_profile = RoundProfile(
            style_pack_id=style.id,
            round_key="round_2",
            name="二面",
            evaluation_weights={"system_design": 0.5},
        )
        session.add(round_profile)
        await session.flush()
        config = InterviewConfig(
            profile_id=profile.id,
            company_id=company.id,
            round_profile_id=round_profile.id,
            role_name="大模型应用开发",
            target_question_count=2,
        )
        session.add(config)
        await session.flush()
        plan = InterviewPlan(
            config_id=config.id,
            style_pack_id=style.id,
            status=PlanStatus.FROZEN,
            total_minutes=45,
            frozen_at=utc_now(),
            plan_snapshot={"style_pack_version": 3},
        )
        session.add(plan)
        await session.flush()
        questions = [
            PlanQuestion(
                plan_id=plan.id,
                sequence=index,
                prompt_snapshot=prompt,
                capability_tags=["system_design"],
                selection_reason="覆盖核心能力",
            )
            for index, prompt in enumerate(
                ["如何设计 RAG 检索链路？", "如何压缩长会话上下文？"],
                start=1,
            )
        ]
        session.add_all(questions)
        await session.flush()
        interview = InterviewSession(
            profile_id=profile.id,
            plan_id=plan.id,
            status=SessionStatus.COMPLETED,
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        session.add(interview)
        await session.flush()
        messages: list[InterviewMessage] = []
        sequence = 1
        for question in questions:
            messages.extend(
                [
                    InterviewMessage(
                        session_id=interview.id,
                        plan_question_id=question.id,
                        role=MessageRole.ASSISTANT,
                        sequence=sequence,
                        content=question.prompt_snapshot,
                    ),
                    InterviewMessage(
                        session_id=interview.id,
                        plan_question_id=question.id,
                        role=MessageRole.USER,
                        sequence=sequence + 1,
                        content="先澄清规模，再给出架构、取舍和失败降级策略。",
                    ),
                ]
            )
            sequence += 2
        session.add_all(messages)
        await session.commit()
        return profile, interview, questions, [messages[1], messages[3]]


def _response(questions, answers) -> dict:
    dimensions = [
        "technical_depth",
        "problem_solving",
        "communication",
        "system_design",
    ]
    return {
        "overall_anchor": "solid",
        "overview": "能够给出完整主线，但量化仍不足。",
        "strengths": ["架构思路完整"],
        "gaps": ["缺少容量数字"],
        "action_plan": [
            {
                "title": "容量估算训练",
                "instruction": "每天完成一道系统设计量级估算。",
                "success_criteria": "五分钟内得到合理数量级。",
                "priority": 1,
            }
        ],
        "questions": [
            {
                "plan_question_id": str(question.id),
                "anchor": "solid",
                "summary": "回答覆盖架构与降级。",
                "evidence": [
                    {"message_id": str(answer.id), "claim": "说明了架构和降级策略"}
                ],
                "gaps": ["缺少量化"],
                "actions": ["增加 QPS 估算"],
                "confidence": 0.82,
            }
            for question, answer in zip(questions, answers, strict=True)
        ],
        "dimensions": [
            {
                "dimension": dimension,
                "anchor": "solid",
                "evidence": [
                    {"message_id": str(answers[0].id), "claim": "解释了核心取舍"}
                ],
                "gaps": ["量化不足"],
                "action": "补充可验证的数量级和边界。",
                "confidence": 0.76,
            }
            for dimension in dimensions
        ],
    }


@pytest.mark.asyncio
async def test_evaluation_persists_only_raw_answer_evidence_and_report_api(
    client: AsyncClient,
) -> None:
    _, interview, questions, answers = await _seed_finished_interview()
    async with async_session_factory() as session:
        session.add(
            AnswerAttachment(
                message_id=answers[0].id,
                attachment_type=AttachmentType.CODE,
                filename="solution.py",
                mime_type="text/plain",
                language="python",
                content="def solve():\n    return 42",
                size_bytes=26,
                attachment_metadata={"execution_allowed": False},
            )
        )
        await session.commit()
    provider = ReportProvider(_response(questions, answers))
    async with async_session_factory() as session:
        interview_row = await session.get(InterviewSession, interview.id)
        assert interview_row is not None
        report = await evaluate_interview(session, interview_row, provider=provider)
        report_id = report.id

    assert provider.requests
    request_payload = json.loads(provider.requests[0].messages[0].content)
    assert request_payload["contract"]["style_pack_version"] == 3
    assert len(request_payload["interview_messages"]) == 4
    answer_payload = next(
        item for item in request_payload["interview_messages"]
        if item["message_id"] == str(answers[0].id)
    )
    assert answer_payload["attachments"][0]["content"] == "def solve():\n    return 42"
    assert answer_payload["attachments"][0]["execution_allowed"] is False

    response = await client.get(f"/api/reports/{report_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == EvaluationStatus.COMPLETED
    assert body["trend_comparison"] == {}
    assert len(body["questions"]) == 2
    assert len(body["dimensions"]) == 4
    assert {item["id"] for item in body["evidence_messages"]} == {
        str(item.id) for item in answers
    }
    evidence_answer = next(
        item for item in body["evidence_messages"] if item["id"] == str(answers[0].id)
    )
    assert evidence_answer["attachments"][0]["filename"] == "solution.py"
    assert all(item["confidence"] > 0 for item in body["questions"])

    listing = await client.get("/api/reports")
    assert listing.status_code == 200
    assert listing.json()[0]["company_name"] == "证据公司"
    assert listing.json()[0]["round_name"] == "二面"


@pytest.mark.asyncio
async def test_evaluation_worker_publishes_safe_stage_progress(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, interview, _, _ = await _seed_finished_interview()
    async with async_session_factory() as session:
        report = EvaluationReport(session_id=interview.id)
        job = BackgroundJob(
            profile_id=profile.id,
            job_type=JobType.INTERVIEW_EVALUATION,
            status=JobStatus.QUEUED,
            payload={"session_id": str(interview.id)},
            idempotency_key=f"evaluation-worker-test:{interview.id}",
        )
        session.add_all([report, job])
        await session.commit()
        report_id = report.id
        job_id = job.id

    async def fake_handler(session, *, session_id):
        assert session_id == interview.id
        result = await session.get(EvaluationReport, report_id)
        assert result is not None
        return result

    monkeypatch.setattr(evaluation_jobs, "handle_evaluate_interview", fake_handler)
    assert await evaluation_jobs.run_once("test-worker") is True

    async with async_session_factory() as session:
        completed = await session.get(BackgroundJob, job_id)
        assert completed is not None
        assert completed.status == JobStatus.COMPLETED
        assert completed.progress == 1
        assert completed.result == {
            "phase": "completed",
            "report_id": str(report_id),
            "session_id": str(interview.id),
        }

    events = await client.get(f"/api/jobs/{job_id}/events")
    assert events.status_code == 200
    assert '"phase":"completed"' in events.text
    assert "reasoning" not in events.text.lower()


@pytest.mark.asyncio
async def test_coach_route_receives_only_report_and_cited_answer_fragments(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, interview, questions, answers = await _seed_finished_interview()
    provider = ReportProvider(_response(questions, answers))
    async with async_session_factory() as session:
        interview_row = await session.get(InterviewSession, interview.id)
        assert interview_row is not None
        report = await evaluate_interview(session, interview_row, provider=provider)
        report_id = report.id
        question_evaluation = await session.scalar(
            select(QuestionEvaluation).where(QuestionEvaluation.report_id == report.id)
        )
        assert question_evaluation is not None

    captured: dict = {}

    async def fake_resolve(*args, **kwargs):
        return object()

    async def fake_coach(provider, *, mode, report_context, allowed_message_ids):
        captured.update(report_context)
        assert allowed_message_ids == {answers[0].id}
        return CoachResponse(
            mode=mode,
            title="证据解释",
            explanation="解释只基于所选原回答。",
            original_answer=answers[0].content,
            source_message_ids=[answers[0].id],
        )

    monkeypatch.setattr(coach_route, "resolve_role_connection", fake_resolve)
    monkeypatch.setattr(coach_route, "build_provider", lambda connection: provider)
    monkeypatch.setattr(coach_route, "run_coach", fake_coach)

    response = await client.post(
        f"/api/reports/{report_id}/coach",
        json={
            "mode": "explain",
            "question_evaluation_id": str(question_evaluation.id),
        },
    )

    assert response.status_code == 200
    assert captured["original_answers"] == [
        {"message_id": str(answers[0].id), "content": answers[0].content}
    ]
    assert "resume" not in captured
    assert "memory" not in captured
