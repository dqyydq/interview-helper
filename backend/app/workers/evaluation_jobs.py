import uuid
from datetime import timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import EvaluationStatus, JobStatus, JobType, utc_now
from app.db.models.interview import InterviewSession
from app.db.models.job import BackgroundJob
from app.db.session import async_session_factory
from app.providers.base import ProviderError
from app.services.evaluation import ensure_report, mark_report_failed
from app.workers.handlers.evaluate_interview import handle_evaluate_interview

logger = structlog.get_logger(__name__)


async def claim_next_evaluation_job(
    session: AsyncSession,
    worker_id: str,
) -> uuid.UUID | None:
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == JobType.INTERVIEW_EVALUATION,
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
    job.result = {"phase": "loading_sources"}
    job.attempts += 1
    job.locked_at = utc_now()
    job.locked_by = worker_id
    job.error_code = None
    job.error_message = None
    job.touch()
    await session.commit()
    return job.id


async def _set_progress(job_id: uuid.UUID, progress: float, phase: str) -> None:
    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, job_id)
        if not job or job.status != JobStatus.RUNNING:
            return
        job.progress = progress
        job.result = {**job.result, "phase": phase}
        job.touch()
        await session.commit()


async def _fail_job(
    job_id: uuid.UUID,
    *,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, job_id)
        if not job:
            return
        interview = None
        try:
            interview = await session.get(
                InterviewSession,
                uuid.UUID(str(job.payload.get("session_id"))),
            )
        except (TypeError, ValueError):
            pass
        job.error_code = code
        job.error_message = message[:2_000]
        job.locked_at = None
        job.locked_by = None
        if retryable and job.attempts < job.max_attempts:
            job.status = JobStatus.QUEUED
            job.progress = 0
            job.result = {"phase": "retry_wait"}
            job.available_at = utc_now() + timedelta(seconds=min(30, 2**job.attempts))
            if interview:
                report = await ensure_report(session, interview)
                report.status = EvaluationStatus.PENDING
                report.failure_code = code
                report.touch()
        else:
            job.status = JobStatus.FAILED
            job.result = {"phase": "failed", "retriable_by_user": True}
            if interview:
                await mark_report_failed(session, interview, code=code)
        job.touch()
        await session.commit()


async def process_evaluation_job(job_id: uuid.UUID) -> None:
    try:
        await _set_progress(job_id, 0.25, "grouping_answers")
        async with async_session_factory() as session:
            job = await session.get(BackgroundJob, job_id)
            if not job or job.status != JobStatus.RUNNING:
                return
            try:
                session_id = uuid.UUID(str(job.payload["session_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise AppError(
                    code="evaluation_job_payload_invalid",
                    message="评估任务参数无效",
                ) from exc
            report = await handle_evaluate_interview(session, session_id=session_id)
        await _set_progress(job_id, 0.9, "persisting_report")
        async with async_session_factory() as session:
            job = await session.get(BackgroundJob, job_id)
            if not job or job.status != JobStatus.RUNNING:
                return
            job.status = JobStatus.COMPLETED
            job.progress = 1
            job.result = {
                "phase": "completed",
                "report_id": str(report.id),
                "session_id": str(report.session_id),
            }
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
    except ProviderError as exc:
        await _fail_job(
            job_id,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
    except Exception as exc:
        await logger.aexception(
            "evaluation_job_failed",
            job_id=str(job_id),
            error_type=type(exc).__name__,
        )
        await _fail_job(
            job_id,
            code="evaluation_internal_error",
            message="评估暂时失败，可稍后重试",
            retryable=True,
        )


async def run_once(worker_id: str = "local-worker") -> bool:
    async with async_session_factory() as session:
        job_id = await claim_next_evaluation_job(session, worker_id)
    if not job_id:
        return False
    await process_evaluation_job(job_id)
    return True
