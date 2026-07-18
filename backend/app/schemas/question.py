import uuid
from typing import Literal

from pydantic import Field

from app.db.models.common import (
    Difficulty,
    QuestionStatus,
    QuestionType,
    SourceType,
    Visibility,
)
from app.schemas.common import ApiModel, EntityPublic


class QuestionBankCreate(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4_000)
    visibility: Visibility = Visibility.PRIVATE


class QuestionBankUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4_000)
    visibility: Visibility | None = None


class QuestionBankPublic(EntityPublic):
    name: str
    description: str | None
    visibility: Visibility
    question_count: int
    archived: bool


class QuestionTagPublic(EntityPublic):
    name: str
    slug: str
    category: str


class QuestionVariantCreate(ApiModel):
    prompt: str = Field(min_length=1, max_length=50_000)
    variant_type: str = Field(default="paraphrase", min_length=1, max_length=80)


class QuestionVariantPublic(EntityPublic):
    prompt: str
    variant_type: str


class QuestionCreate(ApiModel):
    bank_id: uuid.UUID
    prompt: str = Field(min_length=1, max_length=50_000)
    question_type: QuestionType = QuestionType.OPEN_ENDED
    difficulty: Difficulty = Difficulty.INTERMEDIATE
    status: QuestionStatus = QuestionStatus.DRAFT
    reference_points: list[str] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)
    applicable_companies: list[str] = Field(default_factory=list)
    applicable_rounds: list[str] = Field(default_factory=list)
    source_note: str | None = Field(default=None, max_length=2_000)
    user_note: str | None = Field(default=None, max_length=10_000)
    tag_names: list[str] = Field(default_factory=list, max_length=30)


class QuestionUpdate(ApiModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=50_000)
    question_type: QuestionType | None = None
    difficulty: Difficulty | None = None
    status: QuestionStatus | None = None
    reference_points: list[str] | None = None
    follow_up_suggestions: list[str] | None = None
    applicable_companies: list[str] | None = None
    applicable_rounds: list[str] | None = None
    source_note: str | None = Field(default=None, max_length=2_000)
    user_note: str | None = Field(default=None, max_length=10_000)
    tag_names: list[str] | None = Field(default=None, max_length=30)


class QuestionPublic(EntityPublic):
    bank_id: uuid.UUID
    prompt: str
    question_type: QuestionType
    difficulty: Difficulty
    status: QuestionStatus
    reference_points: list
    follow_up_suggestions: list
    applicable_companies: list
    applicable_rounds: list
    source_type: SourceType
    source_note: str | None
    user_note: str | None
    times_used: int
    tags: list[QuestionTagPublic]
    variants: list[QuestionVariantPublic]


class QuestionBulkArchive(ApiModel):
    question_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class QuestionBulkResult(ApiModel):
    updated: int = Field(ge=0)


QuestionSortField = Literal["created_at", "updated_at", "difficulty", "times_used"]
SortOrder = Literal["asc", "desc"]
