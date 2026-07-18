import uuid
from datetime import timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import JobStatus, JobType, utc_now
from app.db.models.interview import InterviewPlan
from app.db.models.job import BackgroundJob
from app.db.session import async_session_factory
from app.services.interview_planning import generate_plan

logger = structlog.get_logger(__name__)


async def claim_next_plan_job(session: AsyncSession, worker_id: str) -> uuid.UUID | None:
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == JobType.PLAN_GENERATION,
            BackgroundJob.status == JobStatus.QUEUED,
            BackgroundJob.available_at <= utc_now(),
            BackgroundJob.deleted_at.is_(None),
        )
        .order_by(BackgroundJob.available_at, BackgroundJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not job:
        return None
    job.status = JobStatus.RUNNING
    job.progress = 0.1
    job.attempts += 1
    job.locked_at = utc_now()
    job.locked_by = worker_id
    job.error_code = None
    job.error_message = None
    job.touch()
    await session.commit()
    return job.id


async def _fail_job(job_id: uuid.UUID, *, code: str, message: str, retryable: bool) -> None:
    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, job_id)
        if not job:
            return
        plan_id = job.payload.get("plan_id")
        plan = await session.get(InterviewPlan, uuid.UUID(plan_id)) if plan_id else None
        job.error_code = code
        job.error_message = message
        job.locked_at = None
        job.locked_by = None
        if retryable and job.attempts < job.max_attempts:
            job.status = JobStatus.QUEUED
            job.progress = 0
            job.available_at = utc_now() + timedelta(seconds=min(30, 2**job.attempts))
        else:
            job.status = JobStatus.FAILED
            if plan:
                plan.plan_snapshot = {**plan.plan_snapshot, "phase": "failed", "error_code": code}
                plan.touch()
        job.touch()
        await session.commit()


async def process_plan_job(job_id: uuid.UUID) -> None:
    try:
        async with async_session_factory() as session:
            job = await session.get(BackgroundJob, job_id)
            if not job or job.status != JobStatus.RUNNING:
                return
            try:
                plan_id = uuid.UUID(str(job.payload["plan_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise AppError(code="plan_job_payload_invalid", message="规划任务参数无效") from exc
            plan = await generate_plan(session, plan_id)
            job.status = JobStatus.COMPLETED
            job.progress = 1
            job.result = {"plan_id": str(plan.id), "plan_version": plan.version}
            job.locked_at = None
            job.locked_by = None
            job.touch()
            await session.commit()
    except AppError as exc:
        await _fail_job(
            job_id,
            code=exc.code,
            message=exc.message,
            retryable=exc.status_code >= 500,
        )
    except Exception as exc:
        await logger.aexception(
            "plan_job_failed",
            job_id=str(job_id),
            error_type=type(exc).__name__,
        )
        await _fail_job(
            job_id,
            code="plan_generation_internal_error",
            message="面试计划生成暂时失败，任务将自动重试",
            retryable=True,
        )


async def run_once(worker_id: str = "local-worker") -> bool:
    async with async_session_factory() as session:
        job_id = await claim_next_plan_job(session, worker_id)
    if not job_id:
        return False
    await process_plan_job(job_id)
    return True
