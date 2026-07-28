"""Public, privacy-safe readiness data for the interview preparation desk."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel

ReadinessStatus = Literal[
    "ready",
    "blocked",
    "available",
    "not_configured",
    "processing",
    "unavailable",
]
CompanyTrustStatus = Literal["template", "draft", "source_backed"]


class InterviewReadinessCheck(ApiModel):
    """One concise setup signal, intentionally free of secrets and user content."""

    key: str
    status: ReadinessStatus
    label: str
    detail: str | None = None
    action: str | None = None


class QuickTrialDefaults(ApiModel):
    """A reliable zero-material starting point for a first short simulation."""

    session_kind: Literal["quick_trial"] = "quick_trial"
    duration_minutes: int = Field(default=10, ge=10, le=10)
    target_question_count: int = Field(default=2, ge=2, le=2)
    include_in_trends: bool = False
    role_name: str = "llm_application_engineer"


class InterviewReadinessEvidenceSummary(ApiModel):
    title: str
    url: str
    excerpt: str | None = None
    fetched_at: datetime | None = None


class InterviewReadinessCompanyProfile(ApiModel):
    """The exact profile boundary that will be used for the selected round."""

    company_id: uuid.UUID | None = None
    company_name: str | None = None
    round_profile_id: uuid.UUID | None = None
    round_name: str | None = None
    style_pack_id: uuid.UUID | None = None
    pack_version: int | None = None
    trust_status: CompanyTrustStatus | None = None
    trust_label: str | None = None
    evidence_count: int = 0
    latest_evidence_at: datetime | None = None
    source_summaries: list[InterviewReadinessEvidenceSummary] = Field(default_factory=list)


class InterviewReadinessPublic(ApiModel):
    """Aggregated readiness for creating an interview plan.

    ``blocking`` always contains the required prerequisites so a caller can
    render both ready and missing states without making a second diagnostics
    request.  ``enhancements`` are explicitly non-blocking for quick trials.
    """

    ready: bool
    blocking: list[InterviewReadinessCheck]
    enhancements: list[InterviewReadinessCheck]
    defaults: dict[str, QuickTrialDefaults]
    company_profile: InterviewReadinessCompanyProfile
