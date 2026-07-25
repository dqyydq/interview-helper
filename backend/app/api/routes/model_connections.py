import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import SessionDep
from app.db.models.common import ModelRole
from app.schemas.model_connection import (
    ConnectionTestResult,
    ModelConnectionCreate,
    ModelConnectionPublic,
    ModelConnectionUpdate,
    ModelReadiness,
    RoleBindingPublic,
    RoleBindingUpdate,
)
from app.services import model_connections as service

router = APIRouter(prefix="/model-connections", tags=["model-connections"])


@router.get("", response_model=list[ModelConnectionPublic])
async def list_model_connections(session: SessionDep) -> list[ModelConnectionPublic]:
    profile = await service.ensure_local_profile(session)
    connections = await service.list_connections(session, profile.id)
    return [service.to_public(connection) for connection in connections]


@router.post("", response_model=ModelConnectionPublic, status_code=status.HTTP_201_CREATED)
async def create_model_connection(
    payload: ModelConnectionCreate,
    session: SessionDep,
) -> ModelConnectionPublic:
    profile = await service.ensure_local_profile(session)
    connection = await service.create_connection(session, profile.id, payload)
    return service.to_public(connection)


@router.get("/roles", response_model=list[RoleBindingPublic])
async def list_role_bindings(session: SessionDep) -> list[RoleBindingPublic]:
    profile = await service.ensure_local_profile(session)
    return await service.list_bindings(session, profile.id)


@router.put("/roles/{role}", response_model=RoleBindingPublic)
async def update_role_binding(
    role: ModelRole,
    payload: RoleBindingUpdate,
    session: SessionDep,
) -> RoleBindingPublic:
    profile = await service.ensure_local_profile(session)
    if payload.connection_id is not None:
        connection = await service.get_connection(session, profile.id, payload.connection_id)
        binding = await service.bind_role(session, profile.id, role, connection=connection)
    else:
        binding = await service.bind_role(
            session,
            profile.id,
            role,
            local_capability_key=payload.local_capability_key,
        )
    bindings = await service.list_bindings(session, profile.id)
    return next(item for item in bindings if item.id == binding.id)


@router.delete("/roles/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role_binding(role: ModelRole, session: SessionDep) -> Response:
    """Clear an explicit role target while retaining any saved model connection."""

    profile = await service.ensure_local_profile(session)
    await service.unbind_role(session, profile.id, role)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/readiness", response_model=ModelReadiness)
async def get_model_readiness(session: SessionDep) -> ModelReadiness:
    profile = await service.ensure_local_profile(session)
    return await service.model_readiness(session, profile.id)


@router.get("/{connection_id}", response_model=ModelConnectionPublic)
async def get_model_connection(
    connection_id: uuid.UUID,
    session: SessionDep,
) -> ModelConnectionPublic:
    profile = await service.ensure_local_profile(session)
    connection = await service.get_connection(session, profile.id, connection_id)
    return service.to_public(connection)


@router.patch("/{connection_id}", response_model=ModelConnectionPublic)
async def update_model_connection(
    connection_id: uuid.UUID,
    payload: ModelConnectionUpdate,
    session: SessionDep,
) -> ModelConnectionPublic:
    profile = await service.ensure_local_profile(session)
    connection = await service.get_connection(session, profile.id, connection_id)
    connection = await service.update_connection(session, connection, payload)
    return service.to_public(connection)


@router.post("/{connection_id}/redact-credentials", response_model=ModelConnectionPublic)
async def redact_model_connection_credentials(
    connection_id: uuid.UUID,
    session: SessionDep,
) -> ModelConnectionPublic:
    """Permanently remove an unused connection's stored API credential."""

    profile = await service.ensure_local_profile(session)
    connection = await service.get_connection(session, profile.id, connection_id)
    connection = await service.redact_connection_credentials(session, connection)
    return service.to_public(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_connection(connection_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await service.ensure_local_profile(session)
    connection = await service.get_connection(session, profile.id, connection_id)
    await service.delete_connection(session, connection)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
async def test_model_connection(
    connection_id: uuid.UUID,
    session: SessionDep,
) -> ConnectionTestResult:
    profile = await service.ensure_local_profile(session)
    connection = await service.get_connection(session, profile.id, connection_id)
    return await service.test_connection(session, connection)
