import uuid

from pydantic import Field

from app.db.models.common import ModelRole, SegmentStatus
from app.schemas.common import ApiModel, EntityPublic


class ContextSnapshotPublic(EntityPublic):
    agent_role: ModelRole
    model_connection_id: uuid.UUID | None
    prompt_schema_version: str
    included_refs: dict
    excluded_refs: list
    token_by_layer: dict
    count_method: str
    compaction_level: int
    provider_request_id: str | None
    input_tokens: int
    output_tokens: int


class SegmentDiagnostic(ApiModel):
    id: uuid.UUID
    plan_question_id: uuid.UUID | None
    sequence: int
    status: SegmentStatus
    start_message_sequence: int
    end_message_sequence: int | None
    token_count: int
    valid_summary_ids: list[uuid.UUID] = Field(default_factory=list)


class ContextDiagnosticsPublic(ApiModel):
    session_id: uuid.UUID
    current_state: dict
    snapshots: list[ContextSnapshotPublic]
    segments: list[SegmentDiagnostic]


class SummaryEvidence(ApiModel):
    text: str = Field(min_length=1, max_length=20_000)
    evidence_message_ids: list[uuid.UUID] = Field(min_length=1)


class ContextSummaryPayload(ApiModel):
    question_id: uuid.UUID
    capability_tags: list[str] = Field(default_factory=list)
    asked_question: str = Field(min_length=1, max_length=50_000)
    user_core_answer: SummaryEvidence | None = None
    explicit_claims: list[SummaryEvidence] = Field(default_factory=list)
    decisions_and_tradeoffs: list[SummaryEvidence] = Field(default_factory=list)
    caveats_and_failures: list[SummaryEvidence] = Field(default_factory=list)
    unresolved_points: list[SummaryEvidence] = Field(default_factory=list)
    attachment_refs: list[uuid.UUID] = Field(default_factory=list)
    evidence_message_ids: list[uuid.UUID] = Field(default_factory=list)
