import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import (
    ConflictStatus,
    EntityBase,
    MemoryStatus,
    MemoryType,
    ModelRole,
    utc_now,
)


class MemoryItem(EntityBase, table=True):
    __tablename__ = "memory_items"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "canonical_key",
            "memory_version",
            name="uq_memory_item_version",
        ),
        UniqueConstraint(
            "profile_id",
            "id",
            name="uq_memory_item_profile_identity",
        ),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    memory_type: MemoryType = Field(sa_column=Column(String(48), nullable=False, index=True))
    canonical_key: str = Field(min_length=1, max_length=255, index=True)
    memory_version: int = Field(default=1, ge=1, nullable=False)
    content: str = Field(min_length=1, max_length=20_000, sa_type=Text)
    structured_value: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    status: MemoryStatus = Field(
        default=MemoryStatus.PROPOSED,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    first_observed_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
    )
    last_verified_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    last_used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    pinned: bool = Field(default=False, nullable=False, index=True)


class MemorySource(EntityBase, table=True):
    __tablename__ = "memory_sources"

    memory_id: uuid.UUID = Field(
        foreign_key="memory_items.id",
        ondelete="CASCADE",
        index=True,
    )
    session_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="interview_sessions.id",
        ondelete="CASCADE",
        index=True,
    )
    message_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="interview_messages.id",
        ondelete="CASCADE",
        index=True,
    )
    source_type: str = Field(default="message", min_length=1, max_length=48, index=True)
    evidence_excerpt: str | None = Field(default=None, max_length=1_000, sa_type=Text)
    observed_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))


class MemoryConflict(EntityBase, table=True):
    __tablename__ = "memory_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "conflicting_memory_id",
            name="uq_memory_conflict_pair",
        ),
    )

    memory_id: uuid.UUID = Field(
        foreign_key="memory_items.id",
        ondelete="CASCADE",
        index=True,
    )
    conflicting_memory_id: uuid.UUID = Field(
        foreign_key="memory_items.id",
        ondelete="CASCADE",
        index=True,
    )
    status: ConflictStatus = Field(
        default=ConflictStatus.OPEN,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    resolution: str | None = Field(default=None, max_length=4_000, sa_type=Text)
    resolved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class MemoryUsage(EntityBase, table=True):
    __tablename__ = "memory_usages"

    memory_id: uuid.UUID = Field(
        foreign_key="memory_items.id",
        ondelete="CASCADE",
        index=True,
    )
    session_id: uuid.UUID = Field(
        foreign_key="interview_sessions.id",
        ondelete="CASCADE",
        index=True,
    )
    context_snapshot_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="context_snapshots.id",
        ondelete="SET NULL",
        index=True,
    )
    agent_role: ModelRole = Field(sa_column=Column(String(32), nullable=False, index=True))
    reason: str | None = Field(default=None, max_length=1_000, sa_type=Text)
    used_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))
