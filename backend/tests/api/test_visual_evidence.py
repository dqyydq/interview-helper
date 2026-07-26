import base64

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.agents.visual_evidence import VisualEvidenceCandidate, VisualEvidenceResult
from app.db.models.company import Company
from app.db.session import async_session_factory, engine
from app.main import app
from app.services import visual_evidence


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
        "name": "视觉研究示例公司",
        "style_pack": {
            "name": "待审核风格草案",
            "supported_roles": ["llm_application_engineer"],
        },
        "rounds": [
            {
                "round_key": "round_1",
                "name": "技术一面",
                "sequence": 1,
                "duration_minutes": 45,
            }
        ],
    }


def png() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
        "Xb9M3QAAAABJRU5ErkJggg=="
    )


@pytest.mark.asyncio
async def test_visual_extract_returns_review_drafts_without_creating_evidence(monkeypatch) -> None:
    received: dict[str, object] = {}

    async def fake_analyse(_session, **kwargs):
        received.update(kwargs)
        return VisualEvidenceResult(
            candidates=(
                VisualEvidenceCandidate(
                    field_path="rounds.round_1.follow_up_patterns",
                    excerpt="围绕项目取舍和验证方式继续追问。",
                    confidence=0.74,
                ),
            ),
            allowed_field_paths=(
                "default_interviewer_behavior",
                "rounds.round_1.follow_up_patterns",
            ),
            warning_codes=("image_not_retained",),
        )

    monkeypatch.setattr(visual_evidence, "analyse_visual_evidence", fake_analyse)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/companies", json=company_payload())
        style_pack_id = created.json()["latest_style_pack"]["id"]
        response = await client.post(
            f"/api/style-packs/{style_pack_id}/evidence/visual-extract",
            data={
                "source_url": "https://example.com/interview-notes",
                "source_title": "匿名面试复盘页面",
                "source_confirmed": "true",
            },
            files={"image": ("notes.png", png(), "image/png")},
        )
        detail = await client.get(f"/api/companies/{created.json()['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["image_retained"] is False
    assert body["candidates"] == [
        {
            "field_path": "rounds.round_1.follow_up_patterns",
            "excerpt": "围绕项目取舍和验证方式继续追问。",
            "confidence": 0.74,
        }
    ]
    assert detail.json()["latest_style_pack"]["evidence_count"] == 0
    assert received["image"].content == png()


@pytest.mark.asyncio
async def test_visual_extract_requires_confirmation_and_safe_image_format() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/companies", json=company_payload())
        style_pack_id = created.json()["latest_style_pack"]["id"]
        unconfirmed = await client.post(
            f"/api/style-packs/{style_pack_id}/evidence/visual-extract",
            data={
                "source_url": "https://example.com/interview-notes",
                "source_title": "匿名面试复盘页面",
                "source_confirmed": "false",
            },
            files={"image": ("notes.png", png(), "image/png")},
        )
        unsupported = await client.post(
            f"/api/style-packs/{style_pack_id}/evidence/visual-extract",
            data={
                "source_url": "https://example.com/interview-notes",
                "source_title": "匿名面试复盘页面",
                "source_confirmed": "true",
            },
            files={"image": ("notes.svg", b"<svg></svg>", "image/svg+xml")},
        )

    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "visual_evidence_source_unconfirmed"
    assert unsupported.status_code == 415
    assert unsupported.json()["code"] == "visual_evidence_image_unsupported"


@pytest.mark.asyncio
async def test_visual_extract_rejects_an_active_style_pack_before_model_routing() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/companies", json=company_payload())
        style_pack_id = created.json()["latest_style_pack"]["id"]
        activated = await client.post(f"/api/style-packs/{style_pack_id}/activate")
        response = await client.post(
            f"/api/style-packs/{style_pack_id}/evidence/visual-extract",
            data={
                "source_url": "https://example.com/interview-notes",
                "source_title": "匿名面试复盘页面",
                "source_confirmed": "true",
            },
            files={"image": ("notes.png", png(), "image/png")},
        )

    assert activated.status_code == 200
    assert response.status_code == 409
    assert response.json()["code"] == "style_pack_immutable"
