import uuid

from sqlalchemy import Column, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import EntityBase, EvaluationAnchor, EvaluationStatus


class EvaluationReport(EntityBase, table=True):
    __tablename__ = "evaluation_reports"

    session_id: uuid.UUID = Field(
        foreign_key="interview_sessions.id",
        ondelete="CASCADE",
        unique=True,
        index=True,
    )
    status: EvaluationStatus = Field(
        default=EvaluationStatus.PENDING,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    overall_anchor: EvaluationAnchor = Field(
        default=EvaluationAnchor.EVIDENCE_INSUFFICIENT,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    overview: str | None = Field(default=None, max_length=20_000, sa_type=Text)
    strengths: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    gaps: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    action_plan: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    trend_comparison: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    evaluator_model_connection_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="model_connections.id",
        ondelete="SET NULL",
        index=True,
    )
    failure_code: str | None = Field(default=None, max_length=120)


class QuestionEvaluation(EntityBase, table=True):
    __tablename__ = "question_evaluations"
    __table_args__ = (
        UniqueConstraint("report_id", "plan_question_id", name="uq_question_evaluation"),
    )

    report_id: uuid.UUID = Field(
        foreign_key="evaluation_reports.id",
        ondelete="CASCADE",
        index=True,
    )
    plan_question_id: uuid.UUID = Field(
        foreign_key="plan_questions.id",
        ondelete="RESTRICT",
        index=True,
    )
    anchor: EvaluationAnchor = Field(
        default=EvaluationAnchor.EVIDENCE_INSUFFICIENT,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    summary: str | None = Field(default=None, max_length=10_000, sa_type=Text)
    evidence: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    gaps: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    actions: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DimensionEvaluation(EntityBase, table=True):
    __tablename__ = "dimension_evaluations"
    __table_args__ = (UniqueConstraint("report_id", "dimension", name="uq_dimension_evaluation"),)

    report_id: uuid.UUID = Field(
        foreign_key="evaluation_reports.id",
        ondelete="CASCADE",
        index=True,
    )
    dimension: str = Field(min_length=1, max_length=120, index=True)
    anchor: EvaluationAnchor = Field(
        default=EvaluationAnchor.EVIDENCE_INSUFFICIENT,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    evidence: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    gaps: list = Field(default_factory=list, sa_type=JSONB, nullable=False)
    action: str | None = Field(default=None, max_length=4_000, sa_type=Text)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
