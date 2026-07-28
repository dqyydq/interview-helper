import uuid
from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, Field

from app.db.models.common import ContentStatus, Visibility
from app.schemas.common import ApiModel, EntityPublic


class RoundProfileCreate(ApiModel):
    round_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    sequence: int = Field(ge=1)
    opening_style: str | None = Field(default=None, max_length=4_000)
    topic_weights: dict[str, float] = Field(default_factory=dict)
    follow_up_patterns: list[str] = Field(default_factory=list)
    pressure_level: int = Field(default=1, ge=0, le=5)
    answer_expectations: list[str] = Field(default_factory=list)
    evaluation_weights: dict[str, float] = Field(default_factory=dict)
    duration_minutes: int = Field(default=45, ge=10, le=240)


class RoundProfileUpdate(ApiModel):
    round_key: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sequence: int | None = Field(default=None, ge=1)
    opening_style: str | None = Field(default=None, max_length=4_000)
    topic_weights: dict[str, float] | None = None
    follow_up_patterns: list[str] | None = None
    pressure_level: int | None = Field(default=None, ge=0, le=5)
    answer_expectations: list[str] | None = None
    evaluation_weights: dict[str, float] | None = None
    duration_minutes: int | None = Field(default=None, ge=10, le=240)


class RoundProfilePublic(EntityPublic):
    round_key: str
    name: str
    sequence: int
    opening_style: str | None
    topic_weights: dict
    follow_up_patterns: list
    pressure_level: int
    answer_expectations: list
    evaluation_weights: dict
    duration_minutes: int


class EvidenceItemCreate(ApiModel):
    source_url: AnyHttpUrl
    source_title: str = Field(min_length=1, max_length=500)
    field_path: str = Field(min_length=1, max_length=240)
    excerpt: str = Field(min_length=1, max_length=2_000)
    published_at: datetime | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class EvidenceItemPublic(EntityPublic):
    source_url: str
    source_title: str
    field_path: str
    excerpt: str
    published_at: datetime | None
    fetched_at: datetime
    confidence: float


class VisualEvidenceCandidatePublic(ApiModel):
    field_path: str
    excerpt: str
    confidence: float


class VisualEvidenceExtractionPublic(ApiModel):
    source_url: AnyHttpUrl
    source_title: str
    candidates: list[VisualEvidenceCandidatePublic]
    allowed_field_paths: list[str]
    warning_codes: list[str]
    image_retained: bool = False


class StylePackDraft(ApiModel):
    name: str = Field(default="自定义风格草案", min_length=1, max_length=160)
    supported_roles: list[str] = Field(default_factory=list)
    default_interviewer_behavior: dict = Field(default_factory=dict)
    field_confidence: dict[str, float] = Field(default_factory=dict)
    visibility: Visibility = Visibility.PRIVATE


class StylePackUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    supported_roles: list[str] | None = None
    default_interviewer_behavior: dict | None = None
    field_confidence: dict[str, float] | None = None
    visibility: Visibility | None = None


class CompanyStylePackPublic(EntityPublic):
    name: str
    pack_version: int
    supported_roles: list
    default_interviewer_behavior: dict
    field_confidence: dict
    status: ContentStatus
    visibility: Visibility
    evidence_count: int
    evidence_label: str
    # These are additive trust signals.  They describe the provenance of the
    # current style pack without changing the older label used by existing
    # company-management screens.
    trust_status: Literal["template", "draft", "source_backed"]
    latest_evidence_at: datetime | None
    rounds: list[RoundProfilePublic]
    evidence: list[EvidenceItemPublic]


class CompanyCreate(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=10_000)
    style_pack: StylePackDraft = Field(default_factory=StylePackDraft)
    rounds: list[RoundProfileCreate] = Field(min_length=1, max_length=20)


class CompanyUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=10_000)


class StylePackRevisionCreate(StylePackDraft):
    rounds: list[RoundProfileCreate] | None = Field(default=None, min_length=1, max_length=20)
    copy_evidence: bool = True


class CompanyPublic(EntityPublic):
    name: str
    slug: str
    description: str | None
    is_system: bool
    archived: bool
    latest_style_pack: CompanyStylePackPublic | None


class CompanySeedResult(ApiModel):
    created: int
    unchanged: int
    upgraded: int
    company_ids: list[uuid.UUID]
