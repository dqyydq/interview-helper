import os
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://interview_helper:local-development-only@localhost:5432/"
    "interview_helper_test"
)

os.environ["INTERVIEW_HELPER_ENVIRONMENT"] = "test"
os.environ["INTERVIEW_HELPER_DATABASE_URL"] = os.getenv(
    "INTERVIEW_HELPER_TEST_DATABASE_URL",
    DEFAULT_TEST_DATABASE_URL,
)


@pytest.fixture(scope="session", autouse=True)
def migrate_test_database():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")
    yield
