import hashlib
import uuid
import zipfile
from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.db.models.common import JobStatus, JobType, ResumeParseStatus
from app.db.models.job import BackgroundJob
from app.db.models.resume import Resume, ResumeClaim, ResumeSection
from app.schemas.resume import (
    BackgroundJobPublic,
    ResumeClaimPublic,
    ResumePublic,
    ResumeSectionPublic,
    ResumeUploadResult,
)

ALLOWED_MIME_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
}


def _safe_filename(filename: str) -> str:
    safe = Path(filename).name.strip()
    if not safe or safe in {".", ".."}:
        raise AppError(code="filename_invalid", message="文件名无效", status_code=422)
    return safe[:255]


def _validate_docx(data: bytes) -> bool:
    try:
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
        return "[Content_Types].xml" in names and "word/document.xml" in names
    except zipfile.BadZipFile:
        return False


def validate_resume_upload(filename: str, mime_type: str, data: bytes) -> tuple[str, str]:
    safe_filename = _safe_filename(filename)
    suffix = Path(safe_filename).suffix.casefold()
    if suffix not in ALLOWED_MIME_TYPES:
        raise AppError(
            code="resume_type_unsupported",
            message="仅支持 PDF、DOCX、Markdown 和 TXT 简历",
            status_code=415,
        )
    normalized_mime = (mime_type or "application/octet-stream").split(";", 1)[0].strip().casefold()
    if normalized_mime not in ALLOWED_MIME_TYPES[suffix]:
        raise AppError(
            code="resume_mime_mismatch",
            message="文件类型与扩展名不一致",
            status_code=415,
        )
    if not data:
        raise AppError(code="resume_file_empty", message="上传的简历为空", status_code=422)
    if len(data) > settings.resume_upload_max_bytes:
        raise AppError(
            code="resume_file_too_large",
            message="简历文件不能超过 5 MB",
            status_code=413,
        )
    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise AppError(code="resume_signature_invalid", message="PDF 文件签名无效", status_code=422)
    if suffix == ".docx" and not _validate_docx(data):
        raise AppError(
            code="resume_signature_invalid",
            message="DOCX 文件结构无效",
            status_code=422,
        )
    if suffix in {".md", ".txt"}:
        try:
            decoded = data.decode("utf-8-sig").replace("\x00", "").strip()
        except UnicodeDecodeError as exc:
            raise AppError(
                code="resume_text_encoding_invalid",
                message="文本简历必须使用 UTF-8 编码",
                status_code=422,
            ) from exc
        if not decoded:
            raise AppError(code="resume_file_empty", message="上传的简历为空", status_code=422)
    return safe_filename, suffix


async def _sections(session: AsyncSession, resume_id: uuid.UUID) -> list[ResumeSection]:
    return list(
        (
            await session.scalars(
                select(ResumeSection)
                .where(
                    ResumeSection.resume_id == resume_id,
                    ResumeSection.deleted_at.is_(None),
                )
                .order_by(ResumeSection.sequence)
            )
        ).all()
    )


async def _claims(session: AsyncSession, resume_id: uuid.UUID) -> list[ResumeClaim]:
    return list(
        (
            await session.scalars(
                select(ResumeClaim)
                .where(
                    ResumeClaim.resume_id == resume_id,
                    ResumeClaim.deleted_at.is_(None),
                )
                .order_by(ResumeClaim.created_at)
            )
        ).all()
    )


async def resume_public(session: AsyncSession, resume: Resume) -> ResumePublic:
    sections = await _sections(session, resume.id)
    claims = await _claims(session, resume.id)
    return ResumePublic(
        id=resume.id,
        created_at=resume.created_at,
        updated_at=resume.updated_at,
        version=resume.version,
        filename=resume.filename,
        mime_type=resume.mime_type,
        content_hash=resume.content_hash,
        parse_status=resume.parse_status,
        parsed_text=resume.parsed_text,
        parse_error_code=resume.parse_error_code,
        sections=[ResumeSectionPublic.model_validate(item) for item in sections],
        claims=[ResumeClaimPublic.model_validate(item) for item in claims],
    )


def job_public(job: BackgroundJob) -> BackgroundJobPublic:
    return BackgroundJobPublic.model_validate(job)


async def get_resume(
    session: AsyncSession,
    profile_id: uuid.UUID,
    resume_id: uuid.UUID,
) -> Resume:
    resume = await session.scalar(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.profile_id == profile_id,
            Resume.deleted_at.is_(None),
        )
    )
    if not resume:
        raise AppError(code="resume_not_found", message="简历不存在", status_code=404)
    return resume


async def list_resumes(session: AsyncSession, profile_id: uuid.UUID) -> list[ResumePublic]:
    resumes = (
        await session.scalars(
            select(Resume)
            .where(Resume.profile_id == profile_id, Resume.deleted_at.is_(None))
            .order_by(Resume.created_at.desc())
        )
    ).all()
    return [await resume_public(session, item) for item in resumes]


async def get_job(
    session: AsyncSession,
    profile_id: uuid.UUID,
    job_id: uuid.UUID,
) -> BackgroundJob:
    job = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.profile_id == profile_id,
            BackgroundJob.deleted_at.is_(None),
        )
    )
    if not job:
        raise AppError(code="job_not_found", message="后台任务不存在", status_code=404)
    return job


async def _resume_job(
    session: AsyncSession,
    profile_id: uuid.UUID,
    resume_id: uuid.UUID,
) -> BackgroundJob | None:
    return await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.profile_id == profile_id,
            BackgroundJob.job_type == JobType.RESUME_PARSE,
            BackgroundJob.payload["resume_id"].astext == str(resume_id),
            BackgroundJob.deleted_at.is_(None),
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(1)
    )


async def _write_upload(
    profile_id: uuid.UUID,
    content_hash: str,
    suffix: str,
    data: bytes,
) -> str:
    directory = anyio.Path(settings.upload_dir) / profile_id.hex
    await directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{content_hash}{suffix}"
    await target.write_bytes(data)
    return str(target)


def _new_parse_job(profile_id: uuid.UUID, resume: Resume, *, key_suffix: str) -> BackgroundJob:
    return BackgroundJob(
        profile_id=profile_id,
        job_type=JobType.RESUME_PARSE,
        status=JobStatus.QUEUED,
        progress=0.0,
        payload={"resume_id": str(resume.id)},
        idempotency_key=f"resume-parse:{resume.id}:{key_suffix}",
        max_attempts=3,
    )


async def create_resume_upload(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    filename: str,
    mime_type: str,
    data: bytes,
) -> ResumeUploadResult:
    safe_filename, suffix = validate_resume_upload(filename, mime_type, data)
    content_hash = hashlib.sha256(data).hexdigest()
    existing = await session.scalar(
        select(Resume).where(
            Resume.profile_id == profile_id,
            Resume.content_hash == content_hash,
            Resume.deleted_at.is_(None),
        )
    )
    if existing:
        job = await _resume_job(session, profile_id, existing.id)
        return ResumeUploadResult(
            resume=await resume_public(session, existing),
            job=job_public(job) if job else None,
            reused=True,
        )

    resume = Resume(
        profile_id=profile_id,
        filename=safe_filename,
        mime_type=mime_type,
        content_hash=content_hash,
        parse_status=ResumeParseStatus.PENDING,
    )
    session.add(resume)
    await session.flush()
    resume.storage_path = await _write_upload(profile_id, content_hash, suffix, data)
    job = _new_parse_job(profile_id, resume, key_suffix="initial")
    session.add(job)
    await session.commit()
    await session.refresh(resume)
    await session.refresh(job)
    return ResumeUploadResult(
        resume=await resume_public(session, resume),
        job=job_public(job),
        reused=False,
    )


async def retry_resume_parse(
    session: AsyncSession,
    profile_id: uuid.UUID,
    resume: Resume,
) -> BackgroundJobPublic:
    current = await _resume_job(session, profile_id, resume.id)
    if current and current.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        return job_public(current)
    if resume.parse_status == ResumeParseStatus.READY:
        if current:
            return job_public(current)
        raise AppError(code="resume_already_parsed", message="简历已经解析完成", status_code=409)
    resume.parse_status = ResumeParseStatus.PENDING
    resume.parse_error_code = None
    resume.touch()
    job = _new_parse_job(profile_id, resume, key_suffix=f"retry-{resume.version}")
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job_public(job)
