import os
from pathlib import Path

import pytest
from alembic.config import Config
from dotenv import dotenv_values

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://interview_helper:local-development-only@127.0.0.1:5432/"
    "interview_helper_test"
)

# pydantic-settings reads the repository .env for the application, but pytest must
# choose its database before importing any application module.  Read the explicitly
# separate test URL here so a customized local port/credential is never silently
# replaced by the development database default.
configured_test_database_url = dotenv_values(REPOSITORY_ROOT / ".env").get(
    "INTERVIEW_HELPER_TEST_DATABASE_URL"
)
os.environ["INTERVIEW_HELPER_ENVIRONMENT"] = "test"
os.environ["INTERVIEW_HELPER_DATABASE_URL"] = (
    os.getenv(
        "INTERVIEW_HELPER_TEST_DATABASE_URL",
    )
    or configured_test_database_url
    or DEFAULT_TEST_DATABASE_URL
)


@pytest.fixture(scope="session", autouse=True)
def migrate_test_database():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")
    yield
