import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db.models.company import Company
from app.db.session import async_session_factory, engine
from app.main import app


async def clear_companies() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Company))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_companies():
    await clear_companies()
    yield
    await clear_companies()
    await engine.dispose()


def company_payload() -> dict:
    return {
        "name": "示例科技",
        "description": "用户维护的公司资料",
        "style_pack": {
            "name": "LLM 应用开发草案",
            "supported_roles": ["llm_application_engineer"],
            "default_interviewer_behavior": {"follow_up": "从项目证据继续追问"},
        },
        "rounds": [
            {
                "round_key": "round_1",
                "name": "一面",
                "sequence": 1,
                "duration_minutes": 45,
            },
            {
                "round_key": "round_2",
                "name": "二面",
                "sequence": 2,
                "duration_minutes": 60,
            },
        ],
    }


@pytest.mark.asyncio
async def test_company_round_evidence_and_revision_lifecycle() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/companies", json=company_payload())

        assert created.status_code == 201
        company = created.json()
        company_id = company["id"]
        style_pack = company["latest_style_pack"]
        assert style_pack["status"] == "draft"
        assert style_pack["evidence_count"] == 0
        assert len(style_pack["rounds"]) == 2

        evidence = await client.post(
            f"/api/style-packs/{style_pack['id']}/evidence",
            json={
                "source_url": "https://example.com/interview-notes",
                "source_title": "候选人复盘",
                "field_path": "rounds.round_2.follow_up_patterns",
                "excerpt": "面试官围绕项目取舍连续追问。",
                "confidence": 0.7,
            },
        )
        assert evidence.status_code == 201

        detail = await client.get(f"/api/companies/{company_id}")
        assert detail.json()["latest_style_pack"]["evidence_count"] == 1
        assert detail.json()["latest_style_pack"]["evidence_label"] == "有来源支持"

        activated = await client.post(f"/api/style-packs/{style_pack['id']}/activate")
        assert activated.status_code == 200
        immutable = await client.patch(
            f"/api/style-packs/{style_pack['id']}",
            json={"name": "不应直接修改"},
        )
        assert immutable.status_code == 409
        assert immutable.json()["code"] == "style_pack_immutable"

        revision = await client.post(
            f"/api/companies/{company_id}/style-packs",
            json={"name": "LLM 应用开发草案 v2", "copy_evidence": True},
        )
        assert revision.status_code == 201
        revision_data = revision.json()
        assert revision_data["pack_version"] == 2
        assert revision_data["status"] == "draft"
        assert revision_data["evidence_count"] == 1
        assert len(revision_data["rounds"]) == 2

        first_round = revision_data["rounds"][0]
        updated_round = await client.patch(
            f"/api/rounds/{first_round['id']}",
            json={"pressure_level": 3, "duration_minutes": 50},
        )
        assert updated_round.status_code == 200
        assert updated_round.json()["pressure_level"] == 3

        duplicate_sequence = await client.post(
            f"/api/style-packs/{revision_data['id']}/rounds",
            json={"round_key": "duplicate", "name": "重复轮次", "sequence": 1},
        )
        assert duplicate_sequence.status_code == 409
        assert duplicate_sequence.json()["code"] == "database_conflict"

        activated_revision = await client.post(
            f"/api/style-packs/{revision_data['id']}/activate"
        )
        assert activated_revision.status_code == 200
        assert activated_revision.json()["status"] == "active"


@pytest.mark.asyncio
async def test_archived_company_is_hidden_by_default() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/companies", json=company_payload())
        company_id = created.json()["id"]
        archived = await client.delete(f"/api/companies/{company_id}")
        visible = await client.get("/api/companies")
        including_archived = await client.get("/api/companies?include_archived=true")

    assert archived.status_code == 204
    assert visible.json() == []
    assert including_archived.status_code == 200
    assert including_archived.json()[0]["archived"] is True
