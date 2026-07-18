import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import (
    EntityBase,
    ModelRole,
    SegmentStatus,
    SummaryValidationStatus,
)


class InterviewContextState(EntityBase, table=True):
    __tablename__ = "interview_context_states"

    session_id: uuid.UUID = Field(
        foreign_key="interview_sessions.id",
        ondelete="CASCADE",
        unique=True,
        index=True,
    )
    current_plan_question_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="plan_questions.id",
        ondelete="SET NULL",
        index=True,
    )
    current_follow_up_index: int = Field(default=0, ge=0)
    completed_question_ids: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    unresolved_points: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    state_payload: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    token_count: int = Field(default=0, ge=0)


class ConversationSegment(EntityBase, table=True):
    __tablename__ = "conversation_segments"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_conversation_segment_sequence"),
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
    sequence: int = Field(ge=1)
    status: SegmentStatus = Field(
        default=SegmentStatus.OPEN,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    start_message_sequence: int = Field(ge=1)
    end_message_sequence: int | None = Field(default=None, ge=1)
    token_count: int = Field(default=0, ge=0)
    closed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ContextSummary(EntityBase, table=True):
    __tablename__ = "context_summaries"
    __table_args__ = (
        UniqueConstraint("segment_id", "summary_version", name="uq_context_summary_version"),
    )

    segment_id: uuid.UUID = Field(
        foreign_key="conversation_segments.id",
        ondelete="CASCADE",
        index=True,
    )
    summary_version: int = Field(default=1, ge=1, nullable=False)
    content: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    evidence_message_ids: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    schema_version: str = Field(default="1", min_length=1, max_length=32)
    summarizer_model_connection_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="model_connections.id",
        ondelete="SET NULL",
        index=True,
    )
    token_count: int = Field(default=0, ge=0)
    validation_status: SummaryValidationStatus = Field(
        default=SummaryValidationStatus.PENDING,
        sa_column=Column(String(32), nullable=False, index=True),
    )


class SummaryBundle(EntityBase, table=True):
    __tablename__ = "summary_bundles"

    session_id: uuid.UUID = Field(
        foreign_key="interview_sessions.id",
        ondelete="CASCADE",
        index=True,
    )
    summary_ids: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    covered_start_sequence: int = Field(ge=1)
    covered_end_sequence: int = Field(ge=1)
    content: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    token_count: int = Field(default=0, ge=0)


class ContextSnapshot(EntityBase, table=True):
    __tablename__ = "context_snapshots"

    session_id: uuid.UUID = Field(
        foreign_key="interview_sessions.id",
        ondelete="CASCADE",
        index=True,
    )
    agent_role: ModelRole = Field(sa_column=Column(String(32), nullable=False, index=True))
    model_connection_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="model_connections.id",
        ondelete="SET NULL",
        index=True,
    )
    prompt_schema_version: str = Field(default="1", min_length=1, max_length=32)
    included_refs: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    excluded_refs: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    token_by_layer: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    count_method: str = Field(min_length=1, max_length=80)
    compaction_level: int = Field(default=0, ge=0, le=4)
    provider_request_id: str | None = Field(default=None, max_length=255, index=True)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
