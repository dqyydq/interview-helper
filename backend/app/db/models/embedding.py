import uuid
from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field

from app.db.models.common import EmbeddingProfileStatus, EntityBase, utc_now


class EmbeddingProfile(EntityBase, table=True):
    """An immutable embedding configuration scoped to one user profile.

    A profile captures the model and retrieval-space contract used to create a
    set of vectors.  Status and diagnostics may change while the configuration
    fields remain a historical snapshot, so vectors from different dimensions
    or instructions are never combined accidentally.
    """

    __tablename__ = "embedding_profiles"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "id",
            name="uq_embedding_profile_profile_identity",
        ),
        CheckConstraint(
            "num_nonnulls(model_connection_id, local_capability_key) = 1",
            name="ck_embedding_profile_exactly_one_target",
        ),
        CheckConstraint(
            "local_capability_key IS NULL "
            "OR local_capability_key IN ('multilingual-e5-small', 'bge-m3')",
            name="ck_embedding_profile_local_capability",
        ),
        CheckConstraint(
            "vector_dimensions IS NULL OR vector_dimensions BETWEEN 1 AND 2000",
            name="ck_embedding_profile_vector_dimensions",
        ),
        CheckConstraint(
            "status <> 'active' OR vector_dimensions IS NOT NULL",
            name="ck_embedding_profile_active_dimensions",
        ),
        CheckConstraint(
            "distance_metric IN ('cosine', 'l2', 'inner_product')",
            name="ck_embedding_profile_distance_metric",
        ),
        Index(
            "uq_embedding_profile_active_for_profile",
            "profile_id",
            unique=True,
            postgresql_where=text("status = 'active' AND deleted_at IS NULL"),
        ),
        Index(
            "uq_embedding_profile_building_for_profile",
            "profile_id",
            unique=True,
            postgresql_where=text("status = 'building' AND deleted_at IS NULL"),
        ),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    model_connection_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="model_connections.id",
        ondelete="RESTRICT",
        index=True,
    )
    local_capability_key: str | None = Field(
        default=None,
        max_length=80,
        sa_column=Column(String(80), nullable=True),
    )
    target_fingerprint: str = Field(min_length=32, max_length=128, index=True)
    model_name: str = Field(min_length=1, max_length=255, index=True)
    model_revision: str = Field(default="unspecified", min_length=1, max_length=255)
    vector_dimensions: int | None = Field(default=None, ge=1, le=2_000)
    normalized: bool = Field(default=True, nullable=False)
    query_instruction: str = Field(default="", max_length=512, sa_type=Text)
    distance_metric: str = Field(default="cosine", min_length=1, max_length=32)
    status: EmbeddingProfileStatus = Field(
        default=EmbeddingProfileStatus.BUILDING,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    activated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    failed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    failure_code: str | None = Field(default=None, max_length=120)
    failure_summary: str | None = Field(default=None, max_length=2_000, sa_type=Text)


class MemoryEmbedding(EntityBase, table=True):
    """A profile-scoped vector for a long-term memory item.

    ``VECTOR()`` is deliberately unbounded: profile filtering prevents mixed
    dimensions, while allowing the user to migrate from E5 (384) to BGE-M3
    (1024) without a schema rewrite.  The first retrieval phase uses exact
    scans, so this table intentionally has no HNSW/IVFFlat index yet.
    """

    __tablename__ = "memory_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "embedding_profile_id"],
            ["embedding_profiles.profile_id", "embedding_profiles.id"],
            name="fk_memory_embedding_profile_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["profile_id", "memory_id"],
            ["memory_items.profile_id", "memory_items.id"],
            name="fk_memory_embedding_memory_tenant",
            ondelete="CASCADE",
        ),
        Index(
            "uq_memory_embedding_current_source",
            "profile_id",
            "embedding_profile_id",
            "memory_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_memory_embeddings_profile_scope",
            "profile_id",
            "embedding_profile_id",
            "company_id",
            "role_name",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    profile_id: uuid.UUID = Field(index=True)
    embedding_profile_id: uuid.UUID = Field(index=True)
    memory_id: uuid.UUID = Field(index=True)
    company_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="companies.id",
        ondelete="SET NULL",
        index=True,
    )
    role_name: str | None = Field(default=None, max_length=160, index=True)
    content_hash: str = Field(min_length=32, max_length=128, index=True)
    source_version: int = Field(default=1, ge=1)
    embedding: list[float] = Field(sa_column=Column(VECTOR(), nullable=False))
    embedded_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))


class PlanQuestionEmbedding(EntityBase, table=True):
    """A profile-scoped vector for an immutable plan-question snapshot."""

    __tablename__ = "plan_question_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "embedding_profile_id"],
            ["embedding_profiles.profile_id", "embedding_profiles.id"],
            name="fk_plan_question_embedding_profile_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["plan_id", "plan_question_id"],
            ["plan_questions.plan_id", "plan_questions.id"],
            name="fk_plan_question_embedding_source",
            ondelete="CASCADE",
        ),
        Index(
            "uq_plan_question_embedding_current_source",
            "profile_id",
            "embedding_profile_id",
            "plan_question_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_plan_question_embeddings_profile_scope",
            "profile_id",
            "embedding_profile_id",
            "company_id",
            "role_name",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    profile_id: uuid.UUID = Field(index=True)
    embedding_profile_id: uuid.UUID = Field(index=True)
    plan_id: uuid.UUID = Field(index=True)
    plan_question_id: uuid.UUID = Field(index=True)
    company_id: uuid.UUID = Field(
        foreign_key="companies.id",
        ondelete="RESTRICT",
        index=True,
    )
    role_name: str = Field(min_length=1, max_length=160, index=True)
    content_hash: str = Field(min_length=32, max_length=128, index=True)
    source_version: int = Field(default=1, ge=1)
    embedding: list[float] = Field(sa_column=Column(VECTOR(), nullable=False))
    embedded_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))
