from app.api.errors import AppError
from app.db.models.common import SessionStatus

ALLOWED_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.READY: frozenset({SessionStatus.INTERVIEWING, SessionStatus.FAILED}),
    SessionStatus.INTERVIEWING: frozenset(
        {SessionStatus.PAUSED, SessionStatus.COMPLETING, SessionStatus.FAILED}
    ),
    SessionStatus.PAUSED: frozenset(
        {SessionStatus.INTERVIEWING, SessionStatus.COMPLETING, SessionStatus.FAILED}
    ),
    SessionStatus.COMPLETING: frozenset({SessionStatus.COMPLETED, SessionStatus.FAILED}),
    SessionStatus.COMPLETED: frozenset({SessionStatus.EVALUATING}),
    SessionStatus.EVALUATING: frozenset({SessionStatus.COMPLETED, SessionStatus.FAILED}),
    SessionStatus.CONFIGURING: frozenset({SessionStatus.PLANNING, SessionStatus.FAILED}),
    SessionStatus.PLANNING: frozenset({SessionStatus.READY, SessionStatus.FAILED}),
    SessionStatus.FAILED: frozenset(),
}


def ensure_transition(current: SessionStatus, target: SessionStatus) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise AppError(
            code="session_transition_invalid",
            message=f"会话不能从 {current.value} 切换到 {target.value}",
            status_code=409,
        )
