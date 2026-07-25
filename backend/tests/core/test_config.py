from app.core.config import Settings


def test_local_database_url_prefers_ipv4_loopback_for_docker() -> None:
    configured = Settings(
        environment="local",
        database_url=(
            "postgresql+asyncpg://interview_helper:local-development-only@"
            "localhost:5432/interview_helper"
        ),
    )

    assert configured.database_url.endswith("@127.0.0.1:5432/interview_helper")


def test_remote_database_url_keeps_its_explicit_host() -> None:
    configured = Settings(
        environment="production",
        database_url="postgresql+asyncpg://user:secret@localhost:5432/interview_helper",
    )

    assert configured.database_url.endswith("@localhost:5432/interview_helper")
