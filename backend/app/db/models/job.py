import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import EntityBase, JobStatus, JobType, utc_now


class BackgroundJob(EntityBase, table=True):
    __tablename__ = "background_jobs"

    profile_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    job_type: JobType = Field(sa_column=Column(String(48), nullable=False, index=True))
    status: JobStatus = Field(
        default=JobStatus.QUEUED,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    payload: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    result: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    error_code: str | None = Field(default=None, max_length=120)
    error_message: str | None = Field(default=None, max_length=2_000, sa_type=Text)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=20)
    idempotency_key: str = Field(min_length=1, max_length=255, unique=True, index=True)
    available_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        index=True,
    )
    locked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    locked_by: str | None = Field(default=None, max_length=255)
