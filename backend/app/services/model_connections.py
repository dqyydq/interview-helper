import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.core.crypto import SecretCipher
from app.db.models.common import ConnectionStatus, ModelRole, ProviderType
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.models.profile import UserProfile
from app.providers.factory import build_provider
from app.providers.types import ProviderHealthStatus
from app.schemas.model_connection import (
    ConnectionTestResult,
    ModelConnectionCreate,
    ModelConnectionPublic,
    ModelConnectionUpdate,
    ModelReadiness,
    RoleBindingPublic,
)


async def ensure_local_profile(session: AsyncSession) -> UserProfile:
    profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.deleted_at.is_(None))
        .order_by(UserProfile.created_at)
        .limit(1)
    )
    if profile:
        return profile
    profile = UserProfile()
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


def to_public(connection: ModelConnection) -> ModelConnectionPublic:
    return ModelConnectionPublic(
        id=connection.id,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
        version=connection.version,
        name=connection.name,
        provider_type=ProviderType(connection.provider_type),
        base_url=connection.base_url,
        model_name=connection.model_name,
        context_window_tokens=connection.context_window_tokens,
        max_output_tokens=connection.max_output_tokens,
        tokenizer_type=connection.tokenizer_type,
        supports_prompt_caching=connection.supports_prompt_caching,
        supports_token_count_endpoint=connection.supports_token_count_endpoint,
        status=ConnectionStatus(connection.status),
        has_api_key=bool(connection.encrypted_api_key),
    )


async def list_connections(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> Sequence[ModelConnection]:
    result = await session.scalars(
        select(ModelConnection)
        .where(
            ModelConnection.profile_id == profile_id,
            ModelConnection.deleted_at.is_(None),
        )
        .order_by(ModelConnection.created_at)
    )
    return result.all()


async def get_connection(
    session: AsyncSession,
    profile_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> ModelConnection:
    connection = await session.scalar(
        select(ModelConnection).where(
            ModelConnection.id == connection_id,
            ModelConnection.profile_id == profile_id,
            ModelConnection.deleted_at.is_(None),
        )
    )
    if not connection:
        raise AppError(code="model_connection_not_found", message="模型连接不存在", status_code=404)
    return connection


async def create_connection(
    session: AsyncSession,
    profile_id: uuid.UUID,
    payload: ModelConnectionCreate,
) -> ModelConnection:
    cipher = SecretCipher(settings.encryption_secret)
    data = payload.model_dump(
        exclude={"api_key", "extra_headers", "base_url"},
        mode="json",
    )
    connection = ModelConnection(
        **data,
        profile_id=profile_id,
        base_url=str(payload.base_url).rstrip("/"),
        encrypted_api_key=cipher.encrypt(payload.api_key),
        extra_headers_encrypted=cipher.encrypt_mapping(payload.extra_headers),
    )
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    return connection


async def update_connection(
    session: AsyncSession,
    connection: ModelConnection,
    payload: ModelConnectionUpdate,
) -> ModelConnection:
    cipher = SecretCipher(settings.encryption_secret)
    values = payload.model_dump(exclude_unset=True, exclude={"api_key", "extra_headers"})
    for key, value in values.items():
        if key == "base_url" and value is not None:
            value = str(value).rstrip("/")
        setattr(connection, key, value)
    if payload.api_key is not None:
        connection.encrypted_api_key = cipher.encrypt(payload.api_key)
    if payload.extra_headers is not None:
        connection.extra_headers_encrypted = cipher.encrypt_mapping(payload.extra_headers)
    connection.status = ConnectionStatus.UNTESTED
    connection.touch()
    await session.commit()
    await session.refresh(connection)
    return connection


async def delete_connection(session: AsyncSession, connection: ModelConnection) -> None:
    await session.execute(
        delete(ModelRoleBinding).where(ModelRoleBinding.connection_id == connection.id)
    )
    await session.delete(connection)
    await session.commit()


async def test_connection(
    session: AsyncSession,
    connection: ModelConnection,
) -> ConnectionTestResult:
    provider = build_provider(connection)
    try:
        health = await provider.health_check()
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()
    connection.status = (
        ConnectionStatus.HEALTHY
        if health.status is ProviderHealthStatus.HEALTHY
        else ConnectionStatus.DEGRADED
    )
    connection.touch()
    await session.commit()
    return ConnectionTestResult(
        status=connection.status,
        latency_ms=health.latency_ms,
        error_code=health.error_code,
    )


async def list_bindings(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> list[RoleBindingPublic]:
    rows = await session.execute(
        select(ModelRoleBinding, ModelConnection)
        .join(ModelConnection, ModelRoleBinding.connection_id == ModelConnection.id)
        .where(
            ModelRoleBinding.profile_id == profile_id,
            ModelRoleBinding.deleted_at.is_(None),
            ModelConnection.deleted_at.is_(None),
        )
        .order_by(ModelRoleBinding.role)
    )
    return [
        RoleBindingPublic(
            id=binding.id,
            created_at=binding.created_at,
            updated_at=binding.updated_at,
            version=binding.version,
            role=ModelRole(binding.role),
            connection_id=binding.connection_id,
            connection_name=connection.name,
            model_name=connection.model_name,
            connection_status=ConnectionStatus(connection.status),
        )
        for binding, connection in rows.all()
    ]


async def bind_role(
    session: AsyncSession,
    profile_id: uuid.UUID,
    role: ModelRole,
    connection: ModelConnection,
) -> ModelRoleBinding:
    if connection.status is ConnectionStatus.DISABLED:
        raise AppError(code="model_connection_disabled", message="不能绑定已停用的模型连接")
    binding = await session.scalar(
        select(ModelRoleBinding).where(
            ModelRoleBinding.profile_id == profile_id,
            ModelRoleBinding.role == role,
            ModelRoleBinding.deleted_at.is_(None),
        )
    )
    if binding:
        binding.connection_id = connection.id
        binding.touch()
    else:
        binding = ModelRoleBinding(
            profile_id=profile_id,
            role=role,
            connection_id=connection.id,
        )
        session.add(binding)
    await session.commit()
    await session.refresh(binding)
    return binding


async def model_readiness(session: AsyncSession, profile_id: uuid.UUID) -> ModelReadiness:
    bindings = {binding.role: binding for binding in await list_bindings(session, profile_id)}
    required = (ModelRole.INTERVIEWER, ModelRole.EVALUATOR)
    missing = [role for role in required if role not in bindings]
    degraded = [
        role
        for role in required
        if role in bindings and bindings[role].connection_status != ConnectionStatus.HEALTHY
    ]
    return ModelReadiness(
        ready=not missing and not degraded,
        missing_roles=missing,
        degraded_roles=degraded,
    )


async def resolve_role_connection(
    session: AsyncSession,
    profile_id: uuid.UUID,
    role: ModelRole,
) -> ModelConnection:
    fallback_roles = {
        ModelRole.CONTEXT_SUMMARIZER: [ModelRole.PLANNER, ModelRole.INTERVIEWER],
        ModelRole.PLANNER: [ModelRole.INTERVIEWER],
        ModelRole.RESEARCHER: [ModelRole.INTERVIEWER],
        ModelRole.COACH: [ModelRole.INTERVIEWER],
    }
    candidates = [role, *fallback_roles.get(role, [])]
    row = await session.execute(
        select(ModelRoleBinding, ModelConnection)
        .join(ModelConnection, ModelRoleBinding.connection_id == ModelConnection.id)
        .where(
            ModelRoleBinding.profile_id == profile_id,
            ModelRoleBinding.role.in_(candidates),
            ModelRoleBinding.deleted_at.is_(None),
            ModelConnection.deleted_at.is_(None),
            ModelConnection.status != ConnectionStatus.DISABLED,
        )
    )
    by_role = {binding.role: connection for binding, connection in row.all()}
    for candidate in candidates:
        if candidate in by_role:
            return by_role[candidate]
    raise AppError(
        code="model_role_unbound",
        message=f"尚未为 {role.value} 配置可用模型",
        status_code=409,
    )


async def resolve_explicit_role_connection(
    session: AsyncSession,
    profile_id: uuid.UUID,
    role: ModelRole,
) -> ModelConnection:
    """Resolve only the connection explicitly bound to ``role`` for a profile.

    This deliberately differs from :func:`resolve_role_connection`: callers with
    data-isolation requirements, such as external-source research, must opt into
    the exact configured model role and never inherit a more general fallback.
    """

    connection = await session.scalar(
        select(ModelConnection)
        .join(ModelRoleBinding, ModelRoleBinding.connection_id == ModelConnection.id)
        .where(
            ModelRoleBinding.profile_id == profile_id,
            ModelRoleBinding.role == role,
            ModelRoleBinding.deleted_at.is_(None),
            ModelConnection.profile_id == profile_id,
            ModelConnection.deleted_at.is_(None),
            ModelConnection.status != ConnectionStatus.DISABLED,
        )
    )
    if connection:
        return connection
    raise AppError(
        code="model_role_unbound",
        message=f"No usable model is explicitly configured for {role.value}.",
        status_code=409,
    )
