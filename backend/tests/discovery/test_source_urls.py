from app.discovery.source_urls import verified_source_url


def test_verified_source_url_prefers_final_url_and_retains_normalized_fallback() -> None:
    assert (
        verified_source_url(
            normalized_url="https://search.example.test/outbound/question",
            final_url="https://notes.example.test/question",
        )
        == "https://notes.example.test/question"
    )
    assert (
        verified_source_url(
            normalized_url="https://search.example.test/outbound/question",
            final_url=None,
        )
        == "https://search.example.test/outbound/question"
    )
