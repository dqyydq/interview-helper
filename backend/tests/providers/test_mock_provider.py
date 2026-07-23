import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.dev.mock_provider import app


@pytest.mark.asyncio
async def test_mock_provider_supports_health_chat_stream_and_transcription() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://mock",
    ) as client:
        health = await client.get("/health")
        chat = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-interview",
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
        stream = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-interview",
                "messages": [{"role": "user", "content": "请继续"}],
                "stream": True,
            },
        )
        transcription = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("answer.webm", b"mock-audio", "audio/webm")},
        )

    assert health.json()["mode"] == "deterministic-local-only"
    assert chat.json()["choices"][0]["message"]["content"] == "pong"
    assert "data: [DONE]" in stream.text
    assert "关键指标" in stream.text
    assert transcription.json()["language"] == "zh"


@pytest.mark.asyncio
async def test_mock_provider_generates_traceable_evaluation_schema() -> None:
    question_id = "11111111-1111-4111-8111-111111111111"
    message_id = "22222222-2222-4222-8222-222222222222"
    source = {
        "contract": {"expected_dimensions": ["technical_depth", "communication"]},
        "plan_questions": [{"plan_question_id": question_id, "sequence": 1}],
        "interview_messages": [
            {
                "message_id": message_id,
                "plan_question_id": question_id,
                "role": "user",
                "confirmed": True,
                "content": "answer",
            }
        ],
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://mock",
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-interview",
                "messages": [{"role": "user", "content": json.dumps(source)}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_output",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "overall_anchor": {},
                                "questions": {},
                                "dimensions": {},
                            },
                        },
                    },
                },
            },
        )

    result = json.loads(response.json()["choices"][0]["message"]["content"])
    assert result["questions"][0]["plan_question_id"] == question_id
    assert result["questions"][0]["evidence"][0]["message_id"] == message_id
    assert [item["dimension"] for item in result["dimensions"]] == [
        "technical_depth",
        "communication",
    ]


@pytest.mark.asyncio
async def test_mock_provider_generates_source_grounded_resume_structure() -> None:
    source = {
        "source_sections": [
            {
                "sequence": 1,
                "heading": "Experience",
                "lines": [
                    {"line": 1, "content": "Built an interview service"},
                ],
            }
        ]
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://mock",
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-interview",
                "messages": [{"role": "user", "content": json.dumps(source)}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_output",
                        "schema": {"type": "object", "properties": {"sections": {}, "claims": {}}},
                    },
                },
            },
        )

    result = json.loads(response.json()["choices"][0]["message"]["content"])
    assert result["sections"][0]["section_type"] == "experience"
    assert result["claims"][0]["content"] == "Built an interview service"


@pytest.mark.asyncio
async def test_mock_provider_generates_a_source_bounded_planner_draft() -> None:
    source = {
        "contract": {"duration_seconds": 600},
        "candidate_pool": [
            {
                "candidate_key": "question:one",
                "capability_tags": ["system_design"],
                "max_follow_up_budget": 2,
            },
            {
                "candidate_key": "template:two",
                "capability_tags": ["agent_engineering", "system_design"],
                "max_follow_up_budget": 3,
            },
        ],
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://mock",
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-interview",
                "messages": [{"role": "user", "content": json.dumps(source)}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_output",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "questions": {},
                                "rationale": {},
                                "capability_coverage": {},
                            },
                        },
                    },
                },
            },
        )

    result = json.loads(response.json()["choices"][0]["message"]["content"])
    assert [item["candidate_key"] for item in result["questions"]] == [
        "question:one",
        "template:two",
    ]
    assert sum(item["allocated_seconds"] for item in result["questions"]) == 600
    assert result["capability_coverage"] == ["system_design", "agent_engineering"]
