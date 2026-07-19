import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db.models.common import JobStatus, JobType, SegmentStatus
from app.db.models.company import Company
from app.db.models.context import ConversationSegment, InterviewContextState
from app.db.models.interview import (
    InterviewConfig,
    InterviewMessage,
    InterviewPlan,
    InterviewRealtimeEvent,
    InterviewSession,
    PlanQuestion,
)
from app.db.models.job import BackgroundJob
from app.db.models.question import Question, QuestionBank, QuestionTagLink
from app.db.session import async_session_factory, engine
from app.main import app
from app.realtime.event_store import append_event, find_client_event, replay_events
from app.workers.context_summary_jobs import run_once as run_summary_once
from app.workers.plan_jobs import run_once as run_plan_once


async def clear_planning_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(BackgroundJob))
        await session.execute(delete(InterviewRealtimeEvent))
        await session.execute(delete(InterviewMessage))
        await session.execute(delete(ConversationSegment))
        await session.execute(delete(InterviewContextState))
        await session.execute(delete(InterviewSession))
        await session.execute(delete(PlanQuestion))
        await session.execute(delete(InterviewPlan))
        await session.execute(delete(InterviewConfig))
        await session.execute(delete(QuestionTagLink))
        await session.execute(delete(Question))
        await session.execute(delete(QuestionBank))
        await session.execute(delete(Company))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_planning_data():
    await clear_planning_data()
    yield
    await clear_planning_data()
    await engine.dispose()


def company_payload() -> dict:
    return {
        "name": "规划测试公司",
        "style_pack": {"name": "轮次骨架"},
        "rounds": [
            {
                "round_key": "round_2",
                "name": "二面",
                "sequence": 1,
                "duration_minutes": 45,
            }
        ],
    }


@pytest.mark.asyncio
async def test_plan_job_builds_traceable_ready_plan() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        company = (await client.post("/api/companies", json=company_payload())).json()
        bank = (
            await client.post(
                "/api/question-banks",
                json={"name": "LLM 基础"},
            )
        ).json()
        for prompt, tags in [
            ("RAG 的召回质量如何评估？", ["rag_and_retrieval"]),
            ("如何设计 Agent 工具调用的重试机制？", ["agent_engineering"]),
        ]:
            response = await client.post(
                "/api/questions",
                json={
                    "bank_id": bank["id"],
                    "prompt": prompt,
                    "status": "active",
                    "tag_names": tags,
                },
            )
            assert response.status_code == 201

        created = await client.post(
            "/api/interview-plans",
            json={
                "company_id": company["id"],
                "round_profile_id": company["latest_style_pack"]["rounds"][0]["id"],
                "duration_minutes": 45,
                "target_question_count": 4,
                "question_bank_ids": [bank["id"]],
            },
        )
        assert created.status_code == 202
        assert created.json()["job"]["status"] == "queued"
        assert created.json()["plan"]["questions"] == []

        assert await run_plan_once("test-planner") is True
        plan_id = created.json()["plan"]["id"]
        job_id = created.json()["job"]["id"]
        plan = await client.get(f"/api/interview-plans/{plan_id}")
        job = await client.get(f"/api/jobs/{job_id}")

    assert plan.status_code == 200
    payload = plan.json()
    assert payload["status"] == "ready"
    assert len(payload["questions"]) == 4
    assert sum(item["allocated_seconds"] for item in payload["questions"]) == 45 * 60
    assert [item["sequence"] for item in payload["questions"]] == [1, 2, 3, 4]
    assert all(item["source_ref"] for item in payload["questions"])
    assert payload["plan_snapshot"]["planner"] == "deterministic-v1"
    assert payload["plan_snapshot"]["source_distribution"]["manual"] == 2
    assert job.json()["status"] == "completed"

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created_session = await client.post(
            "/api/interview-sessions",
            json={"plan_id": plan_id},
        )
        session_id = created_session.json()["id"]
        started = await client.post(f"/api/interview-sessions/{session_id}/start")
        started_again = await client.post(f"/api/interview-sessions/{session_id}/start")
        paused = await client.post(f"/api/interview-sessions/{session_id}/pause")
        resumed = await client.post(f"/api/interview-sessions/{session_id}/resume")
        finished = await client.post(f"/api/interview-sessions/{session_id}/finish")
        finished_again = await client.post(f"/api/interview-sessions/{session_id}/finish")

    assert created_session.status_code == 201
    assert created_session.json()["plan"]["status"] == "frozen"
    assert started.json()["status"] == "interviewing"
    assert len(started.json()["messages"]) == 1
    assert len(started_again.json()["messages"]) == 1
    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] == "interviewing"
    assert finished.json()["status"] == "completed"
    assert finished_again.json()["ended_at"] == finished.json()["ended_at"]

    assert await run_summary_once("test-summary-worker") is True

    async with async_session_factory() as database:
        interview = await database.get(InterviewSession, session_id)
        assert interview is not None
        segment = await database.scalar(
            select(ConversationSegment).where(ConversationSegment.session_id == interview.id)
        )
        summary_job = await database.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == JobType.CONTEXT_SUMMARY,
                BackgroundJob.payload["session_id"].astext == str(interview.id),
            )
        )
        assert segment is not None
        assert segment.status == SegmentStatus.SUMMARY_FAILED
        assert segment.end_message_sequence == 1
        assert segment.token_count > 0
        assert summary_job is not None
        assert summary_job.status == JobStatus.FAILED
        assert summary_job.error_code == "model_role_unbound"
        first_event = await append_event(
            database,
            interview,
            event_type="input.ack",
            payload={"message_id": "message-1"},
            client_event_id="client-event-1",
        )
        await append_event(
            database,
            interview,
            event_type="assistant.message",
            payload={"message_id": "message-2"},
        )
        duplicate = await find_client_event(database, interview.id, "client-event-1")
        replay = await replay_events(database, interview.id, first_event.sequence)

    assert duplicate is not None
    assert duplicate.event_id == first_event.event_id
    assert [event.type for event in replay] == ["assistant.message"]


@pytest.mark.asyncio
async def test_plan_rejects_round_from_another_company() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        first = (await client.post("/api/companies", json=company_payload())).json()
        other_payload = company_payload()
        other_payload["name"] = "另一家公司"
        second = (await client.post("/api/companies", json=other_payload)).json()
        response = await client.post(
            "/api/interview-plans",
            json={
                "company_id": first["id"],
                "round_profile_id": second["latest_style_pack"]["rounds"][0]["id"],
            },
        )

    assert response.status_code == 404
    assert response.json()["code"] == "round_profile_not_found"
