import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db.models.question import QuestionBank, QuestionTag
from app.db.session import async_session_factory, engine
from app.main import app


async def clear_question_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(QuestionBank))
        await session.execute(delete(QuestionTag))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_question_data():
    await clear_question_data()
    yield
    await clear_question_data()
    await engine.dispose()


async def create_bank(client: AsyncClient, name: str = "LLM 应用开发") -> dict:
    response = await client.post(
        "/api/question-banks",
        json={"name": name, "description": "手工维护的个人题库"},
    )
    assert response.status_code == 201
    return response.json()


def question_payload(bank_id: str, prompt: str, **overrides) -> dict:
    payload = {
        "bank_id": bank_id,
        "prompt": prompt,
        "question_type": "project_deep_dive",
        "difficulty": "intermediate",
        "status": "active",
        "reference_points": ["目标", "方案", "取舍", "结果"],
        "follow_up_suggestions": ["为什么没有采用另一种方案？"],
        "applicable_companies": ["bytedance"],
        "applicable_rounds": ["round_2"],
        "source_note": "个人复盘",
        "tag_names": ["RAG", "项目深挖"],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_manual_question_crud_tags_variants_and_normalized_deduplication() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        bank = await create_bank(client)
        created = await client.post(
            "/api/questions",
            json=question_payload(bank["id"], "Ｅxplain   RAG architecture"),
        )
        assert created.status_code == 201
        question = created.json()
        assert {tag["name"] for tag in question["tags"]} == {"RAG", "项目深挖"}

        duplicate = await client.post(
            "/api/questions",
            json=question_payload(bank["id"], "explain rag architecture"),
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "question_duplicate"

        variant = await client.post(
            f"/api/questions/{question['id']}/variants",
            json={"prompt": "请画出 RAG 系统的数据流。", "variant_type": "scenario"},
        )
        assert variant.status_code == 201

        updated = await client.patch(
            f"/api/questions/{question['id']}",
            json={"difficulty": "advanced", "tag_names": ["RAG", "系统设计"]},
        )
        assert updated.status_code == 200
        assert updated.json()["difficulty"] == "advanced"
        assert len(updated.json()["variants"]) == 1
        assert {tag["name"] for tag in updated.json()["tags"]} == {"RAG", "系统设计"}

        bank_detail = await client.get(f"/api/question-banks/{bank['id']}")
        assert bank_detail.json()["question_count"] == 1


@pytest.mark.asyncio
async def test_question_filters_pagination_sort_and_bulk_archive() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        bank = await create_bank(client)
        first = await client.post(
            "/api/questions",
            json=question_payload(bank["id"], "如何评估 RAG 检索质量？"),
        )
        second = await client.post(
            "/api/questions",
            json=question_payload(
                bank["id"],
                "如何设计 Agent 的上下文压缩？",
                difficulty="advanced",
                tag_names=["Agent", "上下文工程"],
            ),
        )
        assert first.status_code == 201
        assert second.status_code == 201

        filtered = await client.get(
            "/api/questions",
            params={
                "bank_id": bank["id"],
                "difficulty": "advanced",
                "tag": "Agent",
                "search": "上下文",
                "sort_by": "updated_at",
                "sort_order": "asc",
                "offset": 0,
                "limit": 1,
            },
        )
        assert filtered.status_code == 200
        assert filtered.json()["count"] == 1
        assert len(filtered.json()["data"]) == 1
        assert filtered.json()["data"][0]["id"] == second.json()["id"]

        archived = await client.post(
            "/api/questions/bulk-archive",
            json={"question_ids": [first.json()["id"], second.json()["id"]]},
        )
        remaining = await client.get("/api/questions", params={"bank_id": bank["id"]})
        archived_list = await client.get(
            "/api/questions",
            params={"bank_id": bank["id"], "status": "archived"},
        )

    assert archived.status_code == 200
    assert archived.json() == {"updated": 2}
    assert remaining.json()["count"] == 0
    assert archived_list.json()["count"] == 2


@pytest.mark.asyncio
async def test_question_bank_archive_and_duplicate_name_contract() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        bank = await create_bank(client)
        duplicate = await client.post("/api/question-banks", json={"name": "llm 应用开发"})
        archived = await client.delete(f"/api/question-banks/{bank['id']}")
        visible = await client.get("/api/question-banks")
        including_archived = await client.get(
            "/api/question-banks", params={"include_archived": True}
        )

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "question_bank_duplicate"
    assert archived.status_code == 204
    assert visible.json() == []
    assert including_archived.json()[0]["archived"] is True
