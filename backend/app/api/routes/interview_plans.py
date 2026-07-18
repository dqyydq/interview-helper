import uuid

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.schemas.interview_plan import (
    InterviewPlanCreate,
    InterviewPlanCreateResult,
    InterviewPlanPublic,
)
from app.services import interview_planning as service
from app.services.model_connections import ensure_local_profile

router = APIRouter(prefix="/interview-plans", tags=["interview-plans"])


@router.post("", response_model=InterviewPlanCreateResult, status_code=status.HTTP_202_ACCEPTED)
async def create_interview_plan(
    payload: InterviewPlanCreate,
    session: SessionDep,
) -> InterviewPlanCreateResult:
    profile = await ensure_local_profile(session)
    return await service.create_plan_job(session, profile.id, payload)


@router.get("/{plan_id}", response_model=InterviewPlanPublic)
async def get_interview_plan(plan_id: uuid.UUID, session: SessionDep) -> InterviewPlanPublic:
    profile = await ensure_local_profile(session)
    plan = await service.get_plan(session, profile.id, plan_id)
    return await service.plan_public(session, plan)
