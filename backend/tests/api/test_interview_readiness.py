import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services import interview_readiness as readiness_service


@pytest.mark.asyncio
async def test_readiness_degrades_safely_when_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable() -> str:
        return "unavailable"

    monkeypatch.setattr(readiness_service, "database_healthcheck", unavailable)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/interview-readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["defaults"]["quick_trial"] == {
        "session_kind": "quick_trial",
        "duration_minutes": 10,
        "target_question_count": 2,
        "include_in_trends": False,
        "role_name": "llm_application_engineer",
    }
    by_key = {item["key"]: item for item in body["blocking"]}
    assert by_key["database"]["status"] == "blocked"
    assert by_key["worker"]["status"] == "blocked"
    assert by_key["interviewer_model"]["status"] == "blocked"
    assert by_key["evaluator_model"]["status"] == "blocked"
    assert body["company_profile"] == {
        "company_id": None,
        "company_name": None,
        "round_profile_id": None,
        "round_name": None,
        "style_pack_id": None,
        "pack_version": None,
        "trust_status": None,
        "trust_label": None,
        "evidence_count": 0,
        "latest_evidence_at": None,
        "source_summaries": [],
    }
