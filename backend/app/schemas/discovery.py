"""Public request and response contracts for public-source discovery.

The connector API is deliberately small. It exposes only non-sensitive provider
configuration and capability flags; the encrypted credential never crosses this
boundary.
"""

import uuid
from datetime import datetime

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.db.models.common import (
    ConnectionStatus,
    Difficulty,
    DiscoveryCandidateStatus,
    DiscoveryProviderType,
    DiscoveryRunStatus,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    QuestionType,
)
from app.schemas.common import ApiModel, EntityPublic, Page


class DiscoveryConnectorConfiguration(ApiModel):
    """Non-sensitive defaults for a discovery connector.

    Search terms and domain policy are request-scoped, so the initial Tavily
    connector intentionally has no endpoint, proxy, callback, or arbitrary-header
    settings. ``default_country`` is retained as a harmless future search default.
    """

    model_config = ConfigDict(from_attributes=True, use_enum_values=True, extra="forbid")

    default_country: str | None = Field(default=None, min_length=2, max_length=64)

    @field_validator("default_country")
    @classmethod
    def normalize_country(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("default_country cannot be blank")
        if not all(character.isalpha() or character in {"-", "_"} for character in normalized):
            raise ValueError("default_country contains unsupported characters")
        return normalized


class DiscoveryConnectorCapabilities(ApiModel):
    supports_domain_filters: bool
    supports_extract: bool
    safe_extract: bool


class DiscoveryConnectorCreate(ApiModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    provider_type: DiscoveryProviderType = DiscoveryProviderType.TAVILY
    api_key: str = Field(min_length=1, max_length=16_000, repr=False)
    enabled: bool = True
    configuration: DiscoveryConnectorConfiguration = Field(
        default_factory=DiscoveryConnectorConfiguration
    )

    @field_validator("name", "api_key")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized


class DiscoveryConnectorUpdate(ApiModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    api_key: str | None = Field(default=None, min_length=1, max_length=16_000, repr=False)
    enabled: bool | None = None
    configuration: DiscoveryConnectorConfiguration | None = None

    @field_validator("name", "api_key")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized


class DiscoveryConnectorPublic(EntityPublic):
    name: str
    provider_type: DiscoveryProviderType
    enabled: bool
    capabilities: DiscoveryConnectorCapabilities
    configuration: DiscoveryConnectorConfiguration
    configuration_version: int = Field(ge=1)
    status: ConnectionStatus
    last_tested_at: datetime | None
    last_error_code: str | None
    has_api_key: bool


class DiscoveryConnectorTestResult(ApiModel):
    status: ConnectionStatus
    latency_ms: int = Field(ge=0)
    error_code: str | None = None


class QuestionDiscoveryCreate(ApiModel):
    """The explicit, privacy-bounded input for one discovery run.

    Nothing outside this object is sent to a search connector.  In particular, a
    resume, interview transcript, memory, or existing answer cannot be smuggled into
    a discovery request through a free-form metadata object.
    """

    model_config = ConfigDict(from_attributes=True, use_enum_values=True, extra="forbid")

    connector_id: uuid.UUID
    source_mode: DiscoverySourceMode
    company: str | None = Field(default=None, max_length=160)
    round: str | None = Field(default=None, max_length=160)
    role: str | None = Field(default=None, max_length=160)
    skills: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    query: str | None = Field(default=None, max_length=500)
    question_type: QuestionType | None = None
    difficulty: Difficulty | None = None
    country: str | None = Field(default=None, max_length=64)
    urls: list[str] = Field(default_factory=list, max_length=5)
    full_web: bool = False
    allow_domains: list[str] = Field(default_factory=list, max_length=50)
    deny_domains: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("company", "round", "role", "query", "country")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("skills", "keywords", "urls", "allow_domains", "deny_domains")
    @classmethod
    def normalize_text_lists(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("list values must be text")
            cleaned = item.strip()
            if not cleaned:
                continue
            if len(cleaned) > 500:
                raise ValueError("list value is too long")
            if cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @model_validator(mode="after")
    def validate_mode_input(self) -> "QuestionDiscoveryCreate":
        if DiscoverySourceMode(self.source_mode) is DiscoverySourceMode.SEARCH:
            if self.urls:
                raise ValueError("urls are only accepted for URL discovery")
            search_fields = (
                self.company,
                self.round,
                self.role,
                self.skills,
                self.keywords,
                self.query,
            )
            if not any(search_fields):
                raise ValueError("search discovery needs at least one explicit search condition")
        elif not self.urls:
            raise ValueError("URL discovery needs at least one URL")
        return self


class QuestionDiscoveryRunPublic(EntityPublic):
    connector_id: uuid.UUID
    connector_configuration_version: int = Field(ge=1)
    initiated_by: str
    source_mode: DiscoverySourceMode
    query_snapshot: dict
    status: DiscoveryRunStatus
    stage: str | None
    progress: float = Field(ge=0.0, le=1.0)
    source_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    failed_source_count: int = Field(ge=0)
    error_code: str | None
    error_summary: str | None
    cancel_requested_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime


class QuestionDiscoverySourcePublic(EntityPublic):
    run_id: uuid.UUID
    normalized_url: str
    final_url: str | None
    title: str | None
    domain: str
    source_category: str
    status: DiscoverySourceStatus
    fetched_at: datetime | None
    excerpt: str | None
    attribution: dict
    policy_metadata: dict
    failure_code: str | None
    failure_summary: str | None
    expires_at: datetime


class QuestionDiscoveryCandidatePublic(EntityPublic):
    """Read-only candidate contract reserved for the later Researcher milestone."""

    run_id: uuid.UUID
    prompt: str
    question_type: QuestionType
    difficulty: Difficulty
    suggested_tags: list
    suggested_roles: list
    suggested_skills: list
    applicable_companies: list
    applicable_rounds: list
    reference_points: list
    follow_up_suggestions: list
    matching_reason: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    researcher_model_name: str | None
    schema_version: str
    candidate_revision: int = Field(ge=1)
    similar_question_ids: list
    status: DiscoveryCandidateStatus
    import_count: int = Field(ge=0)
    failure_code: str | None
    failure_summary: str | None
    expires_at: datetime


class QuestionDiscoveryCandidateEvidencePublic(EntityPublic):
    run_id: uuid.UUID
    candidate_id: uuid.UUID
    source_id: uuid.UUID
    source_title: str
    normalized_url: str
    source_domain: str
    source_category: str
    excerpt: str
    source_locator: str | None
    confidence: float = Field(ge=0.0, le=1.0)


class DiscoveryImportItemCreate(ApiModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True, extra="forbid")

    candidate_id: uuid.UUID
    candidate_revision: int = Field(ge=1)
    prompt: str | None = Field(default=None, min_length=1, max_length=50_000)
    question_type: QuestionType | None = None
    difficulty: Difficulty | None = None
    tag_names: list[str] | None = Field(default=None, max_length=30)
    reference_points: list[str] | None = Field(default=None, max_length=50)
    follow_up_suggestions: list[str] | None = Field(default=None, max_length=50)
    applicable_companies: list[str] | None = Field(default=None, max_length=50)
    applicable_rounds: list[str] | None = Field(default=None, max_length=50)
    source_note: str | None = Field(default=None, max_length=2_000)
    user_note: str | None = Field(default=None, max_length=10_000)


class DiscoveryImportCreate(ApiModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True, extra="forbid")

    bank_id: uuid.UUID
    items: list[DiscoveryImportItemCreate] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def reject_duplicate_candidates(self) -> "DiscoveryImportCreate":
        candidate_ids = [item.candidate_id for item in self.items]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("a candidate can appear only once in an import request")
        return self


class DiscoveryImportItemPublic(ApiModel):
    candidate_id: uuid.UUID
    candidate_revision: int = Field(ge=1)
    question_id: uuid.UUID
    import_id: uuid.UUID


class DiscoveryImportPublic(ApiModel):
    run_id: uuid.UUID
    bank_id: uuid.UUID
    batch_id: uuid.UUID
    request_hash: str
    items: list[DiscoveryImportItemPublic]
    replayed: bool


class QuestionSourceProvenancePublic(EntityPublic):
    question_id: uuid.UUID
    discovery_run_id: uuid.UUID | None
    candidate_id: uuid.UUID | None
    source_title: str
    normalized_url: str
    source_domain: str
    source_category: str
    fetched_at: datetime
    excerpt: str
    evidence_hash: str
    attribution: dict


class QuestionDiscoveryRunPage(Page[QuestionDiscoveryRunPublic]):
    pass


class QuestionDiscoverySourcePage(Page[QuestionDiscoverySourcePublic]):
    pass


class QuestionDiscoveryCandidatePage(Page[QuestionDiscoveryCandidatePublic]):
    pass
