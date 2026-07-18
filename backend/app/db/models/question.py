import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import (
    Difficulty,
    EntityBase,
    QuestionStatus,
    QuestionType,
    SourceType,
    Visibility,
)


class QuestionBank(EntityBase, table=True):
    __tablename__ = "question_banks"
    __table_args__ = (UniqueConstraint("profile_id", "name", name="uq_question_bank_name"),)

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4_000, sa_type=Text)
    visibility: Visibility = Field(
        default=Visibility.PRIVATE,
        sa_column=Column(String(32), nullable=False, index=True),
    )


class Question(EntityBase, table=True):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("bank_id", "normalized_hash", name="uq_question_bank_hash"),
    )

    bank_id: uuid.UUID = Field(
        foreign_key="question_banks.id",
        ondelete="CASCADE",
        index=True,
    )
    prompt: str = Field(min_length=1, max_length=50_000, sa_type=Text)
    question_type: QuestionType = Field(
        default=QuestionType.OPEN_ENDED,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    difficulty: Difficulty = Field(
        default=Difficulty.INTERMEDIATE,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    status: QuestionStatus = Field(
        default=QuestionStatus.DRAFT,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    reference_points: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    follow_up_suggestions: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    applicable_companies: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    applicable_rounds: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    source_type: SourceType = Field(
        default=SourceType.MANUAL,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    source_note: str | None = Field(default=None, max_length=2_000, sa_type=Text)
    user_note: str | None = Field(default=None, max_length=10_000, sa_type=Text)
    normalized_hash: str = Field(min_length=32, max_length=128, index=True)
    times_used: int = Field(default=0, ge=0)
    last_used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class QuestionVariant(EntityBase, table=True):
    __tablename__ = "question_variants"

    question_id: uuid.UUID = Field(
        foreign_key="questions.id",
        ondelete="CASCADE",
        index=True,
    )
    prompt: str = Field(min_length=1, max_length=50_000, sa_type=Text)
    variant_type: str = Field(default="paraphrase", min_length=1, max_length=80)


class QuestionTag(EntityBase, table=True):
    __tablename__ = "question_tags"

    name: str = Field(min_length=1, max_length=100, unique=True, index=True)
    slug: str = Field(min_length=1, max_length=120, unique=True, index=True)
    category: str = Field(default="capability", min_length=1, max_length=80, index=True)


class QuestionTagLink(EntityBase, table=True):
    __tablename__ = "question_tag_links"
    __table_args__ = (
        UniqueConstraint("question_id", "tag_id", name="uq_question_tag_link"),
    )

    question_id: uuid.UUID = Field(
        foreign_key="questions.id",
        ondelete="CASCADE",
        index=True,
    )
    tag_id: uuid.UUID = Field(
        foreign_key="question_tags.id",
        ondelete="CASCADE",
        index=True,
    )
