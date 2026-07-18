import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.common import JobStatus, JobType, ResumeParseStatus
from app.schemas.common import ApiModel, EntityPublic


class ResumeSectionPublic(EntityPublic):
    section_type: str
    heading: str | None
    content: str
    sequence: int
    section_metadata: dict


class ResumeClaimPublic(EntityPublic):
    section_id: uuid.UUID | None
    claim_type: str
    content: str
    confidence: float
    source_span: dict


class ResumePublic(EntityPublic):
    filename: str
    mime_type: str
    content_hash: str
    parse_status: ResumeParseStatus
    parsed_text: str | None
    parse_error_code: str | None
    sections: list[ResumeSectionPublic]
    claims: list[ResumeClaimPublic]


class BackgroundJobPublic(EntityPublic):
    job_type: JobType
    status: JobStatus
    progress: float = Field(ge=0.0, le=1.0)
    result: dict
    error_code: str | None
    error_message: str | None
    attempts: int
    max_attempts: int
    available_at: datetime


class ResumeUploadResult(ApiModel):
    resume: ResumePublic
    job: BackgroundJobPublic | None
    reused: bool
