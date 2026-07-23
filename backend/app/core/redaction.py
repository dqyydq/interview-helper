import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_KEY = re.compile(
    r"(^|_)(api_?key|authorization|cookie|password|secret|token|encrypted|credential)($|_)",
    re.IGNORECASE,
)
CONTENT_KEY = re.compile(
    r"(^|_)(answer|content|transcript|prompt|parsed_text|resume_text|audio)($|_)",
    re.IGNORECASE,
)
EXCEPTION_KEY = re.compile(
    r"(^|_)(exc_info|exception|traceback|stack_trace)($|_)",
    re.IGNORECASE,
)
BEARER_VALUE = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
INLINE_SECRET = re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+")


def _content_marker(value: object) -> str:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("utf-8", errors="replace")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return f"<redacted-content bytes={len(raw)} sha256={digest}>"


def _exception_marker(value: object) -> str:
    """Keep an operational signal without rendering exception text or traceback frames."""
    if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[1], BaseException):
        return f"<redacted-exception type={type(value[1]).__name__}>"
    if isinstance(value, BaseException):
        return f"<redacted-exception type={type(value).__name__}>"
    return "<redacted-exception>"


def sanitize_text(value: str) -> str:
    redacted = BEARER_VALUE.sub("Bearer <redacted>", value)
    return INLINE_SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    if key and SENSITIVE_KEY.search(key):
        return "<redacted-secret>"
    if key and EXCEPTION_KEY.search(key):
        return _exception_marker(value)
    if key and CONTENT_KEY.search(key):
        return _content_marker(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item, key=str(item_key)) for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, bytes):
        return _content_marker(value)
    return value


def redact_event(_: object, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact_value(event_dict)
