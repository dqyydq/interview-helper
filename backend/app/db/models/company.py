import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import ContentStatus, EntityBase, Visibility, utc_now


class Company(EntityBase, table=True):
    __tablename__ = "companies"

    profile_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    name: str = Field(min_length=1, max_length=160, index=True)
    slug: str = Field(min_length=1, max_length=180, unique=True, index=True)
    description: str | None = Field(default=None, max_length=10_000, sa_type=Text)
    is_system: bool = Field(default=False, nullable=False)


class CompanyStylePack(EntityBase, table=True):
    __tablename__ = "company_style_packs"
    __table_args__ = (UniqueConstraint("company_id", "pack_version", name="uq_style_pack_version"),)

    company_id: uuid.UUID = Field(
        foreign_key="companies.id",
        ondelete="CASCADE",
        index=True,
    )
    name: str = Field(min_length=1, max_length=160)
    pack_version: int = Field(default=1, ge=1, nullable=False)
    supported_roles: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    default_interviewer_behavior: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    field_confidence: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    status: ContentStatus = Field(
        default=ContentStatus.DRAFT,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    visibility: Visibility = Field(
        default=Visibility.PRIVATE,
        sa_column=Column(String(32), nullable=False, index=True),
    )


class RoundProfile(EntityBase, table=True):
    __tablename__ = "round_profiles"
    __table_args__ = (
        UniqueConstraint("style_pack_id", "round_key", name="uq_round_profile_key"),
        UniqueConstraint("style_pack_id", "sequence", name="uq_round_profile_sequence"),
    )

    style_pack_id: uuid.UUID = Field(
        foreign_key="company_style_packs.id",
        ondelete="CASCADE",
        index=True,
    )
    round_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    sequence: int = Field(default=1, ge=1)
    opening_style: str | None = Field(default=None, max_length=4_000, sa_type=Text)
    topic_weights: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    follow_up_patterns: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    pressure_level: int = Field(default=1, ge=0, le=5)
    answer_expectations: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    evaluation_weights: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    duration_minutes: int = Field(default=45, ge=10, le=240)


class EvidenceItem(EntityBase, table=True):
    __tablename__ = "evidence_items"

    style_pack_id: uuid.UUID = Field(
        foreign_key="company_style_packs.id",
        ondelete="CASCADE",
        index=True,
    )
    source_url: str = Field(min_length=1, max_length=2_048)
    source_title: str = Field(min_length=1, max_length=500)
    field_path: str = Field(min_length=1, max_length=240, index=True)
    excerpt: str = Field(min_length=1, max_length=2_000, sa_type=Text)
    published_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    fetched_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_hash: str = Field(min_length=32, max_length=128, index=True)
