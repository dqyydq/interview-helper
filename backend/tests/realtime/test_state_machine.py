import pytest

from app.api.errors import AppError
from app.db.models.common import SessionStatus
from app.realtime.state_machine import ensure_transition


def test_state_machine_accepts_only_declared_transitions() -> None:
    ensure_transition(SessionStatus.READY, SessionStatus.INTERVIEWING)
    ensure_transition(SessionStatus.INTERVIEWING, SessionStatus.PAUSED)
    ensure_transition(SessionStatus.PAUSED, SessionStatus.INTERVIEWING)

    with pytest.raises(AppError) as error:
        ensure_transition(SessionStatus.READY, SessionStatus.COMPLETED)

    assert error.value.code == "session_transition_invalid"
    assert error.value.status_code == 409
