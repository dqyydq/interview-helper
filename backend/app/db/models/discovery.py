import uuid
from datetime import datetime, timedelta

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import (
    ConnectionStatus,
    Difficulty,
    DiscoveryCandidateStatus,
    DiscoveryImportStatus,
    DiscoveryProviderType,
    DiscoveryRunStatus,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    EntityBase,
    QuestionType,
    utc_now,
)

DISCOVERY_RETENTION_DAYS = 30


def discovery_retention_expires_at() -> datetime:
    return utc_now() + timedelta(days=DISCOVERY_RETENTION_DAYS)


class DiscoveryConnector(EntityBase, table=True):
    """A profile-scoped, encrypted connection to a public-source discovery provider."""

    __tablename__ = "discovery_connectors"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "id",
            name="uq_discovery_connector_profile_identity",
        ),
        Index(
            "uq_discovery_connector_active_name",
            "profile_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    name: str = Field(min_length=1, max_length=120)
    provider_type: DiscoveryProviderType = Field(
        sa_column=Column(String(32), nullable=False, index=True),
    )
    enabled: bool = Field(default=True, nullable=False, index=True)
    capabilities: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    configuration: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    configuration_version: int = Field(default=1, ge=1, nullable=False)
    encrypted_api_key: str | None = Field(default=None, max_length=16_000, sa_type=Text)
    status: ConnectionStatus = Field(
        default=ConnectionStatus.UNTESTED,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    last_tested_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    last_error_code: str | None = Field(default=None, max_length=120)
    last_error_summary: str | None = Field(default=None, max_length=2_000, sa_type=Text)
    last_used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class QuestionDiscoveryRun(EntityBase, table=True):
    """A bounded, auditable request to discover questions from public sources."""

    __tablename__ = "question_discovery_runs"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "id",
            name="uq_discovery_run_profile_identity",
        ),
        ForeignKeyConstraint(
            ["profile_id", "connector_id"],
            ["discovery_connectors.profile_id", "discovery_connectors.id"],
            name="fk_discovery_run_connector_profile",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_discovery_runs_profile_status_expires_at",
            "profile_id",
            "status",
            "expires_at",
        ),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    connector_id: uuid.UUID = Field(index=True)
    connector_configuration_version: int = Field(ge=1, nullable=False)
    initiated_by: str = Field(default="profile", min_length=1, max_length=120)
    source_mode: DiscoverySourceMode = Field(
        sa_column=Column(String(32), nullable=False, index=True),
    )
    query_snapshot: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    status: DiscoveryRunStatus = Field(
        default=DiscoveryRunStatus.QUEUED,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    stage: str | None = Field(default=None, max_length=32, index=True)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    source_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    failed_source_count: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=120)
    error_summary: str | None = Field(default=None, max_length=2_000, sa_type=Text)
    cancel_requested_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    expires_at: datetime = Field(
        default_factory=discovery_retention_expires_at,
        sa_type=DateTime(timezone=True),
        nullable=False,
        index=True,
    )


class QuestionDiscoverySource(EntityBase, table=True):
    """A compact, policy-checked source record retained only for discovery review."""

    __tablename__ = "question_discovery_sources"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "run_id",
            "id",
            name="uq_discovery_source_profile_run_identity",
        ),
        UniqueConstraint("run_id", "normalized_url", name="uq_discovery_source_run_url"),
        ForeignKeyConstraint(
            ["profile_id", "run_id"],
            ["question_discovery_runs.profile_id", "question_discovery_runs.id"],
            name="fk_discovery_source_run_profile",
            ondelete="CASCADE",
        ),
        Index(
            "ix_discovery_sources_profile_status_expires_at",
            "profile_id",
            "status",
            "expires_at",
        ),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    run_id: uuid.UUID = Field(index=True)
    raw_url: str | None = Field(default=None, max_length=2_048, sa_type=Text)
    normalized_url: str = Field(min_length=1, max_length=2_048, sa_type=Text)
    final_url: str | None = Field(default=None, max_length=2_048, sa_type=Text)
    title: str | None = Field(default=None, max_length=500)
    domain: str = Field(min_length=1, max_length=255, index=True)
    source_category: str = Field(default="unknown", min_length=1, max_length=48, index=True)
    status: DiscoverySourceStatus = Field(
        default=DiscoverySourceStatus.PENDING,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    fetched_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    content_hash: str | None = Field(default=None, max_length=128, index=True)
    excerpt: str | None = Field(default=None, max_length=16_384, sa_type=Text)
    attribution: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    policy_metadata: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    failure_code: str | None = Field(default=None, max_length=120)
    failure_summary: str | None = Field(default=None, max_length=2_000, sa_type=Text)
    expires_at: datetime = Field(
        default_factory=discovery_retention_expires_at,
        sa_type=DateTime(timezone=True),
        nullable=False,
        index=True,
    )


class QuestionDiscoveryCandidate(EntityBase, table=True):
    """A Researcher-curated question draft that remains outside formal question banks."""

    __tablename__ = "question_discovery_candidates"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "id",
            name="uq_discovery_candidate_profile_identity",
        ),
        UniqueConstraint(
            "profile_id",
            "run_id",
            "id",
            name="uq_discovery_candidate_profile_run_identity",
        ),
        UniqueConstraint(
            "profile_id",
            "run_id",
            "content_hash",
            name="uq_discovery_candidate_run_content",
        ),
        ForeignKeyConstraint(
            ["profile_id", "run_id"],
            ["question_discovery_runs.profile_id", "question_discovery_runs.id"],
            name="fk_discovery_candidate_run_profile",
            ondelete="CASCADE",
        ),
        Index(
            "ix_discovery_candidates_profile_status_expires_at",
            "profile_id",
            "status",
            "expires_at",
        ),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    run_id: uuid.UUID = Field(index=True)
    prompt: str = Field(min_length=1, max_length=50_000, sa_type=Text)
    question_type: QuestionType = Field(
        default=QuestionType.OPEN_ENDED,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    difficulty: Difficulty = Field(
        default=Difficulty.INTERMEDIATE,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    suggested_tags: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    suggested_roles: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    suggested_skills: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    applicable_companies: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    applicable_rounds: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    reference_points: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    follow_up_suggestions: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    matching_reason: str | None = Field(default=None, max_length=2_000, sa_type=Text)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    researcher_connection_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="model_connections.id",
        ondelete="SET NULL",
        index=True,
    )
    researcher_model_name: str | None = Field(default=None, max_length=255)
    schema_version: str = Field(default="v1", min_length=1, max_length=80)
    candidate_revision: int = Field(default=1, ge=1, nullable=False)
    content_hash: str = Field(min_length=32, max_length=128, index=True)
    similar_question_ids: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    status: DiscoveryCandidateStatus = Field(
        default=DiscoveryCandidateStatus.PROPOSED,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    import_count: int = Field(default=0, ge=0)
    failure_code: str | None = Field(default=None, max_length=120)
    failure_summary: str | None = Field(default=None, max_length=2_000, sa_type=Text)
    expires_at: datetime = Field(
        default_factory=discovery_retention_expires_at,
        sa_type=DateTime(timezone=True),
        nullable=False,
        index=True,
    )


class QuestionDiscoveryCandidateEvidence(EntityBase, table=True):
    """A short, source-grounded excerpt that justifies one candidate question."""

    __tablename__ = "question_discovery_candidate_evidence"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "candidate_id",
            "source_id",
            "evidence_hash",
            name="uq_discovery_candidate_evidence",
        ),
        ForeignKeyConstraint(
            ["profile_id", "run_id"],
            ["question_discovery_runs.profile_id", "question_discovery_runs.id"],
            name="fk_discovery_evidence_run_profile",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["profile_id", "run_id", "candidate_id"],
            [
                "question_discovery_candidates.profile_id",
                "question_discovery_candidates.run_id",
                "question_discovery_candidates.id",
            ],
            name="fk_discovery_evidence_candidate_run_profile",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["profile_id", "run_id", "source_id"],
            [
                "question_discovery_sources.profile_id",
                "question_discovery_sources.run_id",
                "question_discovery_sources.id",
            ],
            name="fk_discovery_evidence_source_run_profile",
            ondelete="CASCADE",
        ),
        Index(
            "ix_discovery_evidence_profile_expires_at",
            "profile_id",
            "expires_at",
        ),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    run_id: uuid.UUID = Field(index=True)
    candidate_id: uuid.UUID = Field(index=True)
    source_id: uuid.UUID = Field(index=True)
    excerpt: str = Field(min_length=1, max_length=4_000, sa_type=Text)
    source_locator: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_hash: str = Field(min_length=32, max_length=128)
    expires_at: datetime = Field(
        default_factory=discovery_retention_expires_at,
        sa_type=DateTime(timezone=True),
        nullable=False,
        index=True,
    )


class QuestionDiscoveryImport(EntityBase, table=True):
    """An idempotent import audit record for a candidate moved into a question bank."""

    __tablename__ = "question_discovery_imports"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "idempotency_key",
            "candidate_content_hash",
            name="uq_discovery_import_idempotency_candidate",
        ),
        ForeignKeyConstraint(
            ["profile_id", "candidate_id"],
            ["question_discovery_candidates.profile_id", "question_discovery_candidates.id"],
            name="fk_discovery_import_candidate_profile",
            ondelete="SET NULL (candidate_id)",
        ),
        Index(
            "uq_discovery_import_success_candidate_bank",
            "profile_id",
            "candidate_id",
            "bank_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
        Index("ix_discovery_imports_profile_status", "profile_id", "status"),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    candidate_id: uuid.UUID | None = Field(default=None, index=True)
    candidate_content_hash: str = Field(min_length=32, max_length=128, index=True)
    bank_id: uuid.UUID = Field(
        foreign_key="question_banks.id",
        ondelete="RESTRICT",
        index=True,
    )
    question_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="questions.id",
        ondelete="SET NULL",
        index=True,
    )
    idempotency_key: str = Field(min_length=1, max_length=255, index=True)
    request_hash: str = Field(min_length=32, max_length=128, index=True)
    candidate_revision: int = Field(ge=1, nullable=False)
    batch_id: uuid.UUID = Field(default_factory=uuid.uuid4, index=True)
    status: DiscoveryImportStatus = Field(
        default=DiscoveryImportStatus.PENDING,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    failure_code: str | None = Field(default=None, max_length=120)
    failure_summary: str | None = Field(default=None, max_length=2_000, sa_type=Text)
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class QuestionSourceProvenance(EntityBase, table=True):
    """The minimal, durable attribution snapshot attached to an imported question."""

    __tablename__ = "question_source_provenance"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "question_id",
            "normalized_url",
            "evidence_hash",
            name="uq_question_source_provenance_evidence",
        ),
        ForeignKeyConstraint(
            ["profile_id", "discovery_run_id"],
            ["question_discovery_runs.profile_id", "question_discovery_runs.id"],
            name="fk_provenance_run_profile",
            ondelete="SET NULL (discovery_run_id)",
        ),
        ForeignKeyConstraint(
            ["profile_id", "candidate_id"],
            ["question_discovery_candidates.profile_id", "question_discovery_candidates.id"],
            name="fk_provenance_candidate_profile",
            ondelete="SET NULL (candidate_id)",
        ),
        ForeignKeyConstraint(
            ["profile_id", "discovery_run_id", "candidate_id"],
            [
                "question_discovery_candidates.profile_id",
                "question_discovery_candidates.run_id",
                "question_discovery_candidates.id",
            ],
            name="fk_provenance_candidate_run_profile",
            ondelete="SET NULL (candidate_id)",
        ),
        Index("ix_question_source_provenance_profile_question", "profile_id", "question_id"),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    question_id: uuid.UUID = Field(
        foreign_key="questions.id",
        ondelete="CASCADE",
        index=True,
    )
    discovery_run_id: uuid.UUID | None = Field(default=None, index=True)
    candidate_id: uuid.UUID | None = Field(default=None, index=True)
    source_title: str = Field(min_length=1, max_length=500)
    normalized_url: str = Field(min_length=1, max_length=2_048, sa_type=Text)
    source_domain: str = Field(min_length=1, max_length=255, index=True)
    source_category: str = Field(min_length=1, max_length=48, index=True)
    fetched_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))
    excerpt: str = Field(min_length=1, max_length=4_000, sa_type=Text)
    evidence_hash: str = Field(min_length=32, max_length=128)
    attribution: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
