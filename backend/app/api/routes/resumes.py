import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import anyio
from fastapi import APIRouter, File, Header, Request, Response, UploadFile, status
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import SessionDep
from app.core.config import settings
from app.db.models.common import JobStatus
from app.db.models.job import BackgroundJob
from app.db.session import async_session_factory
from app.schemas.resume import BackgroundJobPublic, ResumePublic, ResumeUploadResult
from app.services import resumes as service
from app.services.model_connections import ensure_local_profile

router = APIRouter(tags=["resumes"])
TERMINAL_JOB_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


@router.post(
    "/resumes",
    response_model=ResumeUploadResult,
    status_code=status.HTTP_201_CREATED,
)
async def upload_resume(
    session: SessionDep,
    file: Annotated[UploadFile, File()],
) -> ResumeUploadResult:
    profile = await ensure_local_profile(session)
    data = await file.read(settings.resume_upload_max_bytes + 1)
    await file.close()
    return await service.create_resume_upload(
        session,
        profile.id,
        filename=file.filename or "",
        mime_type=file.content_type or "application/octet-stream",
        data=data,
    )


@router.get("/resumes", response_model=list[ResumePublic])
async def list_resumes(session: SessionDep) -> list[ResumePublic]:
    profile = await ensure_local_profile(session)
    return await service.list_resumes(session, profile.id)


@router.get("/resumes/{resume_id}", response_model=ResumePublic)
async def get_resume(resume_id: uuid.UUID, session: SessionDep) -> ResumePublic:
    profile = await ensure_local_profile(session)
    resume = await service.get_resume(session, profile.id, resume_id)
    return await service.resume_public(session, resume)


@router.delete("/resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume(resume_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await ensure_local_profile(session)
    resume = await service.get_resume(session, profile.id, resume_id)
    await service.delete_resume(session, profile.id, resume)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/resumes/{resume_id}/parse",
    response_model=BackgroundJobPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_resume_parse(
    resume_id: uuid.UUID,
    session: SessionDep,
) -> BackgroundJobPublic:
    profile = await ensure_local_profile(session)
    resume = await service.get_resume(session, profile.id, resume_id)
    return await service.retry_resume_parse(session, profile.id, resume)


@router.get("/jobs/{job_id}", response_model=BackgroundJobPublic)
async def get_job(job_id: uuid.UUID, session: SessionDep) -> BackgroundJobPublic:
    profile = await ensure_local_profile(session)
    job = await service.get_job(session, profile.id, job_id)
    return service.job_public(job)


async def _job_events(
    request: Request,
    profile_id: uuid.UUID,
    job_id: uuid.UUID,
    last_version: int,
) -> AsyncIterator[ServerSentEvent]:
    cursor = last_version
    while True:
        if await request.is_disconnected():
            return
        async with async_session_factory() as session:
            job = await session.get(BackgroundJob, job_id)
            if not job or job.profile_id != profile_id or job.deleted_at is not None:
                return
            if job.version > cursor:
                public = service.job_public(job)
                cursor = job.version
                yield ServerSentEvent(
                    event="job",
                    id=str(job.version),
                    data=public.model_dump_json(),
                )
            if job.status in TERMINAL_JOB_STATUSES:
                return
        await anyio.sleep(settings.job_poll_interval_seconds)


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    profile = await ensure_local_profile(session)
    await service.get_job(session, profile.id, job_id)
    try:
        last_version = max(0, int(last_event_id or 0))
    except ValueError:
        last_version = 0
    return EventSourceResponse(
        _job_events(request, profile.id, job_id, last_version),
        ping=15,
        headers={"Cache-Control": "no-cache"},
    )
