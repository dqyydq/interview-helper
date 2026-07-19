import uuid

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas.context import ContextDiagnosticsPublic
from app.services import interview_sessions as service
from app.services.model_connections import ensure_local_profile

router = APIRouter(prefix="/interview-sessions", tags=["context-diagnostics"])


@router.get("/{session_id}/context/diagnostics", response_model=ContextDiagnosticsPublic)
async def get_context_diagnostics(
    session_id: uuid.UUID,
    session: SessionDep,
) -> ContextDiagnosticsPublic:
    profile = await ensure_local_profile(session)
    interview = await service.get_session(session, profile.id, session_id)
    return await service.context_diagnostics(session, interview)
