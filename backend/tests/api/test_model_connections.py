from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.api.errors import AppError
from app.core.config import settings
from app.core.crypto import SecretCipher
from app.db.models.common import ConnectionStatus, ModelRole
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.main import app
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatRequest,
    ChatResponse,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
)
from app.schemas.model_connection import ModelConnectionCreate
from app.services import model_connections as service


class HealthyProvider(ChatProvider):
    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(content="ok")

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=7)

    async def aclose(self) -> None:
        return None


async def clear_model_settings() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(ModelRoleBinding))
        await session.execute(delete(ModelConnection))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_model_settings():
    await clear_model_settings()
    yield
    await clear_model_settings()
    await engine.dispose()


def connection_payload(name: str, provider_type: str = "openai_compatible") -> dict:
    return {
        "name": name,
        "provider_type": provider_type,
        "base_url": "https://models.example.test/v1",
        "api_key": f"secret-{name}",
        "model_name": f"model-{name}",
        "extra_headers": {"x-workspace": "private-tenant"},
        "context_window_tokens": 128000,
        "max_output_tokens": 4096,
        "tokenizer_type": "estimated",
        "supports_prompt_caching": True,
        "supports_token_count_endpoint": False,
    }


@pytest.mark.asyncio
async def test_create_and_list_connections_never_return_plaintext_secrets() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/model-connections", json=connection_payload("primary"))
        listed = await client.get("/api/model-connections")

    assert created.status_code == 201
    assert created.json()["has_api_key"] is True
    assert listed.status_code == 200
    response_text = created.text + listed.text
    assert "secret-primary" not in response_text
    assert "private-tenant" not in response_text
    assert "encrypted_api_key" not in response_text

    async with async_session_factory() as session:
        connection = await session.scalar(select(ModelConnection))
    assert connection is not None
    assert connection.encrypted_api_key != "secret-primary"
    assert SecretCipher(settings.encryption_secret).decrypt(connection.encrypted_api_key or "") == (
        "secret-primary"
    )


@pytest.mark.asyncio
async def test_two_provider_instances_can_be_tested_and_bound_to_required_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "build_provider", lambda _: HealthyProvider())
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        openai = await client.post("/api/model-connections", json=connection_payload("openai"))
        anthropic = await client.post(
            "/api/model-connections",
            json=connection_payload("anthropic", "anthropic_compatible"),
        )
        openai_id = openai.json()["id"]
        anthropic_id = anthropic.json()["id"]

        assert (await client.post(f"/api/model-connections/{openai_id}/test")).json() == {
            "status": "healthy",
            "latency_ms": 7,
            "error_code": None,
        }
        await client.post(f"/api/model-connections/{anthropic_id}/test")
        interviewer = await client.put(
            "/api/model-connections/roles/interviewer",
            json={"connection_id": openai_id},
        )
        evaluator = await client.put(
            "/api/model-connections/roles/evaluator",
            json={"connection_id": anthropic_id},
        )
        readiness = await client.get("/api/model-connections/readiness")

    assert interviewer.status_code == 200
    assert evaluator.status_code == 200
    assert readiness.json() == {"ready": True, "missing_roles": [], "degraded_roles": []}


@pytest.mark.asyncio
async def test_context_summarizer_falls_back_to_planner_binding() -> None:
    async with async_session_factory() as session:
        profile = await service.ensure_local_profile(session)
        connection = await service.create_connection(
            session,
            profile.id,
            ModelConnectionCreate.model_validate(connection_payload("planner")),
        )
        await service.bind_role(session, profile.id, ModelRole.PLANNER, connection)
        resolved = await service.resolve_role_connection(
            session,
            profile.id,
            ModelRole.CONTEXT_SUMMARIZER,
        )

    assert resolved.id == connection.id


@pytest.mark.asyncio
async def test_explicit_researcher_role_does_not_fall_back_to_interviewer_binding() -> None:
    async with async_session_factory() as session:
        profile = await service.ensure_local_profile(session)
        connection = await service.create_connection(
            session,
            profile.id,
            ModelConnectionCreate.model_validate(connection_payload("interviewer")),
        )
        await service.bind_role(session, profile.id, ModelRole.INTERVIEWER, connection)

        fallback_connection = await service.resolve_role_connection(
            session,
            profile.id,
            ModelRole.RESEARCHER,
        )
        with pytest.raises(AppError) as error:
            await service.resolve_explicit_role_connection(
                session,
                profile.id,
                ModelRole.RESEARCHER,
            )

    assert fallback_connection.id == connection.id
    assert error.value.code == "model_role_unbound"
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_explicit_researcher_role_resolves_its_enabled_connection() -> None:
    async with async_session_factory() as session:
        profile = await service.ensure_local_profile(session)
        connection = await service.create_connection(
            session,
            profile.id,
            ModelConnectionCreate.model_validate(connection_payload("researcher")),
        )
        connection.status = ConnectionStatus.HEALTHY
        await session.commit()
        await service.bind_role(session, profile.id, ModelRole.RESEARCHER, connection)

        resolved = await service.resolve_explicit_role_connection(
            session,
            profile.id,
            ModelRole.RESEARCHER,
        )

    assert resolved.id == connection.id
    assert resolved.status is ConnectionStatus.HEALTHY


@pytest.mark.asyncio
async def test_deleting_connection_removes_encrypted_secret_and_role_binding() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/api/model-connections", json=connection_payload("delete-me"))
        connection_id = created.json()["id"]
        await client.put(
            "/api/model-connections/roles/interviewer",
            json={"connection_id": connection_id},
        )
        deleted = await client.delete(f"/api/model-connections/{connection_id}")

    assert deleted.status_code == 204
    async with async_session_factory() as session:
        connection = await session.get(ModelConnection, connection_id)
        bindings = (await session.scalars(select(ModelRoleBinding))).all()
    assert connection is None
    assert bindings == []


@pytest.mark.asyncio
async def test_base_url_cannot_leak_inline_credentials() -> None:
    payload = connection_payload("unsafe")
    payload["base_url"] = "https://user:password@models.example.test/v1?key=secret"
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/model-connections", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.asyncio
async def test_local_capability_binding_has_no_fake_connection_or_api_key() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        bound = await client.put(
            "/api/model-connections/roles/transcriber",
            json={"local_capability_key": "sensevoice-small"},
        )
        bindings = await client.get("/api/model-connections/roles")
        invalid_role = await client.put(
            "/api/model-connections/roles/transcriber",
            json={"local_capability_key": "bge-m3"},
        )

    assert bound.status_code == 200
    assert bound.json()["target_kind"] == "local_capability"
    assert bound.json()["connection_id"] is None
    assert bound.json()["local_capability_key"] == "sensevoice-small"
    assert bindings.json()[0]["model_name"] == "sensevoice-small"
    assert invalid_role.status_code == 409
    assert invalid_role.json()["code"] == "local_capability_role_invalid"

    async with async_session_factory() as session:
        connections = (await session.scalars(select(ModelConnection))).all()
        binding = await session.scalar(select(ModelRoleBinding))
    assert connections == []
    assert binding is not None
    assert binding.connection_id is None
    assert binding.local_capability_key == "sensevoice-small"
