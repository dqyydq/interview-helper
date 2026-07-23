import uuid
from datetime import timedelta

import anyio
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.resume_structurer import ResumeStructureError, structure_resume_with_planner
from app.api.errors import AppError
from app.core.config import settings
from app.core.security import validated_existing_upload_path
from app.db.models.common import JobStatus, JobType, ModelRole, ResumeParseStatus, utc_now
from app.db.models.job import BackgroundJob
from app.db.models.resume import Resume, ResumeClaim, ResumeSection
from app.db.session import async_session_factory
from app.providers.base import ProviderError, StructuredOutputError
from app.providers.factory import build_provider
from app.services.model_connections import resolve_role_connection
from app.services.resume_parser import (
    ParsedClaim,
    ParsedSection,
    ResumeParseError,
    extract_resume_claims,
    extract_resume_text,
    split_resume_sections,
)

logger = structlog.get_logger(__name__)


async def _structure_resume(
    session: AsyncSession,
    resume: Resume,
    source_sections: list[ParsedSection],
) -> tuple[list[ParsedSection], list[ParsedClaim], str, str | None]:
    """Prefer the Planner role, but keep locally uploaded resumes usable without a model."""
    fallback_claims = extract_resume_claims(source_sections)
    provider = None
    try:
        connection = await resolve_role_connection(session, resume.profile_id, ModelRole.PLANNER)
        provider = build_provider(connection)
        structured = await structure_resume_with_planner(
            provider,
            source_sections,
            context_window_tokens=connection.context_window_tokens,
            max_output_tokens=connection.max_output_tokens,
            tokenizer_type=connection.tokenizer_type,
        )
        return structured.sections, structured.claims, "planner-v1", None
    except (
        AppError,
        ProviderError,
        StructuredOutputError,
        ResumeStructureError,
        ValueError,
    ) as exc:
        reason = getattr(exc, "code", "structure_invalid")
        await logger.awarning(
            "resume_planner_fallback",
            resume_id=str(resume.id),
            fallback_reason=reason,
        )
        return source_sections, fallback_claims, "deterministic-v1", str(reason)
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()


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
            try:
                storage_path = validated_existing_upload_path(
                    resume.storage_path,
                    resume.profile_id,
                )
            except ValueError as exc:
                raise ResumeParseError(
                    "resume_source_invalid",
                    "Resume source is outside the controlled upload directory.",
                ) from exc
            resume.parse_status = ResumeParseStatus.PARSING
            resume.parse_error_code = None
            resume.touch()
            job.progress = 0.15
            job.touch()
            await session.commit()

            try:
                with anyio.fail_after(settings.resume_parse_timeout_seconds):
                    text = await anyio.to_thread.run_sync(
                        extract_resume_text,
                        storage_path,
                        resume.mime_type,
                        abandon_on_cancel=True,
                    )
            except TimeoutError as exc:
                raise ResumeParseError(
                    "resume_parse_timeout",
                    "简历解析超时，任务将自动重试",
                    retryable=True,
                ) from exc
            source_sections = split_resume_sections(text)
            sections, claims, parser_name, fallback_reason = await _structure_resume(
                session,
                resume,
                source_sections,
            )

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
                    section_metadata={
                        "parser": parser_name,
                        **({"fallback_reason": fallback_reason} if fallback_reason else {}),
                    },
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
                "parser": parser_name,
                **({"fallback_reason": fallback_reason} if fallback_reason else {}),
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
