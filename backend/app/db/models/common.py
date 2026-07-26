import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class EntityBase(SQLModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        nullable=True,
    )
    version: int = Field(default=1, ge=1, nullable=False)

    def touch(self, *, at: datetime | None = None) -> None:
        self.updated_at = at or utc_now()
        self.version += 1

    def soft_delete(self, *, at: datetime | None = None) -> None:
        deleted_at = at or utc_now()
        self.deleted_at = deleted_at
        self.touch(at=deleted_at)


class QuestionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SessionStatus(StrEnum):
    CONFIGURING = "configuring"
    PLANNING = "planning"
    READY = "ready"
    INTERVIEWING = "interviewing"
    PAUSED = "paused"
    COMPLETING = "completing"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvaluationAnchor(StrEnum):
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    INSUFFICIENT = "insufficient"
    PARTIAL = "partial"
    SOLID = "solid"
    STRONG = "strong"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Visibility(StrEnum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class ContentStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ResumeParseStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    READY = "ready"
    FAILED = "failed"


class QuestionType(StrEnum):
    OPEN_ENDED = "open_ended"
    PROJECT_DEEP_DIVE = "project_deep_dive"
    SYSTEM_DESIGN = "system_design"
    CODE_DISCUSSION = "code_discussion"
    SCENARIO = "scenario"


class Difficulty(StrEnum):
    FOUNDATIONAL = "foundational"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class SourceType(StrEnum):
    MANUAL = "manual"
    LINK_IMPORT = "link_import"
    STYLE_PACK = "style_pack"
    RESUME = "resume"
    REPORT = "report"
    GENERATED = "generated"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    FROZEN = "frozen"
    CANCELLED = "cancelled"


class AttachmentType(StrEnum):
    CODE = "code"
    AUDIO = "audio"
    TEXT = "text"


class SegmentStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    SUMMARY_FAILED = "summary_failed"


class SummaryValidationStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    FAILED = "failed"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MemoryType(StrEnum):
    PROJECT_FACT = "project_fact"
    STABLE_SKILL = "stable_skill"
    RECURRING_GAP = "recurring_gap"
    COMMUNICATION_PREFERENCE = "communication_preference"
    INTERVIEW_PREFERENCE = "interview_preference"
    PRACTICE_GOAL = "practice_goal"


class ConflictStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ProviderType(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC_COMPATIBLE = "anthropic_compatible"


class ModelRole(StrEnum):
    RESEARCHER = "researcher"
    VISION_RESEARCHER = "vision_researcher"
    PLANNER = "planner"
    CONTEXT_SUMMARIZER = "context_summarizer"
    INTERVIEWER = "interviewer"
    EVALUATOR = "evaluator"
    COACH = "coach"
    EMBEDDING = "embedding"
    TRANSCRIBER = "transcriber"


class ConnectionStatus(StrEnum):
    UNTESTED = "untested"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class EmbeddingProfileStatus(StrEnum):
    BUILDING = "building"
    ACTIVE = "active"
    FAILED = "failed"
    RETIRED = "retired"


class DiscoveryProviderType(StrEnum):
    TAVILY = "tavily"
    FIRECRAWL = "firecrawl"


class DiscoverySourceMode(StrEnum):
    SEARCH = "search"
    URLS = "urls"


class DiscoveryRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    NO_RESULTS = "no_results"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DiscoverySourceStatus(StrEnum):
    PENDING = "pending"
    FETCHED = "fetched"
    BLOCKED = "blocked"
    FAILED = "failed"


class DiscoveryCandidateStatus(StrEnum):
    PROPOSED = "proposed"
    SELECTED = "selected"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    IMPORTED = "imported"
    FAILED = "failed"


class DiscoveryImportStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CONFLICTED = "conflicted"


class EvaluationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(StrEnum):
    RESUME_PARSE = "resume_parse"
    COMPANY_RESEARCH = "company_research"
    QUESTION_DISCOVERY = "question_discovery"
    PLAN_GENERATION = "plan_generation"
    CONTEXT_SUMMARY = "context_summary"
    MEMORY_EXTRACTION = "memory_extraction"
    EMBEDDING_REINDEX = "embedding_reindex"
    INTERVIEW_EVALUATION = "interview_evaluation"
