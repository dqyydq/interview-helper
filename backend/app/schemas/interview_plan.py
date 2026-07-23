import uuid
from datetime import datetime

from pydantic import Field, model_validator

from app.db.models.common import JobStatus, PlanStatus, SourceType
from app.schemas.common import ApiModel, EntityPublic


class InterviewPlanCreate(ApiModel):
    company_id: uuid.UUID
    round_profile_id: uuid.UUID
    role_name: str = Field(default="llm_application_engineer", min_length=1, max_length=160)
    duration_minutes: int = Field(default=45, ge=10, le=240)
    target_question_count: int = Field(default=6, ge=1, le=20)
    question_bank_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    resume_id: uuid.UUID | None = None
    source_weights: dict[str, float] = Field(
        default_factory=lambda: {"manual": 0.4, "resume": 0.3, "generated": 0.3}
    )
    preferences: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_weights(self) -> "InterviewPlanCreate":
        allowed = {"manual", "resume", "generated"}
        if set(self.source_weights) - allowed:
            raise ValueError("source weights contain an unsupported source")
        if any(value < 0 or value > 1 for value in self.source_weights.values()):
            raise ValueError("source weights must be between 0 and 1")
        if sum(self.source_weights.values()) <= 0:
            raise ValueError("at least one source weight must be positive")
        return self


class PlannerQuestionDraft(ApiModel):
    """A model-selected ordering of a server-provided candidate question.

    The candidate key is deliberately opaque to the provider.  The service resolves it
    back to the original prompt, source reference and capability tags only after
    semantic validation succeeds.
    """

    candidate_key: str = Field(min_length=1, max_length=240)
    sequence: int = Field(ge=1, le=50)
    allocated_seconds: int = Field(ge=30, le=7_200)
    follow_up_budget: int = Field(ge=0, le=10)
    selection_reason: str = Field(min_length=1, max_length=2_000)


class PlannerDraft(ApiModel):
    """Schema-only planner output; source grounding is checked by the agent."""

    questions: list[PlannerQuestionDraft] = Field(min_length=1, max_length=50)
    rationale: str = Field(min_length=1, max_length=10_000)
    capability_coverage: list[str] = Field(default_factory=list, max_length=100)


class InterviewConfigPublic(EntityPublic):
    company_id: uuid.UUID
    round_profile_id: uuid.UUID
    resume_id: uuid.UUID | None
    role_name: str
    duration_minutes: int
    target_question_count: int
    question_bank_ids: list
    source_weights: dict
    preferences: dict


class PlanQuestionPublic(EntityPublic):
    sequence: int
    question_id: uuid.UUID | None
    source_type: SourceType
    source_ref: dict
    prompt_snapshot: str
    capability_tags: list
    allocated_seconds: int
    follow_up_budget: int
    selection_reason: str


class InterviewPlanPublic(EntityPublic):
    config_id: uuid.UUID
    style_pack_id: uuid.UUID
    status: PlanStatus
    total_minutes: int
    plan_snapshot: dict
    rationale: str | None
    frozen_at: datetime | None
    config: InterviewConfigPublic
    questions: list[PlanQuestionPublic]


class PlanJobPublic(EntityPublic):
    status: JobStatus
    progress: float
    result: dict
    error_code: str | None
    error_message: str | None
    attempts: int
    max_attempts: int


class InterviewPlanCreateResult(ApiModel):
    plan: InterviewPlanPublic
    job: PlanJobPublic
