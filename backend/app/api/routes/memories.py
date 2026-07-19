import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import SessionDep
from app.db.models.common import MemoryStatus, MemoryType
from app.schemas.memory import (
    ConflictResolveRequest,
    ForgetSessionResult,
    MemoryItemPublic,
    MemoryPinUpdate,
    MemoryPreviewPublic,
    MemorySettingsPublic,
    MemorySettingsUpdate,
    MemoryUpdate,
)
from app.services import memories as service
from app.services.model_connections import ensure_local_profile

router = APIRouter(tags=["memories"])


@router.get("/memories", response_model=list[MemoryItemPublic])
async def list_memories(
    session: SessionDep,
    status_filter: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    memory_type: MemoryType | None = None,
) -> list[MemoryItemPublic]:
    profile = await ensure_local_profile(session)
    return await service.list_memories(
        session,
        profile.id,
        status=status_filter,
        memory_type=memory_type,
    )


@router.patch("/memories/{memory_id}", response_model=MemoryItemPublic)
async def update_memory(
    memory_id: uuid.UUID, payload: MemoryUpdate, session: SessionDep
) -> MemoryItemPublic:
    profile = await ensure_local_profile(session)
    memory = await service.get_memory(session, profile.id, memory_id)
    memory = await service.update_memory(session, memory, payload)
    return await service.memory_public(session, memory)


@router.post("/memories/{memory_id}/confirm", response_model=MemoryItemPublic)
async def confirm_memory(memory_id: uuid.UUID, session: SessionDep) -> MemoryItemPublic:
    profile = await ensure_local_profile(session)
    memory = await service.get_memory(session, profile.id, memory_id)
    memory = await service.confirm_memory(session, memory, MemoryStatus.ACTIVE)
    return await service.memory_public(session, memory)


@router.patch("/memories/{memory_id}/pin", response_model=MemoryItemPublic)
async def pin_memory(
    memory_id: uuid.UUID, payload: MemoryPinUpdate, session: SessionDep
) -> MemoryItemPublic:
    profile = await ensure_local_profile(session)
    memory = await service.get_memory(session, profile.id, memory_id)
    memory = await service.pin_memory(session, memory, payload.pinned)
    return await service.memory_public(session, memory)


@router.post("/memories/{memory_id}/reject", response_model=MemoryItemPublic)
async def reject_memory(memory_id: uuid.UUID, session: SessionDep) -> MemoryItemPublic:
    profile = await ensure_local_profile(session)
    memory = await service.get_memory(session, profile.id, memory_id)
    memory = await service.confirm_memory(session, memory, MemoryStatus.REJECTED)
    return await service.memory_public(session, memory)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await ensure_local_profile(session)
    memory = await service.get_memory(session, profile.id, memory_id)
    await service.delete_memory(session, memory)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/memory-conflicts/{conflict_id}/resolve", response_model=MemoryItemPublic)
async def resolve_memory_conflict(
    conflict_id: uuid.UUID,
    payload: ConflictResolveRequest,
    session: SessionDep,
) -> MemoryItemPublic:
    profile = await ensure_local_profile(session)
    memory = await service.resolve_memory_conflict(
        session,
        profile_id=profile.id,
        conflict_id=conflict_id,
        winning_memory_id=payload.winning_memory_id,
    )
    return await service.memory_public(session, memory)


@router.get("/memory-settings", response_model=MemorySettingsPublic)
async def get_memory_settings(session: SessionDep) -> MemorySettingsPublic:
    profile = await ensure_local_profile(session)
    return await service.get_settings(profile)


@router.patch("/memory-settings", response_model=MemorySettingsPublic)
async def update_memory_settings(
    payload: MemorySettingsUpdate, session: SessionDep
) -> MemorySettingsPublic:
    profile = await ensure_local_profile(session)
    return await service.update_settings(
        session, profile, memory_enabled=payload.memory_enabled
    )


@router.get("/interview-plans/{plan_id}/memory-preview", response_model=MemoryPreviewPublic)
async def preview_plan_memory(plan_id: uuid.UUID, session: SessionDep) -> MemoryPreviewPublic:
    profile = await ensure_local_profile(session)
    return await service.preview_for_plan(session, profile, plan_id)


@router.post("/interviews/{session_id}/forget", response_model=ForgetSessionResult)
async def forget_interview_memory(
    session_id: uuid.UUID, session: SessionDep
) -> ForgetSessionResult:
    profile = await ensure_local_profile(session)
    return await service.forget_session(
        session,
        profile_id=profile.id,
        session_id=session_id,
    )
