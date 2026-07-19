import uuid
from datetime import timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.context.summarizer import summarize_segment
from app.db.models.common import JobStatus, JobType, SegmentStatus, utc_now
from app.db.models.context import ConversationSegment
from app.db.models.job import BackgroundJob
from app.db.session import async_session_factory
from app.providers.base import ProviderError

logger = structlog.get_logger(__name__)


async def claim_next_summary_job(session: AsyncSession, worker_id: str) -> uuid.UUID | None:
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == JobType.CONTEXT_SUMMARY,
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
        job.error_code = code
        job.error_message = message
        job.locked_at = None
        job.locked_by = None
        terminal = not retryable or job.attempts >= job.max_attempts
        if terminal:
            job.status = JobStatus.FAILED
            segment_id = job.payload.get("segment_id")
            try:
                parsed_segment_id = uuid.UUID(str(segment_id)) if segment_id else None
            except (TypeError, ValueError):
                parsed_segment_id = None
            segment = (
                await session.get(ConversationSegment, parsed_segment_id)
                if parsed_segment_id
                else None
            )
            if segment:
                segment.status = SegmentStatus.SUMMARY_FAILED
                segment.touch()
        else:
            job.status = JobStatus.QUEUED
            job.progress = 0
            job.available_at = utc_now() + timedelta(seconds=min(30, 2**job.attempts))
        job.touch()
        await session.commit()


async def process_summary_job(job_id: uuid.UUID) -> None:
    try:
        async with async_session_factory() as session:
            job = await session.get(BackgroundJob, job_id)
            if not job or job.status != JobStatus.RUNNING:
                return
            try:
                segment_id = uuid.UUID(str(job.payload["segment_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise AppError(
                    code="summary_job_payload_invalid",
                    message="摘要任务参数无效",
                    status_code=422,
                ) from exc
            summary = await summarize_segment(session, segment_id=segment_id)
            job.status = JobStatus.COMPLETED
            job.progress = 1
            job.result = {
                "segment_id": str(segment_id),
                "summary_id": str(summary.id),
                "summary_version": summary.summary_version,
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
            "context_summary_job_failed",
            job_id=str(job_id),
            error_type=type(exc).__name__,
        )
        await _fail_job(
            job_id,
            code="context_summary_internal_error",
            message="上下文摘要暂时失败，任务将自动重试",
            retryable=True,
        )


async def run_once(worker_id: str = "local-worker") -> bool:
    async with async_session_factory() as session:
        job_id = await claim_next_summary_job(session, worker_id)
    if not job_id:
        return False
    await process_summary_job(job_id)
    return True
