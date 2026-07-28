import uuid

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.schemas.evaluation import (
    EvaluationJobPublic,
    EvaluationReportPublic,
    ReportListItem,
)
from app.schemas.practice import PracticeTaskCreateFromReport, PracticeTaskPublic
from app.services import evaluation as service
from app.services import practice_tasks as practice_task_service
from app.services.model_connections import ensure_local_profile

router = APIRouter(tags=["reports"])


@router.get("/reports", response_model=list[ReportListItem])
async def list_reports(session: SessionDep) -> list[ReportListItem]:
    profile = await ensure_local_profile(session)
    return await service.list_reports(session, profile.id)


@router.get("/reports/{report_id}", response_model=EvaluationReportPublic)
async def get_report(
    report_id: uuid.UUID,
    session: SessionDep,
) -> EvaluationReportPublic:
    profile = await ensure_local_profile(session)
    report = await service.get_report(session, profile.id, report_id)
    return await service.report_public(session, report)


@router.get(
    "/interview-sessions/{session_id}/report",
    response_model=EvaluationReportPublic,
)
async def get_session_report(
    session_id: uuid.UUID,
    session: SessionDep,
) -> EvaluationReportPublic:
    profile = await ensure_local_profile(session)
    report = await service.get_report_for_session(session, profile.id, session_id)
    return await service.report_public(session, report)


@router.post(
    "/reports/{report_id}/retry",
    response_model=EvaluationJobPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_report(
    report_id: uuid.UUID,
    session: SessionDep,
) -> EvaluationJobPublic:
    profile = await ensure_local_profile(session)
    report = await service.get_report(session, profile.id, report_id)
    job = await service.retry_evaluation(session, report)
    return EvaluationJobPublic.model_validate(job)


@router.post(
    "/reports/{report_id}/practice-tasks",
    response_model=list[PracticeTaskPublic],
    status_code=status.HTTP_200_OK,
)
async def create_report_practice_tasks(
    report_id: uuid.UUID,
    payload: PracticeTaskCreateFromReport,
    session: SessionDep,
) -> list[PracticeTaskPublic]:
    profile = await ensure_local_profile(session)
    tasks = await practice_task_service.create_tasks_from_report(
        session,
        profile.id,
        report_id,
        payload,
    )
    return [practice_task_service.task_public(task) for task in tasks]
