"""A real backend core-flow test using PostgreSQL and the local mock provider.

This deliberately does not stub ``build_provider`` or intercept browser requests.  The
application is exercised through FastAPI's TestClient, while a separate uvicorn
process serves the OpenAI-compatible deterministic provider over a real loopback
HTTP connection.
"""

import asyncio
import socket
import threading
import time
import uuid
from collections.abc import Generator
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models.company import Company
from app.db.models.evaluation import EvaluationReport
from app.db.models.interview import (
    InterviewConfig,
    InterviewPlan,
    InterviewRealtimeEvent,
    InterviewSession,
    PlanQuestion,
)
from app.db.models.job import BackgroundJob
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.models.question import QuestionBank
from app.db.session import async_session_factory, dispose_engine
from app.dev.mock_provider import app as mock_provider_app
from app.main import app
from app.workers.evaluation_jobs import run_once as run_evaluation_once
from app.workers.plan_jobs import run_once as run_plan_once


def _start_mock_provider() -> tuple[uvicorn.Server, threading.Thread, socket.socket, str]:
    """Start the deterministic provider on a reserved loopback socket."""

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            mock_provider_app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
        name="interview-helper-mock-provider",
    )
    thread.start()
    return server, thread, listener, f"http://127.0.0.1:{port}"


@pytest.fixture
def mock_provider_url() -> Generator[str]:
    server, thread, listener, base_url = _start_mock_provider()
    deadline = time.monotonic() + 8
    try:
        with httpx.Client(timeout=0.25, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    response = client.get(f"{base_url}/health")
                except httpx.HTTPError:
                    time.sleep(0.05)
                    continue
                if response.status_code == 200:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("local deterministic mock provider did not become ready")
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        try:
            listener.close()
        except OSError:
            pass
        assert not thread.is_alive(), "local deterministic mock provider did not stop"


async def _cleanup_created_data(state: dict[str, str]) -> None:
    """Remove only rows created by this test, preserving any unrelated test fixtures."""

    session_id = state.get("session_id")
    plan_id = state.get("plan_id")
    config_id = state.get("config_id")
    bank_id = state.get("bank_id")
    company_id = state.get("company_id")
    connection_id = state.get("connection_id")

    async with async_session_factory() as session:
        if session_id:
            await session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.payload["session_id"].astext == session_id
                )
            )
            await session.execute(
                delete(EvaluationReport).where(EvaluationReport.session_id == uuid.UUID(session_id))
            )
            await session.execute(
                delete(InterviewRealtimeEvent).where(
                    InterviewRealtimeEvent.session_id == uuid.UUID(session_id)
                )
            )
            await session.execute(
                delete(InterviewSession).where(InterviewSession.id == uuid.UUID(session_id))
            )
        if plan_id:
            await session.execute(
                delete(BackgroundJob).where(BackgroundJob.payload["plan_id"].astext == plan_id)
            )
            await session.execute(
                delete(PlanQuestion).where(PlanQuestion.plan_id == uuid.UUID(plan_id))
            )
            await session.execute(
                delete(InterviewPlan).where(InterviewPlan.id == uuid.UUID(plan_id))
            )
        if config_id:
            await session.execute(
                delete(InterviewConfig).where(InterviewConfig.id == uuid.UUID(config_id))
            )
        if bank_id:
            await session.execute(delete(QuestionBank).where(QuestionBank.id == uuid.UUID(bank_id)))
        if company_id:
            await session.execute(delete(Company).where(Company.id == uuid.UUID(company_id)))
        if connection_id:
            await session.execute(
                delete(ModelRoleBinding).where(
                    ModelRoleBinding.connection_id == uuid.UUID(connection_id)
                )
            )
            await session.execute(
                delete(ModelConnection).where(ModelConnection.id == uuid.UUID(connection_id))
            )
        await session.commit()


async def _cleanup_and_dispose(state: dict[str, str]) -> None:
    try:
        await _cleanup_created_data(state)
    finally:
        await dispose_engine()


async def _run_worker_and_dispose(worker, worker_id: str) -> bool:
    try:
        return await worker(worker_id)
    finally:
        await dispose_engine()


@pytest.fixture
def created_data() -> Generator[dict[str, str]]:
    state: dict[str, str] = {}
    try:
        yield state
    finally:
        asyncio.run(_cleanup_and_dispose(state))


def _connection_payload(name: str, mock_provider_url: str) -> dict[str, Any]:
    return {
        "name": name,
        "provider_type": "openai_compatible",
        "base_url": f"{mock_provider_url}/v1",
        "api_key": "local-integration-test-key",
        "model_name": "mock-interview",
        "context_window_tokens": 32_768,
        "max_output_tokens": 1_024,
        "tokenizer_type": "estimated",
        "supports_prompt_caching": False,
        "supports_token_count_endpoint": False,
    }


def _receive_until(
    websocket,
    predicate,
    *,
    maximum_events: int = 8,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    received: list[dict[str, Any]] = []
    for _ in range(maximum_events):
        event = websocket.receive_json()
        received.append(event)
        if predicate(event):
            return event, received
    raise AssertionError(f"did not receive expected event; received={received!r}")


def test_real_postgres_fastapi_and_mock_provider_complete_core_interview_flow(
    mock_provider_url: str,
    created_data: dict[str, str],
) -> None:
    """Run the core interview lifecycle without fake providers or HTTP interception.

    The chain is intentionally end-to-end at the backend boundary:
    connection health -> role bindings -> queued plan -> plan worker -> WebSocket
    answer/follow-up -> evaluation worker -> persisted report with cited evidence.
    """

    suffix = uuid.uuid4().hex[:10]
    connection_name = f"local-mock-core-{suffix}"

    with TestClient(app, raise_server_exceptions=True) as client:
        created_connection = client.post(
            "/api/model-connections",
            json=_connection_payload(connection_name, mock_provider_url),
        )
        assert created_connection.status_code == 201, created_connection.text
        connection_id = created_connection.json()["id"]
        created_data["connection_id"] = connection_id

        # This is a real HTTP health call from the backend provider adapter to uvicorn.
        tested_connection = client.post(f"/api/model-connections/{connection_id}/test")
        assert tested_connection.status_code == 200, tested_connection.text
        assert tested_connection.json()["status"] == "healthy"

        for role in ("interviewer", "evaluator"):
            binding = client.put(
                f"/api/model-connections/roles/{role}",
                json={"connection_id": connection_id},
            )
            assert binding.status_code == 200, binding.text
        assert client.get("/api/model-connections/readiness").json() == {
            "ready": True,
            "missing_roles": [],
            "degraded_roles": [],
        }

        company = client.post(
            "/api/companies",
            json={
                "name": f"Real Core Flow {suffix}",
                "style_pack": {
                    "name": "本地真实链路风格",
                    "default_interviewer_behavior": {"tone": "direct"},
                },
                "rounds": [
                    {
                        "round_key": "technical_1",
                        "name": "技术一面",
                        "sequence": 1,
                        "pressure_level": 2,
                        "evaluation_weights": {"system_design": 1.0},
                        "duration_minutes": 10,
                    }
                ],
            },
        )
        assert company.status_code == 201, company.text
        company_body = company.json()
        created_data["company_id"] = company_body["id"]
        round_id = company_body["latest_style_pack"]["rounds"][0]["id"]

        bank = client.post("/api/question-banks", json={"name": f"Core E2E Bank {suffix}"})
        assert bank.status_code == 201, bank.text
        bank_id = bank.json()["id"]
        created_data["bank_id"] = bank_id
        question = client.post(
            "/api/questions",
            json={
                "bank_id": bank_id,
                "prompt": "请设计一个可观测、可回滚的 RAG 检索链路。",
                "status": "active",
                "tag_names": ["rag_and_retrieval", "system_design"],
                "follow_up_suggestions": ["请量化说明容量和失败降级路径。"],
            },
        )
        assert question.status_code == 201, question.text

        queued_plan = client.post(
            "/api/interview-plans",
            json={
                "company_id": company_body["id"],
                "round_profile_id": round_id,
                "role_name": "llm_application_engineer",
                "duration_minutes": 10,
                "target_question_count": 1,
                "question_bank_ids": [bank_id],
                "source_weights": {"manual": 1.0, "resume": 0.0, "generated": 0.0},
            },
        )
        assert queued_plan.status_code == 202, queued_plan.text
        plan_body = queued_plan.json()["plan"]
        plan_id = plan_body["id"]
        created_data["plan_id"] = plan_id
        created_data["config_id"] = plan_body["config_id"]
        assert queued_plan.json()["job"]["status"] == "queued"

    # The app lifespan disposes its async pool on TestClient exit.  Workers then run
    # against the same real PostgreSQL database, just as the local worker process does.
    assert asyncio.run(_run_worker_and_dispose(run_plan_once, "real-core-flow-plan-worker")) is True

    with TestClient(app, raise_server_exceptions=True) as client:
        ready_plan = client.get(f"/api/interview-plans/{plan_id}")
        assert ready_plan.status_code == 200, ready_plan.text
        assert ready_plan.json()["status"] == "ready"
        assert len(ready_plan.json()["questions"]) == 1
        assert ready_plan.json()["questions"][0]["follow_up_budget"] == 1
        assert ready_plan.json()["plan_snapshot"]["planner"] == "model-v1"
        assert ready_plan.json()["plan_snapshot"]["planner_schema_version"] == "planner.v1"

        started = client.post("/api/interview-sessions", json={"plan_id": plan_id})
        assert started.status_code == 201, started.text
        session_id = started.json()["id"]
        created_data["session_id"] = session_id
        session_started = client.post(f"/api/interview-sessions/{session_id}/start")
        assert session_started.status_code == 200, session_started.text
        assert session_started.json()["status"] == "interviewing"

        answer_text = "我先定义离线召回与线上点击指标，再以灰度和可回滚索引发布控制风险。"
        with client.websocket_connect(f"/api/interviews/{session_id}/live") as websocket:
            initial_state, _ = _receive_until(
                websocket,
                lambda event: event["type"] == "session.state",
                maximum_events=2,
            )
            assert initial_state["payload"]["status"] == "interviewing"
            timer, _ = _receive_until(
                websocket,
                lambda event: event["type"] == "timer.update",
                maximum_events=2,
            )
            assert timer["payload"]["total_seconds"] == 600

            websocket.send_json(
                {
                    "event_id": str(uuid.uuid4()),
                    "type": "user.text.submit",
                    "sequence": 1,
                    "payload": {"text": answer_text, "attachments": []},
                }
            )
            assistant_event, answer_events = _receive_until(
                websocket,
                lambda event: event["type"] == "assistant.message",
            )
            ack = next(event for event in answer_events if event["type"] == "input.ack")
            answer_id = ack["payload"]["message"]["id"]
            assert ack["payload"]["message"]["content"] == answer_text
            assert any(event["type"] == "assistant.delta" for event in answer_events)
            assert assistant_event["payload"]["message"]["content"].strip()

            websocket.send_json(
                {
                    "event_id": str(uuid.uuid4()),
                    "type": "session.finish",
                    "sequence": 2,
                    "payload": {},
                }
            )
            completed_event, _ = _receive_until(
                websocket,
                lambda event: (
                    event["type"] == "session.state" and event["payload"]["status"] == "completed"
                ),
            )
            assert completed_event["payload"]["status"] == "completed"

        diagnostics = client.get(f"/api/interview-sessions/{session_id}/context/diagnostics")
        assert diagnostics.status_code == 200, diagnostics.text
        snapshots = diagnostics.json()["snapshots"]
        assert snapshots
        assert snapshots[0]["model_connection_id"] == connection_id
        # These are the deterministic usage chunks emitted by the real streamed mock response.
        assert snapshots[0]["input_tokens"] == 64
        assert snapshots[0]["output_tokens"] == 32

    assert (
        asyncio.run(
            _run_worker_and_dispose(run_evaluation_once, "real-core-flow-evaluation-worker")
        )
        is True
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        report_response = client.get(f"/api/interview-sessions/{session_id}/report")
        assert report_response.status_code == 200, report_response.text
        report = report_response.json()
        assert report["status"] == "completed"
        assert report["job"]["status"] == "completed"
        assert len(report["questions"]) == 1
        assert report["questions"][0]["evidence"][0]["message_id"] == answer_id
        assert report["evidence_messages"][0]["content"] == answer_text
        # This deterministic overview is generated by app.dev.mock_provider, proving
        # the evaluation worker used the real configured OpenAI-compatible endpoint.
        assert "Provider" in report["overview"]
