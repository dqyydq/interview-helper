import uuid

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas.interview_readiness import InterviewReadinessPublic
from app.services.interview_readiness import get_interview_readiness

router = APIRouter(tags=["interview-readiness"])


@router.get("/interview-readiness", response_model=InterviewReadinessPublic)
async def get_readiness(
    session: SessionDep,
    company_id: uuid.UUID | None = None,
    round_profile_id: uuid.UUID | None = None,
) -> InterviewReadinessPublic:
    """Return only the setup state needed before a local interview begins."""

    return await get_interview_readiness(
        session,
        company_id=company_id,
        round_profile_id=round_profile_id,
    )
