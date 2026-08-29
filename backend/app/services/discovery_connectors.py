"""Profile-scoped, encrypted discovery connector management."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import asdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.core.crypto import SecretCipher, SecretDecryptionError
from app.db.models.common import ConnectionStatus, DiscoveryProviderType, utc_now
from app.db.models.discovery import DiscoveryConnector
from app.discovery.providers.base import (
    DiscoveryProviderError,
    DiscoveryProviderHealth,
    SearchProvider,
)
from app.discovery.providers.firecrawl import FirecrawlSearchProvider
from app.discovery.providers.tavily import TavilySearchProvider
from app.schemas.discovery import (
    DiscoveryConnectorCapabilities,
    DiscoveryConnectorConfiguration,
    DiscoveryConnectorCreate,
    DiscoveryConnectorPublic,
    DiscoveryConnectorTestResult,
    DiscoveryConnectorUpdate,
)

MAX_CONNECTORS_PER_PROVIDER = 3


def _provider_type(value: DiscoveryProviderType | str) -> DiscoveryProviderType:
    return DiscoveryProviderType(value)


def _provider_capabilities(provider_type: DiscoveryProviderType | str) -> dict[str, bool]:
    resolved_provider_type = _provider_type(provider_type)
    if resolved_provider_type is DiscoveryProviderType.TAVILY:
        return asdict(TavilySearchProvider.capabilities)
    if resolved_provider_type is DiscoveryProviderType.FIRECRAWL:
        return asdict(FirecrawlSearchProvider.capabilities)
    raise AppError(
        code="discovery_connector_provider_unsupported",
        message="当前版本不支持该题目发现连接器",
        status_code=422,
    )


def _profile_provider_connector_lock_key(
    profile_id: uuid.UUID,
    provider_type: DiscoveryProviderType | str,
) -> int:
    """Return a stable advisory-lock key for one profile/provider connector bucket."""

    provider = _provider_type(provider_type)
    digest = hashlib.sha256(f"discovery-connectors:{profile_id}:{provider.value}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _lock_profile_provider_connectors(
    session: AsyncSession,
    profile_id: uuid.UUID,
    provider_type: DiscoveryProviderType | str,
) -> None:
    """Serialize one provider's count/create/delete decisions until commit."""

    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                _profile_provider_connector_lock_key(profile_id, provider_type)
            )
        )
    )


async def _active_provider_connector_count(
    session: AsyncSession,
    profile_id: uuid.UUID,
    provider_type: DiscoveryProviderType | str,
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(DiscoveryConnector)
        .where(
            DiscoveryConnector.profile_id == profile_id,
            DiscoveryConnector.provider_type == _provider_type(provider_type),
            DiscoveryConnector.deleted_at.is_(None),
        )
    )
    return int(count or 0)


def _configuration_data(configuration: DiscoveryConnectorConfiguration) -> dict[str, str]:
    return configuration.model_dump(mode="json", exclude_none=True)


def _safe_error_summary(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    return "连接器测试未通过，请检查凭据、服务状态或稍后重试。"


def to_public(connector: DiscoveryConnector) -> DiscoveryConnectorPublic:
    """Build a public DTO without decrypting or serialising secret/error detail."""

    return DiscoveryConnectorPublic(
        id=connector.id,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
        version=connector.version,
        name=connector.name,
        provider_type=_provider_type(connector.provider_type),
        enabled=connector.enabled,
        capabilities=DiscoveryConnectorCapabilities.model_validate(connector.capabilities),
        configuration=DiscoveryConnectorConfiguration.model_validate(connector.configuration),
        configuration_version=connector.configuration_version,
        status=ConnectionStatus(connector.status),
        last_tested_at=connector.last_tested_at,
        last_error_code=connector.last_error_code,
        has_api_key=bool(connector.encrypted_api_key),
    )


async def list_connectors(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> Sequence[DiscoveryConnector]:
    rows = await session.scalars(
        select(DiscoveryConnector)
        .where(
            DiscoveryConnector.profile_id == profile_id,
            DiscoveryConnector.deleted_at.is_(None),
        )
        .order_by(DiscoveryConnector.created_at, DiscoveryConnector.id)
    )
    return rows.all()


async def get_connector(
    session: AsyncSession,
    profile_id: uuid.UUID,
    connector_id: uuid.UUID,
) -> DiscoveryConnector:
    connector = await session.scalar(
        select(DiscoveryConnector).where(
            DiscoveryConnector.id == connector_id,
            DiscoveryConnector.profile_id == profile_id,
            DiscoveryConnector.deleted_at.is_(None),
        )
    )
    if connector is None:
        raise AppError(
            code="discovery_connector_not_found",
            message="题目发现连接器不存在",
            status_code=404,
        )
    return connector


async def _active_name_exists(
    session: AsyncSession,
    profile_id: uuid.UUID,
    name: str,
    *,
    excluding_connector_id: uuid.UUID | None = None,
) -> bool:
    statement = select(DiscoveryConnector.id).where(
        DiscoveryConnector.profile_id == profile_id,
        DiscoveryConnector.deleted_at.is_(None),
        func.lower(DiscoveryConnector.name) == name.casefold(),
    )
    if excluding_connector_id is not None:
        statement = statement.where(DiscoveryConnector.id != excluding_connector_id)
    return await session.scalar(statement) is not None


async def create_connector(
    session: AsyncSession,
    profile_id: uuid.UUID,
    payload: DiscoveryConnectorCreate,
) -> DiscoveryConnector:
    provider_type = _provider_type(payload.provider_type)
    # Keep the count check and insert in one transaction.  A UI-only check would
    # allow simultaneous requests to exceed the user's three-Key provider budget.
    await _lock_profile_provider_connectors(session, profile_id, provider_type)
    if await _active_name_exists(session, profile_id, payload.name):
        raise AppError(
            code="discovery_connector_duplicate",
            message="已存在同名的题目发现连接器",
            status_code=409,
        )

    active_provider_count = await _active_provider_connector_count(
        session,
        profile_id,
        provider_type,
    )
    if active_provider_count >= MAX_CONNECTORS_PER_PROVIDER:
        raise AppError(
            code="discovery_connector_provider_limit",
            message="每种题目发现服务最多保留 3 个连接器，请删除不再使用的连接器后再添加。",
            status_code=409,
        )

    connector = DiscoveryConnector(
        profile_id=profile_id,
        name=payload.name,
        provider_type=provider_type,
        enabled=payload.enabled,
        capabilities=_provider_capabilities(provider_type),
        configuration=_configuration_data(payload.configuration),
        encrypted_api_key=SecretCipher(settings.encryption_secret).encrypt(payload.api_key),
        status=ConnectionStatus.UNTESTED if payload.enabled else ConnectionStatus.DISABLED,
    )
    session.add(connector)
    await session.commit()
    await session.refresh(connector)
    return connector


async def update_connector(
    session: AsyncSession,
    connector: DiscoveryConnector,
    payload: DiscoveryConnectorUpdate,
) -> DiscoveryConnector:
    if payload.name is not None and payload.name != connector.name:
        if await _active_name_exists(
            session,
            connector.profile_id,
            payload.name,
            excluding_connector_id=connector.id,
        ):
            raise AppError(
                code="discovery_connector_duplicate",
                message="已存在同名的题目发现连接器",
                status_code=409,
            )
        connector.name = payload.name

    credential_or_configuration_changed = False
    if payload.configuration is not None:
        new_configuration = _configuration_data(payload.configuration)
        if new_configuration != connector.configuration:
            connector.configuration = new_configuration
            credential_or_configuration_changed = True
    if payload.api_key is not None:
        connector.encrypted_api_key = SecretCipher(settings.encryption_secret).encrypt(
            payload.api_key
        )
        credential_or_configuration_changed = True

    if credential_or_configuration_changed:
        connector.configuration_version += 1

    if payload.enabled is not None and payload.enabled != connector.enabled:
        connector.enabled = payload.enabled
        connector.status = (
            ConnectionStatus.UNTESTED if payload.enabled else ConnectionStatus.DISABLED
        )
        connector.last_error_code = None
        connector.last_error_summary = None
    elif credential_or_configuration_changed and connector.enabled:
        connector.status = ConnectionStatus.UNTESTED
        connector.last_error_code = None
        connector.last_error_summary = None

    connector.touch()
    await session.commit()
    await session.refresh(connector)
    return connector


async def delete_connector(session: AsyncSession, connector: DiscoveryConnector) -> None:
    """Revoke the local credential before keeping a soft-deleted audit shell."""

    await _lock_profile_provider_connectors(
        session,
        connector.profile_id,
        connector.provider_type,
    )
    connector.encrypted_api_key = None
    connector.status = ConnectionStatus.DISABLED
    connector.last_error_code = None
    connector.last_error_summary = None
    connector.soft_delete()
    await session.commit()


def build_search_provider(connector: DiscoveryConnector) -> SearchProvider:
    """Construct a fixed-endpoint provider from an encrypted connector credential."""

    if not connector.encrypted_api_key:
        raise AppError(
            code="discovery_connector_missing_api_key",
            message="该题目发现连接器没有可用密钥",
            status_code=409,
        )
    try:
        api_key = SecretCipher(settings.encryption_secret).decrypt(connector.encrypted_api_key)
    except SecretDecryptionError as exc:
        raise AppError(
            code="discovery_connector_secret_unavailable",
            message="无法读取该题目发现连接器的本地密钥",
            status_code=409,
        ) from exc

    provider_type = _provider_type(connector.provider_type)
    if provider_type is DiscoveryProviderType.TAVILY:
        return TavilySearchProvider(
            api_key=api_key,
            timeout_seconds=settings.discovery_request_timeout_seconds,
            max_response_bytes=settings.discovery_max_response_bytes,
            max_source_characters=settings.discovery_max_source_characters,
        )
    if provider_type is DiscoveryProviderType.FIRECRAWL:
        return FirecrawlSearchProvider(
            api_key=api_key,
            timeout_seconds=settings.discovery_request_timeout_seconds,
            max_response_bytes=settings.discovery_max_response_bytes,
            max_source_characters=settings.discovery_max_source_characters,
        )
    raise AppError(
        code="discovery_connector_provider_unsupported",
        message="当前版本不支持该题目发现连接器",
        status_code=422,
    )


async def _record_test_result(
    session: AsyncSession,
    connector: DiscoveryConnector,
    health: DiscoveryProviderHealth,
) -> DiscoveryConnectorTestResult:
    connector.status = (
        ConnectionStatus.HEALTHY if health.status == "healthy" else ConnectionStatus.DEGRADED
    )
    connector.last_tested_at = utc_now()
    connector.last_error_code = health.error_code
    connector.last_error_summary = _safe_error_summary(health.error_code)
    connector.touch()
    await session.commit()
    await session.refresh(connector)
    return DiscoveryConnectorTestResult(
        status=ConnectionStatus(connector.status),
        latency_ms=health.latency_ms,
        error_code=health.error_code,
    )


async def test_connector(
    session: AsyncSession,
    connector: DiscoveryConnector,
) -> DiscoveryConnectorTestResult:
    """Check a connector and persist only the stable, safe health category."""

    try:
        provider = build_search_provider(connector)
    except AppError as exc:
        return await _record_test_result(
            session,
            connector,
            DiscoveryProviderHealth(status="degraded", latency_ms=0, error_code=exc.code),
        )

    try:
        health = await provider.health_check()
    except DiscoveryProviderError as exc:
        health = DiscoveryProviderHealth(
            status="degraded",
            latency_ms=0,
            error_code=exc.code,
        )
    except Exception:
        health = DiscoveryProviderHealth(
            status="degraded",
            latency_ms=0,
            error_code="discovery_connector_unavailable",
        )
    finally:
        await provider.aclose()
    return await _record_test_result(session, connector, health)
