import uuid

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.db.models.common import PracticeTaskStatus
from app.schemas.practice import PracticeTaskPublic, PracticeTaskUpdate
from app.services import practice_tasks as service
from app.services.model_connections import ensure_local_profile

router = APIRouter(prefix="/practice-tasks", tags=["practice-tasks"])


@router.get("", response_model=list[PracticeTaskPublic])
async def list_practice_tasks(
    session: SessionDep,
    status: PracticeTaskStatus | None = None,
) -> list[PracticeTaskPublic]:
    profile = await ensure_local_profile(session)
    tasks = await service.list_practice_tasks(session, profile.id, status=status)
    return [service.task_public(task) for task in tasks]


@router.get("/{task_id}", response_model=PracticeTaskPublic)
async def get_practice_task(task_id: uuid.UUID, session: SessionDep) -> PracticeTaskPublic:
    profile = await ensure_local_profile(session)
    task = await service.get_practice_task(session, profile.id, task_id)
    return service.task_public(task)


@router.patch("/{task_id}", response_model=PracticeTaskPublic)
async def update_practice_task(
    task_id: uuid.UUID,
    payload: PracticeTaskUpdate,
    session: SessionDep,
) -> PracticeTaskPublic:
    profile = await ensure_local_profile(session)
    task = await service.get_practice_task(session, profile.id, task_id)
    task = await service.update_practice_task(session, task, payload)
    return service.task_public(task)
