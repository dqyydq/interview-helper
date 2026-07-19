import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db.models.common import MemoryType
from app.db.models.memory import MemoryConflict, MemoryItem, MemorySource, MemoryUsage
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.main import app
from app.memory.types import MemoryCandidate, MemorySourceInput
from app.memory.writer import remember


async def _clear_memory_api_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(MemoryUsage))
        await session.execute(delete(MemoryConflict))
        await session.execute(delete(MemorySource))
        await session.execute(delete(MemoryItem))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_memory_api_data():
    await _clear_memory_api_data()
    yield
    await _clear_memory_api_data()
    await engine.dispose()


@pytest.mark.asyncio
async def test_memory_api_controls_lifecycle_and_global_switch() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="API 记忆测试")
        session.add(profile)
        await session.commit()
        proposed = await remember(
            session,
            profile_id=profile.id,
            candidate=MemoryCandidate(
                memory_type=MemoryType.STABLE_SKILL,
                canonical_key="skill.context",
                content="上下文工程能力稳定",
                source=MemorySourceInput(source_type="user_manual"),
            ),
        )

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        listed = await client.get("/api/memories", params={"status": "proposed"})
        confirmed = await client.post(f"/api/memories/{proposed.id}/confirm")
        pinned = await client.patch(
            f"/api/memories/{proposed.id}/pin", json={"pinned": True}
        )
        edited = await client.patch(
            f"/api/memories/{proposed.id}",
            json={"content": "上下文工程与证据追踪能力稳定"},
        )
        disabled = await client.patch(
            "/api/memory-settings", json={"memory_enabled": False}
        )
        rejected = await client.post(f"/api/memories/{proposed.id}/reject")

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [str(proposed.id)]
    assert confirmed.json()["status"] == "active"
    assert pinned.json()["pinned"] is True
    assert edited.json()["content"] == "上下文工程与证据追踪能力稳定"
    assert edited.json()["sources"][0]["source_type"] == "user_manual"
    assert disabled.json() == {"memory_enabled": False}
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["pinned"] is False


@pytest.mark.asyncio
async def test_memory_api_delete_and_not_found_are_scoped() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        await client.get("/api/memory-settings")
        missing = await client.patch(
            f"/api/memories/{uuid.uuid4()}", json={"content": "不存在"}
        )

    assert missing.status_code == 404
    assert missing.json()["code"] == "memory_not_found"
