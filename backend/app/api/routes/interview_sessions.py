import uuid

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.schemas.interview_session import (
    InterviewSessionCreate,
    InterviewSessionPublic,
    TrendInclusionUpdate,
)
from app.services import interview_sessions as service
from app.services.model_connections import ensure_local_profile

router = APIRouter(prefix="/interview-sessions", tags=["interview-sessions"])


@router.post("", response_model=InterviewSessionPublic, status_code=status.HTTP_201_CREATED)
async def create_interview_session(
    payload: InterviewSessionCreate,
    session: SessionDep,
) -> InterviewSessionPublic:
    profile = await ensure_local_profile(session)
    interview = await service.create_session(
        session,
        profile.id,
        payload.plan_id,
        excluded_memory_ids=payload.excluded_memory_ids,
    )
    return await service.session_public(session, interview)


@router.get("/{session_id}", response_model=InterviewSessionPublic)
async def get_interview_session(
    session_id: uuid.UUID,
    session: SessionDep,
) -> InterviewSessionPublic:
    profile = await ensure_local_profile(session)
    interview = await service.get_session(session, profile.id, session_id)
    return await service.session_public(session, interview)


@router.post("/{session_id}/start", response_model=InterviewSessionPublic)
async def start_interview_session(
    session_id: uuid.UUID,
    session: SessionDep,
) -> InterviewSessionPublic:
    profile = await ensure_local_profile(session)
    interview = await service.get_session(session, profile.id, session_id)
    interview = await service.start_session(session, interview)
    return await service.session_public(session, interview)


@router.post("/{session_id}/pause", response_model=InterviewSessionPublic)
async def pause_interview_session(
    session_id: uuid.UUID,
    session: SessionDep,
) -> InterviewSessionPublic:
    profile = await ensure_local_profile(session)
    interview = await service.get_session(session, profile.id, session_id)
    interview = await service.pause_session(session, interview)
    return await service.session_public(session, interview)


@router.post("/{session_id}/resume", response_model=InterviewSessionPublic)
async def resume_interview_session(
    session_id: uuid.UUID,
    session: SessionDep,
) -> InterviewSessionPublic:
    profile = await ensure_local_profile(session)
    interview = await service.get_session(session, profile.id, session_id)
    interview = await service.resume_session(session, interview)
    return await service.session_public(session, interview)


@router.post("/{session_id}/finish", response_model=InterviewSessionPublic)
async def finish_interview_session(
    session_id: uuid.UUID,
    session: SessionDep,
) -> InterviewSessionPublic:
    profile = await ensure_local_profile(session)
    interview = await service.get_session(session, profile.id, session_id)
    interview = await service.finish_session(session, interview)
    return await service.session_public(session, interview)


@router.patch("/{session_id}/trend-inclusion", response_model=InterviewSessionPublic)
async def update_session_trend_inclusion(
    session_id: uuid.UUID,
    payload: TrendInclusionUpdate,
    session: SessionDep,
) -> InterviewSessionPublic:
    profile = await ensure_local_profile(session)
    interview = await service.get_session(session, profile.id, session_id)
    interview = await service.update_trend_inclusion(
        session,
        interview,
        include_in_trends=payload.include_in_trends,
    )
    return await service.session_public(session, interview)
