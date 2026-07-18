import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_database_status
from app.main import app


def make_database_status_override(status: str):
    async def override() -> str:
        return status

    return override


@pytest.mark.asyncio
async def test_health_is_healthy_when_database_is_connected() -> None:
    app.dependency_overrides[get_database_status] = make_database_status_override("connected")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/health")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "version": "0.1.0",
        "database": "connected",
    }


@pytest.mark.asyncio
async def test_health_degrades_when_database_is_unavailable() -> None:
    app.dependency_overrides[get_database_status] = make_database_status_override("unavailable")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/health")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "version": "0.1.0",
        "database": "unavailable",
    }
