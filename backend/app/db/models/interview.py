import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import (
    AttachmentType,
    EntityBase,
    MessageRole,
    PlanStatus,
    SessionStatus,
    SourceType,
)


class InterviewConfig(EntityBase, table=True):
    __tablename__ = "interview_configs"

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    company_id: uuid.UUID = Field(
        foreign_key="companies.id",
        ondelete="RESTRICT",
        index=True,
    )
    round_profile_id: uuid.UUID = Field(
        foreign_key="round_profiles.id",
        ondelete="RESTRICT",
        index=True,
    )
    resume_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="resumes.id",
        ondelete="SET NULL",
        index=True,
    )
    role_name: str = Field(min_length=1, max_length=160)
    duration_minutes: int = Field(default=45, ge=10, le=240)
    target_question_count: int = Field(default=6, ge=1, le=50)
    question_bank_ids: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    source_weights: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    preferences: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)


class InterviewPlan(EntityBase, table=True):
    __tablename__ = "interview_plans"

    config_id: uuid.UUID = Field(
        foreign_key="interview_configs.id",
        ondelete="RESTRICT",
        index=True,
    )
    style_pack_id: uuid.UUID = Field(
        foreign_key="company_style_packs.id",
        ondelete="RESTRICT",
        index=True,
    )
    status: PlanStatus = Field(
        default=PlanStatus.DRAFT,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    total_minutes: int = Field(ge=10, le=240)
    plan_snapshot: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    rationale: str | None = Field(default=None, max_length=10_000, sa_type=Text)
    frozen_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class PlanQuestion(EntityBase, table=True):
    __tablename__ = "plan_questions"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence", name="uq_plan_question_sequence"),
        UniqueConstraint("plan_id", "id", name="uq_plan_question_plan_identity"),
    )

    plan_id: uuid.UUID = Field(
        foreign_key="interview_plans.id",
        ondelete="CASCADE",
        index=True,
    )
    question_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="questions.id",
        ondelete="RESTRICT",
        index=True,
    )
    sequence: int = Field(ge=1)
    source_type: SourceType = Field(
        default=SourceType.MANUAL,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    source_ref: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    prompt_snapshot: str = Field(min_length=1, max_length=50_000, sa_type=Text)
    capability_tags: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    allocated_seconds: int = Field(default=420, ge=30, le=7_200)
    follow_up_budget: int = Field(default=2, ge=0, le=10)
    selection_reason: str = Field(min_length=1, max_length=2_000, sa_type=Text)


class InterviewSession(EntityBase, table=True):
    __tablename__ = "interview_sessions"

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    plan_id: uuid.UUID = Field(
        foreign_key="interview_plans.id",
        ondelete="RESTRICT",
        index=True,
    )
    status: SessionStatus = Field(
        default=SessionStatus.READY,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    ended_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    current_question_sequence: int | None = Field(default=None, ge=1)
    last_event_sequence: int = Field(default=0, ge=0)
    failure_code: str | None = Field(default=None, max_length=120)


class InterviewMessage(EntityBase, table=True):
    __tablename__ = "interview_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_interview_message_sequence"),
    )

    session_id: uuid.UUID = Field(
        foreign_key="interview_sessions.id",
        ondelete="CASCADE",
        index=True,
    )
    plan_question_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="plan_questions.id",
        ondelete="SET NULL",
        index=True,
    )
    segment_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="conversation_segments.id",
        ondelete="SET NULL",
        index=True,
    )
    role: MessageRole = Field(
        sa_column=Column(String(32), nullable=False, index=True),
    )
    sequence: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=200_000, sa_type=Text)
    confirmed: bool = Field(default=True, nullable=False)
    token_count: int | None = Field(default=None, ge=0)
    provider_message_id: str | None = Field(default=None, max_length=255)
    message_metadata: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)


class InterviewRealtimeEvent(EntityBase, table=True):
    __tablename__ = "interview_realtime_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_realtime_event_sequence"),
        UniqueConstraint("session_id", "event_id", name="uq_realtime_event_id"),
        UniqueConstraint("session_id", "client_event_id", name="uq_realtime_client_event_id"),
    )

    session_id: uuid.UUID = Field(
        foreign_key="interview_sessions.id",
        ondelete="CASCADE",
        index=True,
    )
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4, index=True)
    client_event_id: str | None = Field(default=None, max_length=120, index=True)
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=80, index=True)
    payload: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)


class AnswerAttachment(EntityBase, table=True):
    __tablename__ = "answer_attachments"

    message_id: uuid.UUID = Field(
        foreign_key="interview_messages.id",
        ondelete="CASCADE",
        index=True,
    )
    attachment_type: AttachmentType = Field(
        sa_column=Column(String(32), nullable=False, index=True),
    )
    filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, max_length=80)
    storage_path: str | None = Field(default=None, max_length=1_024)
    content: str | None = Field(default=None, max_length=200_000, sa_type=Text)
    size_bytes: int = Field(default=0, ge=0, le=10_000_000)
    attachment_metadata: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
