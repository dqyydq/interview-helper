import ipaddress

import pytest

from app.discovery.url_policy import (
    DomainPolicy,
    URLPolicy,
    URLPolicyError,
    validate_discovery_url,
)


def public_dns(_: str) -> tuple[str, str]:
    return ("8.8.8.8", "2001:4860:4860::8888")


def test_https_url_is_canonicalised_and_checked_against_all_dns_records() -> None:
    policy = URLPolicy(
        domain_policy=DomainPolicy(allow_domains=("example.cn",)),
        dns_resolver=public_dns,
    )

    validated = policy.validate("HTTPS://Sub.Example.CN:443/path?topic=llm#ignored")

    assert validated.normalized_url == "https://sub.example.cn/path?topic=llm"
    assert validated.hostname == "sub.example.cn"
    assert validated.port == 443
    assert validated.resolved_addresses == ("8.8.8.8", "2001:4860:4860::8888")
    assert validated.matched_allow_domain == "example.cn"


def test_http_is_only_allowed_when_local_development_gate_is_explicitly_open() -> None:
    with pytest.raises(URLPolicyError) as exc_info:
        validate_discovery_url("http://source.example.cn", dns_resolver=public_dns)
    assert exc_info.value.code == "url_http_not_allowed"

    validated = validate_discovery_url(
        "http://source.example.cn:80/articles",
        allow_http_local=True,
        dns_resolver=public_dns,
    )
    assert validated.normalized_url == "http://source.example.cn/articles"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://source.example.cn", "url_scheme_not_allowed"),
        ("https://user:secret@source.example.cn", "url_credentials_not_allowed"),
        ("https://@source.example.cn", "url_credentials_not_allowed"),
        ("https://source.example.cn:8443", "url_port_not_allowed"),
        ("https://sub.localhost", "url_host_not_allowed"),
        ("https://localhost", "url_host_not_allowed"),
        ("https://printer", "url_host_not_allowed"),
        ("https://127.0.0.1", "url_ip_literal_not_allowed"),
        ("https://[::1]", "url_ip_literal_not_allowed"),
        ("https://0177.0.0.1", "url_ip_literal_not_allowed"),
    ],
)
def test_unsafe_url_forms_are_rejected_before_dns(
    url: str,
    code: str,
) -> None:
    resolver_called = False

    def resolver(_: str) -> tuple[str, ...]:
        nonlocal resolver_called
        resolver_called = True
        return ("8.8.8.8",)

    with pytest.raises(URLPolicyError) as exc_info:
        validate_discovery_url(url, dns_resolver=resolver)

    assert exc_info.value.code == code
    assert not resolver_called


def test_idn_host_and_domain_rules_use_the_same_idna_canonical_form() -> None:
    resolver_hosts: list[str] = []

    def resolver(hostname: str) -> tuple[str, ...]:
        resolver_hosts.append(hostname)
        return ("8.8.8.8",)

    validated = validate_discovery_url(
        "https://B\u00dcCHER.Example.CN/interview",
        domain_policy=DomainPolicy(allow_domains=("xn--bcher-kva.example.cn",)),
        dns_resolver=resolver,
    )

    assert validated.hostname == "xn--bcher-kva.example.cn"
    assert validated.normalized_url == "https://xn--bcher-kva.example.cn/interview"
    assert resolver_hosts == ["xn--bcher-kva.example.cn"]


def test_domain_matching_is_boundary_aware_and_deny_rules_win() -> None:
    policy = DomainPolicy(
        allow_domains=("example.cn",),
        deny_domains=("blocked.example.cn",),
    )

    accepted = validate_discovery_url(
        "https://nested.example.cn/post",
        domain_policy=policy,
        dns_resolver=public_dns,
    )
    assert accepted.matched_allow_domain == "example.cn"

    with pytest.raises(URLPolicyError) as denied:
        validate_discovery_url(
            "https://blocked.example.cn/post",
            domain_policy=policy,
            dns_resolver=public_dns,
        )
    assert denied.value.code == "url_domain_denied"

    with pytest.raises(URLPolicyError) as suffix_bypass:
        validate_discovery_url(
            "https://notexample.cn/post",
            domain_policy=policy,
            dns_resolver=public_dns,
        )
    assert suffix_bypass.value.code == "url_domain_not_allowed"


@pytest.mark.parametrize(
    "resolved_addresses",
    [
        ("10.0.0.8",),
        ("127.0.0.1",),
        ("192.0.2.8",),
        ("224.0.0.8",),
        ("::1",),
        ("fc00::8",),
        ("8.8.8.8", "10.0.0.8"),
    ],
)
def test_any_non_public_dns_answer_rejects_the_url(resolved_addresses: tuple[str, ...]) -> None:
    with pytest.raises(URLPolicyError) as exc_info:
        validate_discovery_url(
            "https://source.example.cn",
            dns_resolver=lambda _: resolved_addresses,
        )
    assert exc_info.value.code == "url_dns_address_not_public"


def test_dns_resolution_failures_and_result_limits_fail_closed() -> None:
    with pytest.raises(URLPolicyError) as empty:
        validate_discovery_url("https://source.example.cn", dns_resolver=lambda _: ())
    assert empty.value.code == "url_dns_resolution_failed"

    with pytest.raises(URLPolicyError) as failure:
        validate_discovery_url(
            "https://source.example.cn",
            dns_resolver=lambda _: (_ for _ in ()).throw(OSError("resolver unavailable")),
        )
    assert failure.value.code == "url_dns_resolution_failed"

    with pytest.raises(URLPolicyError) as too_many:
        validate_discovery_url(
            "https://source.example.cn",
            dns_resolver=lambda _: tuple(f"8.8.8.{index}" for index in range(1, 18)),
        )
    assert too_many.value.code == "url_dns_result_limit_exceeded"


def test_policy_rejects_invalid_or_ambiguous_domain_rules() -> None:
    with pytest.raises(URLPolicyError) as invalid_rule:
        DomainPolicy(allow_domains=("*.example.cn",))
    assert invalid_rule.value.code == "url_domain_policy_invalid"

    with pytest.raises(URLPolicyError) as missing_required_allowlist:
        DomainPolicy(require_allowlist=True)
    assert missing_required_allowlist.value.code == "url_domain_policy_invalid"


def test_dns_resolver_may_return_ipaddress_instances() -> None:
    validated = validate_discovery_url(
        "https://source.example.cn",
        dns_resolver=lambda _: (ipaddress.ip_address("1.1.1.1"),),
    )
    assert validated.resolved_addresses == ("1.1.1.1",)
