import uuid
from pathlib import Path

import pytest

from app.core import security


def test_upload_paths_are_random_and_confined_to_profile_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile_id = uuid.uuid4()
    monkeypatch.setattr(security.settings, "upload_dir", tmp_path / "uploads")

    first = security.isolated_upload_path(profile_id, ".pdf")
    second = security.isolated_upload_path(profile_id, ".pdf")

    assert first != second
    assert first.parent.name == profile_id.hex
    assert first.suffix == ".pdf"
    assert security.validated_existing_upload_path(first, profile_id) == first
    with pytest.raises(ValueError):
        security.validated_existing_upload_path(tmp_path / "outside.pdf", profile_id)
    with pytest.raises(ValueError):
        security.isolated_upload_path(profile_id, "../../exe")


def test_sliding_window_rate_limiter_recovers_after_window() -> None:
    limiter = security.SlidingWindowRateLimiter(max_events=2, window_seconds=10)

    assert limiter.allow("client", now=0)
    assert limiter.allow("client", now=1)
    assert not limiter.allow("client", now=2)
    assert limiter.allow("client", now=11)


def test_sliding_window_rate_limiter_bounds_untrusted_key_cardinality() -> None:
    limiter = security.SlidingWindowRateLimiter(
        max_events=2,
        window_seconds=10,
        max_keys=2,
    )

    assert limiter.allow("first", now=0)
    assert limiter.allow("second", now=0)
    assert not limiter.allow("attacker-controlled-third-key", now=1)
    assert set(limiter._events) == {"first", "second"}

    # Saturating capacity does not block an already admitted key; it is still
    # governed by its own per-window event limit.
    assert limiter.allow("first", now=1)


def test_sliding_window_rate_limiter_reclaims_expired_buckets_before_admission() -> None:
    limiter = security.SlidingWindowRateLimiter(
        max_events=1,
        window_seconds=10,
        max_keys=2,
    )

    assert limiter.allow("first", now=0)
    assert limiter.allow("second", now=0)
    assert limiter.allow("new-client", now=10)
    assert set(limiter._events) == {"new-client"}


@pytest.mark.parametrize(
    ("max_events", "window_seconds", "max_keys"),
    [(0, 1, 1), (1, 0, 1), (1, 1, 0)],
)
def test_sliding_window_rate_limiter_rejects_invalid_limits(
    max_events: int,
    window_seconds: float,
    max_keys: int,
) -> None:
    with pytest.raises(ValueError):
        security.SlidingWindowRateLimiter(
            max_events=max_events,
            window_seconds=window_seconds,
            max_keys=max_keys,
        )


@pytest.mark.asyncio
async def test_in_flight_answer_registry_serializes_answers_per_session() -> None:
    registry = security.InFlightAnswerRegistry()
    session_id = uuid.uuid4()

    assert await registry.acquire(session_id)
    assert not await registry.acquire(session_id)
    await registry.release(session_id)
    assert await registry.acquire(session_id)
