import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.core.crypto import SecretCipher
from app.db.models.common import ConnectionStatus, ModelRole, ProviderType
from app.db.models.embedding import EmbeddingProfile
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.models.profile import UserProfile
from app.local_ai.capabilities import LocalCapabilityDefinition, get_local_capability
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
    embedding_profile_id = await session.scalar(
        select(EmbeddingProfile.id)
        .where(
            EmbeddingProfile.model_connection_id == connection.id,
        )
        .limit(1)
    )
    if embedding_profile_id is not None:
        # The index stores an immutable, auditable vector-space snapshot.  A
        # raw FK failure would be opaque to users and could tempt a caller to
        # delete history out-of-band, so make this retention boundary explicit.
        raise AppError(
            code="model_connection_embedding_history_exists",
            message=(
                "该模型连接仍被向量索引历史引用，无法删除；请先解除角色绑定，"
                "再清除密钥并停用。索引历史会保留。"
            ),
            status_code=409,
        )
    await session.execute(
        delete(ModelRoleBinding).where(ModelRoleBinding.connection_id == connection.id)
    )
    await session.delete(connection)
    await session.commit()


async def redact_connection_credentials(
    session: AsyncSession,
    connection: ModelConnection,
) -> ModelConnection:
    """Remove stored credentials while retaining immutable index provenance.

    Embedding profiles intentionally retain a foreign key to the connection so
    a served vector space remains auditable.  That must never trap a retired
    cloud credential: after the user moves every Agent role elsewhere, remove
    the encrypted key and headers and disable the retained connection row.
    """

    active_binding_id = await session.scalar(
        select(ModelRoleBinding.id)
        .where(
            ModelRoleBinding.connection_id == connection.id,
            ModelRoleBinding.deleted_at.is_(None),
        )
        .limit(1)
    )
    if active_binding_id is not None:
        raise AppError(
            code="model_connection_still_bound",
            message="请先解除该模型连接的所有 Agent 角色绑定，再清除密钥并停用。",
            status_code=409,
        )
    connection.encrypted_api_key = None
    connection.extra_headers_encrypted = {}
    connection.status = ConnectionStatus.DISABLED
    connection.touch()
    await session.commit()
    await session.refresh(connection)
    return connection


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
        .outerjoin(ModelConnection, ModelRoleBinding.connection_id == ModelConnection.id)
        .where(
            ModelRoleBinding.profile_id == profile_id,
            ModelRoleBinding.deleted_at.is_(None),
            ModelConnection.deleted_at.is_(None),
        )
        .order_by(ModelRoleBinding.role)
    )
    bindings: list[RoleBindingPublic] = []
    for binding, connection in rows.all():
        if binding.local_capability_key:
            capability = get_local_capability(binding.local_capability_key)
            if capability is None:  # Defensive guard for manually damaged local data.
                continue
            bindings.append(
                RoleBindingPublic(
                    id=binding.id,
                    created_at=binding.created_at,
                    updated_at=binding.updated_at,
                    version=binding.version,
                    role=ModelRole(binding.role),
                    target_kind="local_capability",
                    connection_id=None,
                    connection_name=None,
                    model_name=capability.model_name,
                    connection_status=None,
                    local_capability_key=capability.key,
                )
            )
            continue
        if connection is None:  # Defensive guard for a dangling legacy binding.
            continue
        bindings.append(
            RoleBindingPublic(
                id=binding.id,
                created_at=binding.created_at,
                updated_at=binding.updated_at,
                version=binding.version,
                role=ModelRole(binding.role),
                target_kind="model_connection",
                connection_id=connection.id,
                connection_name=connection.name,
                model_name=connection.model_name,
                connection_status=ConnectionStatus(connection.status),
                local_capability_key=None,
            )
        )
    return bindings


async def bind_role(
    session: AsyncSession,
    profile_id: uuid.UUID,
    role: ModelRole,
    connection: ModelConnection | None = None,
    local_capability_key: str | None = None,
) -> ModelRoleBinding:
    if (connection is None) == (local_capability_key is None):
        raise AppError(
            code="role_target_invalid",
            message="必须且只能选择一个模型连接或本地能力",
        )
    capability: LocalCapabilityDefinition | None = None
    if local_capability_key is not None:
        capability = get_local_capability(local_capability_key)
        if capability is None:
            raise AppError(
                code="local_capability_not_found",
                message="本地能力不存在",
                status_code=404,
            )
        if capability.role != role:
            raise AppError(
                code="local_capability_role_invalid",
                message="该本地能力不能绑定到此 Agent 角色",
                status_code=409,
            )
    elif connection is not None and connection.status is ConnectionStatus.DISABLED:
        raise AppError(code="model_connection_disabled", message="不能绑定已停用的模型连接")
    binding = await session.scalar(
        select(ModelRoleBinding).where(
            ModelRoleBinding.profile_id == profile_id,
            ModelRoleBinding.role == role,
            ModelRoleBinding.deleted_at.is_(None),
        )
    )
    if binding:
        binding.connection_id = connection.id if connection is not None else None
        binding.local_capability_key = capability.key if capability is not None else None
        binding.touch()
    else:
        binding = ModelRoleBinding(
            profile_id=profile_id,
            role=role,
            connection_id=connection.id if connection is not None else None,
            local_capability_key=capability.key if capability is not None else None,
        )
        session.add(binding)
    await session.commit()
    await session.refresh(binding)
    return binding


async def unbind_role(
    session: AsyncSession,
    profile_id: uuid.UUID,
    role: ModelRole,
) -> bool:
    """Remove one explicit role target without affecting the underlying connection.

    A role binding is routing metadata, not user-owned model configuration.  In
    particular, this lets a Docker-only user safely clear a local capability
    before switching runtimes or rolling back its migration.
    """

    result = await session.execute(
        delete(ModelRoleBinding).where(
            ModelRoleBinding.profile_id == profile_id,
            ModelRoleBinding.role == role,
            ModelRoleBinding.deleted_at.is_(None),
        )
    )
    await session.commit()
    return bool(result.rowcount)


async def resolve_local_or_connection_target(
    session: AsyncSession,
    profile_id: uuid.UUID,
    role: ModelRole,
) -> ModelConnection | LocalCapabilityDefinition:
    """Resolve an exact transcription/embedding binding without chat fallback."""

    if role not in {ModelRole.TRANSCRIBER, ModelRole.EMBEDDING}:
        raise ValueError("role must be transcription or embedding")
    binding = await session.scalar(
        select(ModelRoleBinding).where(
            ModelRoleBinding.profile_id == profile_id,
            ModelRoleBinding.role == role,
            ModelRoleBinding.deleted_at.is_(None),
        )
    )
    if binding is None:
        raise AppError(
            code="model_role_unbound",
            message=f"尚未为 {role.value} 配置可用模型",
            status_code=409,
        )
    if binding.local_capability_key:
        capability = get_local_capability(binding.local_capability_key)
        if capability is not None and capability.role == role:
            return capability
        raise AppError(
            code="local_capability_invalid",
            message="本地能力配置无效",
            status_code=409,
        )
    if binding.connection_id is not None:
        connection = await session.scalar(
            select(ModelConnection).where(
                ModelConnection.id == binding.connection_id,
                ModelConnection.profile_id == profile_id,
                ModelConnection.deleted_at.is_(None),
                ModelConnection.status != ConnectionStatus.DISABLED,
            )
        )
        if connection is not None:
            return connection
    raise AppError(
        code="model_role_unbound",
        message=f"尚未为 {role.value} 配置可用模型",
        status_code=409,
    )


async def resolve_transcription_target(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> ModelConnection | LocalCapabilityDefinition:
    return await resolve_local_or_connection_target(session, profile_id, ModelRole.TRANSCRIBER)


async def resolve_embedding_target(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> ModelConnection | LocalCapabilityDefinition:
    return await resolve_local_or_connection_target(session, profile_id, ModelRole.EMBEDDING)


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
