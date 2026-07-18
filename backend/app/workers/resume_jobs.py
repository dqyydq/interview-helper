import uuid
from datetime import timedelta
from pathlib import Path

import anyio
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.common import JobStatus, JobType, ResumeParseStatus, utc_now
from app.db.models.job import BackgroundJob
from app.db.models.resume import Resume, ResumeClaim, ResumeSection
from app.db.session import async_session_factory
from app.services.resume_parser import (
    ResumeParseError,
    extract_resume_claims,
    extract_resume_text,
    split_resume_sections,
)

logger = structlog.get_logger(__name__)


async def claim_next_resume_job(session: AsyncSession, worker_id: str) -> uuid.UUID | None:
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == JobType.RESUME_PARSE,
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
    job.progress = 0.05
    job.attempts += 1
    job.locked_at = utc_now()
    job.locked_by = worker_id
    job.error_code = None
    job.error_message = None
    job.touch()
    await session.commit()
    return job.id


async def _fail_job(
    job_id: uuid.UUID,
    resume_id: uuid.UUID | None,
    *,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, job_id)
        resume = await session.get(Resume, resume_id) if resume_id else None
        if not job:
            return
        job.error_code = code
        job.error_message = message
        job.locked_at = None
        job.locked_by = None
        if retryable and job.attempts < job.max_attempts:
            job.status = JobStatus.QUEUED
            job.progress = 0.0
            job.available_at = utc_now() + timedelta(seconds=min(30, 2**job.attempts))
            if resume:
                resume.parse_status = ResumeParseStatus.PENDING
        else:
            job.status = JobStatus.FAILED
            if resume:
                resume.parse_status = ResumeParseStatus.FAILED
                resume.parse_error_code = code
        job.touch()
        if resume:
            resume.touch()
        await session.commit()


async def process_resume_job(job_id: uuid.UUID) -> None:
    resume_id: uuid.UUID | None = None
    try:
        async with async_session_factory() as session:
            job = await session.get(BackgroundJob, job_id)
            if not job or job.status != JobStatus.RUNNING:
                return
            try:
                resume_id = uuid.UUID(str(job.payload["resume_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ResumeParseError("resume_job_payload_invalid", "简历任务参数无效") from exc
            resume = await session.get(Resume, resume_id)
            if not resume or not resume.storage_path:
                raise ResumeParseError("resume_source_missing", "简历源文件不存在")
            resume.parse_status = ResumeParseStatus.PARSING
            resume.parse_error_code = None
            resume.touch()
            job.progress = 0.15
            job.touch()
            await session.commit()

            text = await anyio.to_thread.run_sync(
                extract_resume_text,
                Path(resume.storage_path),
                resume.mime_type,
            )
            sections = split_resume_sections(text)
            claims = extract_resume_claims(sections)

            job.progress = 0.6
            job.touch()
            await session.commit()

            await session.execute(delete(ResumeClaim).where(ResumeClaim.resume_id == resume.id))
            await session.execute(delete(ResumeSection).where(ResumeSection.resume_id == resume.id))
            section_ids: dict[int, uuid.UUID] = {}
            for section in sections:
                model = ResumeSection(
                    resume_id=resume.id,
                    section_type=section.section_type,
                    heading=section.heading,
                    content=section.content,
                    sequence=section.sequence,
                    section_metadata={"parser": "deterministic-v1"},
                )
                session.add(model)
                await session.flush()
                section_ids[section.sequence] = model.id
            session.add_all(
                [
                    ResumeClaim(
                        resume_id=resume.id,
                        section_id=section_ids.get(claim.section_sequence),
                        claim_type=claim.claim_type,
                        content=claim.content,
                        confidence=claim.confidence,
                        source_span=claim.source_span,
                    )
                    for claim in claims
                ]
            )
            resume.parsed_text = text
            resume.parse_status = ResumeParseStatus.READY
            resume.parse_error_code = None
            resume.touch()
            job.status = JobStatus.COMPLETED
            job.progress = 1.0
            job.result = {
                "resume_id": str(resume.id),
                "section_count": len(sections),
                "claim_count": len(claims),
                "parser": "deterministic-v1",
            }
            job.locked_at = None
            job.locked_by = None
            job.touch()
            await session.commit()
    except ResumeParseError as exc:
        await _fail_job(
            job_id,
            resume_id,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
    except Exception as exc:  # Worker errors are recorded without exposing internal details.
        await logger.aexception(
            "resume_job_failed",
            job_id=str(job_id),
            error_type=type(exc).__name__,
        )
        await _fail_job(
            job_id,
            resume_id,
            code="resume_parse_internal_error",
            message="简历解析暂时失败，任务将自动重试",
            retryable=True,
        )


async def run_once(worker_id: str = "local-worker") -> bool:
    async with async_session_factory() as session:
        job_id = await claim_next_resume_job(session, worker_id)
    if not job_id:
        return False
    await process_resume_job(job_id)
    return True
