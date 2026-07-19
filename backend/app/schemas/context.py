import uuid

from app.db.models.common import ModelRole
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


class ContextDiagnosticsPublic(ApiModel):
    session_id: uuid.UUID
    current_state: dict
    snapshots: list[ContextSnapshotPublic]
