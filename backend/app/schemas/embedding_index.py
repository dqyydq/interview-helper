"""Public, privacy-safe status for the durable embedding index.

The index may contain resume and memory-derived text, but this transport model
must never expose source text, vectors, provider payloads, or credentials.  It
only reports the immutable vector-space contract and coarse job progress needed
by the settings screen.
"""

from datetime import datetime

from pydantic import Field

from app.db.models.common import EmbeddingProfileStatus, JobStatus
from app.schemas.common import ApiModel, EntityPublic


class EmbeddingProfilePublic(EntityPublic):
    """A safe subset of one immutable embedding-profile snapshot."""

    target_kind: str = Field(pattern="^(model_connection|local_capability)$")
    model_name: str
    model_revision: str
    vector_dimensions: int | None = Field(default=None, ge=1, le=2_000)
    normalized: bool
    distance_metric: str
    status: EmbeddingProfileStatus
    activated_at: datetime | None
    failed_at: datetime | None
    failure_code: str | None
    failure_summary: str | None


class EmbeddingIndexJobPublic(EntityPublic):
    """Whitelisted progress fields from an embedding-reindex job."""

    status: JobStatus
    progress: float = Field(ge=0.0, le=1.0)
    phase: str = Field(min_length=1, max_length=80)
    memory_scanned: int = Field(default=0, ge=0)
    memory_embeddings: int = Field(default=0, ge=0)
    plan_question_scanned: int = Field(default=0, ge=0)
    plan_question_embeddings: int = Field(default=0, ge=0)
    vector_dimensions: int | None = Field(default=None, ge=1, le=2_000)
    error_code: str | None
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    available_at: datetime


class EmbeddingIndexStatusPublic(ApiModel):
    """The currently served index and any non-blocking rebuild in progress."""

    active_profile: EmbeddingProfilePublic | None
    building_profile: EmbeddingProfilePublic | None
    latest_failed_profile: EmbeddingProfilePublic | None
    job: EmbeddingIndexJobPublic | None
    interview_active: bool


class EmbeddingIndexRebuildResult(ApiModel):
    """Result of explicitly queuing (or replaying) a background rebuild."""

    embedding_profile: EmbeddingProfilePublic
    job: EmbeddingIndexJobPublic
    created: bool
