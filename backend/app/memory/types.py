import uuid
from dataclasses import dataclass, field

from app.db.models.common import MemoryStatus, MemoryType, ModelRole

EXPLICIT_ACTIVE_TYPES = {
    MemoryType.PROJECT_FACT,
    MemoryType.COMMUNICATION_PREFERENCE,
    MemoryType.INTERVIEW_PREFERENCE,
    MemoryType.PRACTICE_GOAL,
}

ROLE_MEMORY_TYPES: dict[ModelRole, set[MemoryType]] = {
    ModelRole.INTERVIEWER: {
        MemoryType.PROJECT_FACT,
        MemoryType.COMMUNICATION_PREFERENCE,
        MemoryType.INTERVIEW_PREFERENCE,
        MemoryType.PRACTICE_GOAL,
    },
    ModelRole.PLANNER: set(MemoryType),
    ModelRole.COACH: set(MemoryType),
    ModelRole.EVALUATOR: set(),
    ModelRole.CONTEXT_SUMMARIZER: set(),
    ModelRole.RESEARCHER: set(),
    ModelRole.EMBEDDING: set(),
}


@dataclass(frozen=True, slots=True)
class MemorySourceInput:
    session_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    source_type: str = "message"
    evidence_excerpt: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_type: MemoryType
    canonical_key: str
    content: str
    structured_value: dict = field(default_factory=dict)
    confidence: float = 0.5
    explicit_user_statement: bool = False
    source: MemorySourceInput = field(default_factory=MemorySourceInput)


def activation_status(
    memory_type: MemoryType,
    *,
    explicit_user_statement: bool,
    independent_session_count: int,
) -> MemoryStatus:
    if explicit_user_statement and memory_type in EXPLICIT_ACTIVE_TYPES:
        return MemoryStatus.ACTIVE
    if memory_type in {MemoryType.STABLE_SKILL, MemoryType.RECURRING_GAP}:
        return MemoryStatus.ACTIVE if independent_session_count >= 2 else MemoryStatus.PROPOSED
    return MemoryStatus.PROPOSED
