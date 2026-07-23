import logging
import sys

import structlog

from app.core.config import settings
from app.core.redaction import redact_event


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        stream=sys.stdout,
        force=True,
    )
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_event,
    ]
    renderer = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.environment == "local"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
