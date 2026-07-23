import uuid

from sqlalchemy import Column, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import EntityBase, ResumeParseStatus


class Resume(EntityBase, table=True):
    __tablename__ = "resumes"
    __table_args__ = (
        UniqueConstraint("profile_id", "content_hash", name="uq_resume_profile_hash"),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=120)
    storage_path: str | None = Field(default=None, max_length=1_024)
    content_hash: str = Field(min_length=32, max_length=128, index=True)
    parse_status: ResumeParseStatus = Field(
        default=ResumeParseStatus.PENDING,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    parsed_text: str | None = Field(default=None, max_length=500_000, sa_type=Text)
    parse_error_code: str | None = Field(default=None, max_length=120)


class ResumeSection(EntityBase, table=True):
    __tablename__ = "resume_sections"
    __table_args__ = (UniqueConstraint("resume_id", "sequence", name="uq_resume_section_sequence"),)

    resume_id: uuid.UUID = Field(
        foreign_key="resumes.id",
        ondelete="CASCADE",
        index=True,
    )
    section_type: str = Field(min_length=1, max_length=80, index=True)
    heading: str | None = Field(default=None, max_length=255)
    content: str = Field(min_length=1, max_length=100_000, sa_type=Text)
    sequence: int = Field(ge=1)
    section_metadata: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)


class ResumeClaim(EntityBase, table=True):
    __tablename__ = "resume_claims"

    resume_id: uuid.UUID = Field(
        foreign_key="resumes.id",
        ondelete="CASCADE",
        index=True,
    )
    section_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="resume_sections.id",
        ondelete="SET NULL",
        index=True,
    )
    claim_type: str = Field(min_length=1, max_length=80, index=True)
    content: str = Field(min_length=1, max_length=10_000, sa_type=Text)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_span: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
