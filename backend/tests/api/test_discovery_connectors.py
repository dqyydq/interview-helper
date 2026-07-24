import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.api.errors import AppError
from app.core.config import settings
from app.core.crypto import SecretCipher
from app.db.models.common import ConnectionStatus
from app.db.models.discovery import DiscoveryConnector
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.discovery.providers.base import DiscoveryProviderHealth
from app.main import app
from app.schemas.discovery import DiscoveryConnectorCreate
from app.services import discovery_connectors as service


class HealthyDiscoveryProvider:
    async def health_check(self) -> DiscoveryProviderHealth:
        return DiscoveryProviderHealth(status="healthy", latency_ms=11)

    async def aclose(self) -> None:
        return None


class DegradedDiscoveryProvider:
    async def health_check(self) -> DiscoveryProviderHealth:
        return DiscoveryProviderHealth(
            status="degraded",
            latency_ms=4,
            error_code="discovery_connector_rate_limited",
        )

    async def aclose(self) -> None:
        return None


async def clear_discovery_connectors() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(DiscoveryConnector))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_discovery_connectors():
    await clear_discovery_connectors()
    yield
    await clear_discovery_connectors()
    await engine.dispose()


def connector_payload(
    name: str = "Tavily primary",
    *,
    provider_type: str = "tavily",
    enabled: bool = True,
) -> dict:
    return {
        "name": name,
        "provider_type": provider_type,
        "api_key": "tavily-secret-value",
        "enabled": enabled,
        "configuration": {"default_country": "China"},
    }


@pytest.mark.asyncio
async def test_create_and_list_connectors_never_return_plaintext_api_keys() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/discovery-connectors", json=connector_payload())
        listed = await client.get("/api/discovery-connectors")

    assert created.status_code == 201
    public = created.json()
    assert public["has_api_key"] is True
    assert public["provider_type"] == "tavily"
    assert public["status"] == "untested"
    assert public["capabilities"] == {
        "supports_domain_filters": True,
        "supports_extract": True,
        "safe_extract": True,
    }
    assert public["configuration"] == {"default_country": "china"}
    response_text = created.text + listed.text
    assert "tavily-secret-value" not in response_text
    assert "encrypted_api_key" not in response_text

    async with async_session_factory() as session:
        connector = await session.get(DiscoveryConnector, public["id"])
    assert connector is not None
    assert connector.encrypted_api_key != "tavily-secret-value"
    assert SecretCipher(settings.encryption_secret).decrypt(connector.encrypted_api_key or "") == (
        "tavily-secret-value"
    )


@pytest.mark.asyncio
async def test_connector_rejects_user_controlled_endpoints_and_unknown_configuration() -> None:
    payload = connector_payload()
    payload["endpoint"] = "https://untrusted.example.test"
    payload["configuration"]["proxy_url"] = "https://untrusted.example.test"
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/discovery-connectors", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "tavily-secret-value" not in response.text


@pytest.mark.asyncio
async def test_update_rotates_secret_and_resets_health_without_exposing_it() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/discovery-connectors", json=connector_payload())
        connector_id = created.json()["id"]
        updated = await client.patch(
            f"/api/discovery-connectors/{connector_id}",
            json={
                "api_key": "rotated-secret-value",
                "configuration": {"default_country": "Japan"},
            },
        )

    assert updated.status_code == 200
    assert updated.json()["status"] == "untested"
    assert updated.json()["configuration"] == {"default_country": "japan"}
    assert updated.json()["configuration_version"] == 2
    assert "rotated-secret-value" not in updated.text

    async with async_session_factory() as session:
        connector = await session.get(DiscoveryConnector, connector_id)
    assert connector is not None
    assert SecretCipher(settings.encryption_secret).decrypt(connector.encrypted_api_key or "") == (
        "rotated-secret-value"
    )


@pytest.mark.asyncio
async def test_connector_test_persists_safe_health_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "build_search_provider",
        lambda _: HealthyDiscoveryProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/discovery-connectors", json=connector_payload())
        response = await client.post(f"/api/discovery-connectors/{created.json()['id']}/test")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "latency_ms": 11,
        "error_code": None,
    }
    async with async_session_factory() as session:
        connector = await session.get(DiscoveryConnector, created.json()["id"])
    assert connector is not None
    assert connector.status == ConnectionStatus.HEALTHY
    assert connector.last_tested_at is not None
    assert connector.last_error_summary is None


@pytest.mark.asyncio
async def test_failed_connector_test_exposes_only_a_stable_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "build_search_provider",
        lambda _: DegradedDiscoveryProvider(),
    )
    payload = connector_payload()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/discovery-connectors", json=payload)
        response = await client.post(f"/api/discovery-connectors/{created.json()['id']}/test")
        retrieved = await client.get(f"/api/discovery-connectors/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "latency_ms": 4,
        "error_code": "discovery_connector_rate_limited",
    }
    assert retrieved.json()["last_error_code"] == "discovery_connector_rate_limited"
    assert "tavily-secret-value" not in response.text + retrieved.text
    assert "last_error_summary" not in retrieved.text


@pytest.mark.asyncio
async def test_delete_clears_secret_soft_deletes_and_allows_name_reuse() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/discovery-connectors", json=connector_payload("Reusable"))
        connector_id = created.json()["id"]
        deleted = await client.delete(f"/api/discovery-connectors/{connector_id}")
        listed = await client.get("/api/discovery-connectors")
        recreated = await client.post(
            "/api/discovery-connectors",
            json=connector_payload("Reusable"),
        )

    assert deleted.status_code == 204
    assert listed.json() == []
    assert recreated.status_code == 201
    async with async_session_factory() as session:
        deleted_connector = await session.get(DiscoveryConnector, connector_id)
    assert deleted_connector is not None
    assert deleted_connector.deleted_at is not None
    assert deleted_connector.encrypted_api_key is None
    assert deleted_connector.status == ConnectionStatus.DISABLED


@pytest.mark.asyncio
async def test_connector_limit_is_scoped_per_provider_and_soft_delete_releases_a_slot() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        first = await client.post(
            "/api/discovery-connectors",
            json=connector_payload("Tavily one"),
        )
        second = await client.post(
            "/api/discovery-connectors",
            json=connector_payload("Tavily two"),
        )
        disabled = await client.post(
            "/api/discovery-connectors",
            json=connector_payload("Tavily disabled", enabled=False),
        )
        over_limit = await client.post(
            "/api/discovery-connectors",
            json=connector_payload("Tavily four"),
        )
        firecrawl = [
            await client.post(
                "/api/discovery-connectors",
                json=connector_payload(f"Firecrawl {index}", provider_type="firecrawl"),
            )
            for index in range(1, 4)
        ]
        firecrawl_over_limit = await client.post(
            "/api/discovery-connectors",
            json=connector_payload("Firecrawl four", provider_type="firecrawl"),
        )
        deleted = await client.delete(f"/api/discovery-connectors/{disabled.json()['id']}")
        replacement = await client.post(
            "/api/discovery-connectors",
            json=connector_payload("Tavily replacement"),
        )

    assert first.status_code == second.status_code == disabled.status_code == 201
    assert over_limit.status_code == 409
    assert over_limit.json()["code"] == "discovery_connector_provider_limit"
    assert all(response.status_code == 201 for response in firecrawl)
    assert all(response.json()["provider_type"] == "firecrawl" for response in firecrawl)
    assert firecrawl_over_limit.status_code == 409
    assert firecrawl_over_limit.json()["code"] == "discovery_connector_provider_limit"
    assert deleted.status_code == 204
    assert replacement.status_code == 201


@pytest.mark.asyncio
async def test_concurrent_connector_creates_never_exceed_provider_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Concurrent connector profile")
        session.add(profile)
        await session.commit()
        profile_id = profile.id

    original_lock = service._lock_profile_provider_connectors
    all_attempts_ready = asyncio.Event()
    arrived = 0

    async def synchronized_lock(session, locked_profile_id, provider_type):
        nonlocal arrived
        assert locked_profile_id == profile_id
        assert str(provider_type) == "tavily"
        arrived += 1
        if arrived == 4:
            all_attempts_ready.set()
        await all_attempts_ready.wait()
        await original_lock(session, locked_profile_id, provider_type)

    monkeypatch.setattr(service, "_lock_profile_provider_connectors", synchronized_lock)

    async def create_parallel_connector(index: int):
        payload = DiscoveryConnectorCreate.model_validate(
            connector_payload(f"Parallel {index}")
        )
        async with async_session_factory() as session:
            return await service.create_connector(session, profile_id, payload)

    results = await asyncio.wait_for(
        asyncio.gather(
            *(create_parallel_connector(index) for index in range(4)),
            return_exceptions=True,
        ),
        timeout=10,
    )

    successful = [result for result in results if isinstance(result, DiscoveryConnector)]
    failures = [result for result in results if isinstance(result, AppError)]
    assert len(successful) == 3
    assert len(failures) == 1
    assert failures[0].code == "discovery_connector_provider_limit"

    async with async_session_factory() as session:
        connector_count = await session.scalar(
            select(func.count())
            .select_from(DiscoveryConnector)
            .where(
                DiscoveryConnector.profile_id == profile_id,
                DiscoveryConnector.deleted_at.is_(None),
            )
        )
    assert connector_count == 3


@pytest.mark.asyncio
async def test_connector_lookup_is_profile_scoped() -> None:
    async with async_session_factory() as session:
        first_profile = UserProfile(display_name="First discovery profile")
        second_profile = UserProfile(display_name="Second discovery profile")
        session.add_all([first_profile, second_profile])
        await session.flush()
        connector = await service.create_connector(
            session,
            first_profile.id,
            DiscoveryConnectorCreate.model_validate(connector_payload()),
        )

        with pytest.raises(AppError) as error:
            await service.get_connector(session, second_profile.id, connector.id)

    assert error.value.code == "discovery_connector_not_found"
