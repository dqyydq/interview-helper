from app.core.redaction import redact_event, redact_value, sanitize_text


def test_recursive_redaction_removes_secrets_and_answer_content() -> None:
    payload = redact_value(
        {
            "api_key": "sk-private",
            "authorization": "Bearer private-token",
            "answer_content": "候选人的完整回答",
            "nested": {"password": "hidden", "status": "ok"},
        }
    )

    rendered = str(payload)
    assert "sk-private" not in rendered
    assert "private-token" not in rendered
    assert "候选人的完整回答" not in rendered
    assert payload["nested"]["status"] == "ok"
    assert "sha256=" in payload["answer_content"]


def test_inline_secret_sanitizer_handles_bearer_and_assignments() -> None:
    text = sanitize_text("Authorization: Bearer abc.def api_key=sk-live password:open")

    assert "abc.def" not in text
    assert "sk-live" not in text
    assert "password:open" not in text


def test_traceback_event_never_renders_candidate_content_or_secret_values() -> None:
    candidate_answer = "The candidate's private answer contains a production credential."
    secret = "sk-private-live-token"
    try:
        raise RuntimeError(f"{candidate_answer} api_key={secret}")
    except RuntimeError as exc:
        payload = redact_event(
            object(),
            "error",
            {"answer_content": candidate_answer, "exc_info": (type(exc), exc, exc.__traceback__)},
        )

    rendered = str(payload)
    assert candidate_answer not in rendered
    assert secret not in rendered
    assert "RuntimeError" in payload["exc_info"]
