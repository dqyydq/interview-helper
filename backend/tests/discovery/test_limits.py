import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_discovery_limits_default_to_the_reviewed_hard_caps() -> None:
    configured = Settings()

    assert configured.discovery_request_timeout_seconds == 15.0
    assert configured.discovery_run_timeout_seconds == 60.0
    assert configured.discovery_max_urls == 5
    assert configured.discovery_max_search_results == 20
    assert configured.discovery_max_sources == 12
    assert configured.discovery_max_response_bytes == 1_048_576
    assert configured.discovery_max_source_characters == 16_384
    assert configured.discovery_researcher_input_tokens == 6_000
    assert configured.discovery_researcher_output_tokens == 2_048
    assert configured.discovery_retention_days == 30


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("discovery_request_timeout_seconds", 15.1),
        ("discovery_run_timeout_seconds", 60.1),
        ("discovery_max_urls", 6),
        ("discovery_max_search_results", 21),
        ("discovery_max_sources", 13),
        ("discovery_max_response_bytes", 1_048_577),
        ("discovery_max_source_characters", 16_385),
        ("discovery_researcher_input_tokens", 6_001),
        ("discovery_researcher_output_tokens", 2_049),
        ("discovery_max_concurrent_runs_per_profile", 5),
        ("discovery_retention_days", 31),
    ],
)
def test_discovery_limits_cannot_exceed_reviewed_caps(field_name: str, value: int | float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field_name: value})
