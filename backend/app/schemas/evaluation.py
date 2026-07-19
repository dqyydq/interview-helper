import uuid
from datetime import datetime

from pydantic import Field, model_validator

from app.db.models.common import EvaluationAnchor, EvaluationStatus, JobStatus
from app.schemas.common import ApiModel, EntityPublic


class EvidenceReference(ApiModel):
    """A traceable claim backed by one immutable interview message."""

    message_id: uuid.UUID
    claim: str = Field(min_length=1, max_length=1_000)


class PracticeAction(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=2_000)
    success_criteria: str = Field(min_length=1, max_length=1_000)
    priority: int = Field(default=2, ge=1, le=3)


class QuestionEvaluationDraft(ApiModel):
    plan_question_id: uuid.UUID
    anchor: EvaluationAnchor
    summary: str = Field(min_length=1, max_length=10_000)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)
    actions: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_evidence_for_a_conclusion(self) -> "QuestionEvaluationDraft":
        if not self.evidence:
            self.anchor = EvaluationAnchor.EVIDENCE_INSUFFICIENT
            self.confidence = min(self.confidence, 0.25)
        if self.anchor == EvaluationAnchor.EVIDENCE_INSUFFICIENT:
            self.confidence = min(self.confidence, 0.25)
        return self


class DimensionEvaluationDraft(ApiModel):
    dimension: str = Field(min_length=1, max_length=120)
    anchor: EvaluationAnchor
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=12)
    action: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_evidence_for_a_conclusion(self) -> "DimensionEvaluationDraft":
        if not self.evidence:
            self.anchor = EvaluationAnchor.EVIDENCE_INSUFFICIENT
            self.confidence = min(self.confidence, 0.25)
        if self.anchor == EvaluationAnchor.EVIDENCE_INSUFFICIENT:
            self.confidence = min(self.confidence, 0.25)
        return self


class EvaluationDraft(ApiModel):
    overall_anchor: EvaluationAnchor
    overview: str = Field(min_length=1, max_length=20_000)
    strengths: list[str] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)
    action_plan: list[PracticeAction] = Field(default_factory=list, max_length=12)
    questions: list[QuestionEvaluationDraft] = Field(min_length=1, max_length=50)
    dimensions: list[DimensionEvaluationDraft] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def prevent_an_unsupported_overall_conclusion(self) -> "EvaluationDraft":
        supported = [
            item
            for item in self.questions
            if item.evidence and item.anchor != EvaluationAnchor.EVIDENCE_INSUFFICIENT
        ]
        if not supported:
            self.overall_anchor = EvaluationAnchor.EVIDENCE_INSUFFICIENT
        return self


class EvaluationJobPublic(EntityPublic):
    status: JobStatus
    progress: float
    result: dict
    error_code: str | None
    error_message: str | None


class EvidenceMessagePublic(ApiModel):
    id: uuid.UUID
    plan_question_id: uuid.UUID | None
    sequence: int
    content: str


class QuestionEvaluationPublic(EntityPublic):
    plan_question_id: uuid.UUID
    question_sequence: int
    question_prompt: str
    anchor: EvaluationAnchor
    summary: str | None
    evidence: list[EvidenceReference]
    gaps: list[str]
    actions: list[str]
    confidence: float


class DimensionEvaluationPublic(EntityPublic):
    dimension: str
    anchor: EvaluationAnchor
    evidence: list[EvidenceReference]
    gaps: list[str]
    action: str | None
    confidence: float


class EvaluationReportPublic(EntityPublic):
    session_id: uuid.UUID
    status: EvaluationStatus
    overall_anchor: EvaluationAnchor
    overview: str | None
    strengths: list[str]
    gaps: list[str]
    action_plan: list[PracticeAction]
    trend_comparison: dict
    completed_at: datetime | None = None
    questions: list[QuestionEvaluationPublic]
    dimensions: list[DimensionEvaluationPublic]
    evidence_messages: list[EvidenceMessagePublic]
    job: EvaluationJobPublic | None = None


class ReportListItem(ApiModel):
    report_id: uuid.UUID
    session_id: uuid.UUID
    status: EvaluationStatus
    overall_anchor: EvaluationAnchor
    overview: str | None
    created_at: datetime
    updated_at: datetime
    company_name: str
    round_name: str
    role_name: str


class CoachModeRequest(ApiModel):
    mode: str = Field(pattern="^(explain|rewrite|practice)$")
    question_evaluation_id: uuid.UUID | None = None
    focus: str | None = Field(default=None, max_length=1_000)


class CoachResponse(ApiModel):
    mode: str
    title: str
    explanation: str
    original_answer: str | None = None
    suggested_answer: str | None = None
    practice_prompts: list[str] = Field(default_factory=list)
    source_message_ids: list[uuid.UUID] = Field(default_factory=list)
