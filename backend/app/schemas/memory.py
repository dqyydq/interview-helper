import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.common import ConflictStatus, MemoryStatus, MemoryType
from app.schemas.common import ApiModel, EntityPublic


class MemorySourcePublic(EntityPublic):
    session_id: uuid.UUID | None
    message_id: uuid.UUID | None
    source_type: str
    evidence_excerpt: str | None
    observed_at: datetime


class MemoryConflictPublic(EntityPublic):
    memory_id: uuid.UUID
    conflicting_memory_id: uuid.UUID
    status: ConflictStatus
    resolution: str | None
    resolved_at: datetime | None


class MemoryItemPublic(EntityPublic):
    memory_type: MemoryType
    canonical_key: str
    memory_version: int
    content: str
    structured_value: dict
    status: MemoryStatus
    confidence: float
    first_observed_at: datetime
    last_verified_at: datetime | None
    last_used_at: datetime | None
    expires_at: datetime | None
    pinned: bool
    sources: list[MemorySourcePublic]
    open_conflicts: list[MemoryConflictPublic]


class MemoryUpdate(ApiModel):
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    structured_value: dict | None = None


class MemoryPinUpdate(ApiModel):
    pinned: bool


class MemorySettingsPublic(ApiModel):
    memory_enabled: bool


class MemorySettingsUpdate(ApiModel):
    memory_enabled: bool


class ConflictResolveRequest(ApiModel):
    winning_memory_id: uuid.UUID


class MemoryPreviewItem(ApiModel):
    id: uuid.UUID
    memory_type: MemoryType
    content: str
    pinned: bool
    reason: str


class MemoryPreviewPublic(ApiModel):
    enabled: bool
    items: list[MemoryPreviewItem]


class ForgetSessionResult(ApiModel):
    session_id: uuid.UUID
    removed_sources: int = Field(ge=0)
    deleted_memories: int = Field(ge=0)
    retained_memories: int = Field(ge=0)
