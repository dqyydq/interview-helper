"""Fail-closed validation for user-supplied discovery URLs.

The application never fetches these URLs itself: a reviewed discovery connector does
that work.  This module still rejects obvious SSRF targets before an URL reaches that
connector, and it gives every caller the same canonical domain-policy semantics.

DNS is deliberately injected.  Production callers use the default resolver, while
tests and deployments with a trusted resolver can provide their own implementation.
The resolver is a pre-flight guard, not a replacement for the connector's redirect
and network-isolation controls.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2_048
MAX_DNS_ADDRESSES = 16

_DEFAULT_PORTS = {"http": 80, "https": 443}
_DOT_TRANSLATION = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})
_ASCII_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_AMBIGUOUS_NUMERIC_LABEL = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)$", re.IGNORECASE)

# These names are special-use, local-network, or documentation-only names.  They are
# blocked before DNS so a malicious or misconfigured resolver cannot make them appear
# publicly routable.
_BLOCKED_EXACT_HOSTS = frozenset(
    {
        "localhost",
        "local",
        "invalid",
        "test",
        "example",
        "home.arpa",
        "example.com",
        "example.net",
        "example.org",
    }
)
_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".invalid",
    ".test",
    ".example",
    ".home.arpa",
    ".example.com",
    ".example.net",
    ".example.org",
)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
DNSResolver = Callable[[str], Iterable[str | IPAddress]]


_SAFE_MESSAGES = {
    "url_invalid": "链接格式无效，请提供完整的公开网页链接。",
    "url_too_long": "链接长度超过安全限制。",
    "url_scheme_not_allowed": "仅允许 HTTPS 链接。",
    "url_http_not_allowed": "当前环境不允许 HTTP 链接。",
    "url_credentials_not_allowed": "链接中不能包含登录凭据。",
    "url_port_not_allowed": "链接端口不符合安全策略。",
    "url_host_not_allowed": "链接主机不符合安全策略。",
    "url_ip_literal_not_allowed": "不允许使用 IP 地址形式的链接。",
    "url_domain_denied": "该域名已被来源策略排除。",
    "url_domain_not_allowed": "该域名不在当前允许的来源范围内。",
    "url_dns_resolution_failed": "无法安全解析该链接的公开地址。",
    "url_dns_result_limit_exceeded": "该链接的地址解析结果超过安全限制。",
    "url_dns_address_not_public": "该链接未解析到可公开路由的地址。",
    "url_domain_policy_invalid": "域名策略配置无效。",
}


class URLPolicyError(ValueError):
    """A stable, safe-to-display policy error.

    The raw URL, resolver diagnostics, and provider details intentionally never appear
    in the exception message.  API layers may return ``code`` and ``message`` without
    leaking internal resolution details.
    """

    def __init__(self, code: str) -> None:
        if code not in _SAFE_MESSAGES:
            code = "url_invalid"
        super().__init__(_SAFE_MESSAGES[code])
        self.code = code
        self.message = _SAFE_MESSAGES[code]


@dataclass(frozen=True, slots=True)
class DomainPolicy:
    """Effective domain filters for one discovery run.

    ``allow_domains`` is normally the selected preset plus any user-added entries.  An
    empty allow-list means full-web mode.  If ``require_allowlist`` is true, an empty
    allow-list is rejected as a configuration error instead of quietly becoming full
    web access.  Deny rules always take precedence over allow rules.
    """

    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()
    require_allowlist: bool = False

    def __post_init__(self) -> None:
        allow_domains = _normalise_policy_domains(self.allow_domains)
        deny_domains = _normalise_policy_domains(self.deny_domains)
        if self.require_allowlist and not allow_domains:
            raise URLPolicyError("url_domain_policy_invalid")
        object.__setattr__(self, "allow_domains", allow_domains)
        object.__setattr__(self, "deny_domains", deny_domains)

    def validate(self, hostname: str) -> tuple[str | None, str | None]:
        """Return matched allow/deny rules, or raise on a denied/disallowed host."""

        denied_by = _first_matching_rule(hostname, self.deny_domains)
        if denied_by is not None:
            raise URLPolicyError("url_domain_denied")

        allowed_by = _first_matching_rule(hostname, self.allow_domains)
        if self.allow_domains and allowed_by is None:
            raise URLPolicyError("url_domain_not_allowed")
        return allowed_by, denied_by


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    """The only URL form a discovery connector is allowed to receive."""

    normalized_url: str
    hostname: str
    scheme: Literal["http", "https"]
    port: int
    resolved_addresses: tuple[str, ...]
    matched_allow_domain: str | None = None

    @property
    def canonical_url(self) -> str:
        """Alias used by source-provenance and connector code."""

        return self.normalized_url

    @property
    def url(self) -> str:
        """Short alias for connector request construction."""

        return self.normalized_url


@dataclass(frozen=True, slots=True)
class URLPolicy:
    """Reusable URL-policy object with an injectable resolver.

    ``allow_http_local`` must only be set by the application when both conditions hold:
    it is a local-development environment and its explicit local HTTP setting is on.
    It never relaxes hostname, domain, or DNS checks.
    """

    domain_policy: DomainPolicy = field(default_factory=DomainPolicy)
    allow_http_local: bool = False
    dns_resolver: DNSResolver = field(default_factory=lambda: default_dns_resolver)
    max_url_length: int = MAX_URL_LENGTH
    max_dns_addresses: int = MAX_DNS_ADDRESSES

    def __post_init__(self) -> None:
        _validate_limits(
            max_url_length=self.max_url_length,
            max_dns_addresses=self.max_dns_addresses,
        )

    def validate(self, raw_url: str) -> ValidatedURL:
        return validate_discovery_url(
            raw_url,
            domain_policy=self.domain_policy,
            allow_http_local=self.allow_http_local,
            dns_resolver=self.dns_resolver,
            max_url_length=self.max_url_length,
            max_dns_addresses=self.max_dns_addresses,
        )


def validate_discovery_url(
    raw_url: str,
    *,
    domain_policy: DomainPolicy | None = None,
    allow_http_local: bool = False,
    dns_resolver: DNSResolver | None = None,
    max_url_length: int = MAX_URL_LENGTH,
    max_dns_addresses: int = MAX_DNS_ADDRESSES,
) -> ValidatedURL:
    """Validate and canonicalise one user-supplied public-web URL.

    A caller must pass only the returned ``normalized_url`` to an extract connector.
    The function intentionally performs no HTTP request and performs DNS only after
    all inexpensive syntax and domain-policy checks pass.
    """

    _validate_limits(
        max_url_length=max_url_length,
        max_dns_addresses=max_dns_addresses,
    )
    if not isinstance(raw_url, str):
        raise URLPolicyError("url_invalid")

    candidate = raw_url.strip()
    if not candidate or len(candidate) > max_url_length or _contains_control_character(candidate):
        raise URLPolicyError("url_too_long" if len(candidate) > max_url_length else "url_invalid")

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise URLPolicyError("url_invalid") from exc

    parsed_scheme = parsed.scheme.casefold()
    if parsed_scheme == "http":
        scheme: Literal["http", "https"] = "http"
    elif parsed_scheme == "https":
        scheme = "https"
    else:
        raise URLPolicyError("url_scheme_not_allowed")
    if scheme == "http" and not allow_http_local:
        raise URLPolicyError("url_http_not_allowed")
    if not parsed.netloc or not hostname or "\\" in parsed.netloc:
        raise URLPolicyError("url_invalid")
    if parsed.username is not None or parsed.password is not None:
        raise URLPolicyError("url_credentials_not_allowed")

    default_port = _DEFAULT_PORTS[scheme]
    effective_port = port if port is not None else default_port
    if effective_port != default_port:
        raise URLPolicyError("url_port_not_allowed")

    normalized_host = normalize_domain(hostname)
    if _is_system_blocked_host(normalized_host):
        raise URLPolicyError("url_host_not_allowed")

    effective_domain_policy = domain_policy or DomainPolicy()
    matched_allow_domain, _ = effective_domain_policy.validate(normalized_host)

    resolved_addresses = _resolve_public_addresses(
        normalized_host,
        dns_resolver=dns_resolver or default_dns_resolver,
        max_dns_addresses=max_dns_addresses,
    )

    netloc = normalized_host
    # An explicit default port is canonicalised away; non-default ports are rejected.
    normalized_url = urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))
    return ValidatedURL(
        normalized_url=normalized_url,
        hostname=normalized_host,
        scheme=scheme,
        port=effective_port,
        resolved_addresses=resolved_addresses,
        matched_allow_domain=matched_allow_domain,
    )


def normalize_domain(value: str) -> str:
    """Return a lower-case ASCII IDNA domain or fail closed.

    This helper is also used for user-provided allow/deny rules.  It accepts a trailing
    root dot and Unicode dot variants, but rejects IP literals, ambiguous numeric host
    forms, single-label names, and malformed IDNA labels.
    """

    if not isinstance(value, str):
        raise URLPolicyError("url_domain_policy_invalid")
    candidate = value.strip().translate(_DOT_TRANSLATION)
    if not candidate:
        raise URLPolicyError("url_host_not_allowed")
    if candidate.endswith("."):
        candidate = candidate[:-1]
    if not candidate or candidate.startswith(".") or ".." in candidate:
        raise URLPolicyError("url_host_not_allowed")
    if _contains_control_character(candidate):
        raise URLPolicyError("url_host_not_allowed")

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise URLPolicyError("url_ip_literal_not_allowed")

    try:
        normalized = candidate.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise URLPolicyError("url_host_not_allowed") from exc

    if len(normalized) > 253 or "." not in normalized:
        raise URLPolicyError("url_host_not_allowed")
    labels = normalized.split(".")
    if any(not _ASCII_DOMAIN_LABEL.fullmatch(label) for label in labels):
        raise URLPolicyError("url_host_not_allowed")
    if _is_ambiguous_numeric_host(labels):
        raise URLPolicyError("url_ip_literal_not_allowed")
    return normalized


def domain_matches(hostname: str, rule: str) -> bool:
    """Match an exact domain or a dotted subdomain boundary, never a raw suffix."""

    return hostname == rule or hostname.endswith(f".{rule}")


def default_dns_resolver(hostname: str) -> tuple[str, ...]:
    """Resolve both A and AAAA records through the platform resolver."""

    addresses: set[str] = set()
    for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
        sockaddr = result[4]
        if sockaddr:
            addresses.add(sockaddr[0])
    return tuple(sorted(addresses))


def _normalise_policy_domains(values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise URLPolicyError("url_domain_policy_invalid") from exc

    normalized: list[str] = []
    for value in iterator:
        if not isinstance(value, str):
            raise URLPolicyError("url_domain_policy_invalid")
        try:
            domain = normalize_domain(value)
        except URLPolicyError as exc:
            raise URLPolicyError("url_domain_policy_invalid") from exc
        if domain not in normalized:
            normalized.append(domain)
    return tuple(normalized)


def _first_matching_rule(hostname: str, rules: tuple[str, ...]) -> str | None:
    # Prefer the most specific matching rule so audit metadata is deterministic.
    matches = (rule for rule in rules if domain_matches(hostname, rule))
    return max(matches, key=len, default=None)


def _resolve_public_addresses(
    hostname: str,
    *,
    dns_resolver: DNSResolver,
    max_dns_addresses: int,
) -> tuple[str, ...]:
    try:
        values = _bounded_dns_values(dns_resolver(hostname), max_dns_addresses)
    except URLPolicyError:
        raise
    except Exception as exc:  # A resolver failure must not become a policy bypass.
        raise URLPolicyError("url_dns_resolution_failed") from exc

    if not values:
        raise URLPolicyError("url_dns_resolution_failed")

    addresses: list[str] = []
    for value in values:
        try:
            address = (
                value
                if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address))
                else ipaddress.ip_address(value)
            )
        except ValueError as exc:
            raise URLPolicyError("url_dns_resolution_failed") from exc
        if not _is_publicly_routable(address):
            raise URLPolicyError("url_dns_address_not_public")
        rendered = str(address)
        if rendered not in addresses:
            addresses.append(rendered)
    return tuple(addresses)


def _bounded_dns_values(
    values: Iterable[str | IPAddress],
    limit: int,
) -> tuple[str | IPAddress, ...]:
    iterator = iter(values)
    bounded: list[str | IPAddress] = []
    for _ in range(limit + 1):
        try:
            value = next(iterator)
        except StopIteration:
            break
        bounded.append(value)
    if len(bounded) > limit:
        raise URLPolicyError("url_dns_result_limit_exceeded")
    return tuple(bounded)


def _is_system_blocked_host(hostname: str) -> bool:
    return hostname in _BLOCKED_EXACT_HOSTS or hostname.endswith(_BLOCKED_HOST_SUFFIXES)


def _is_publicly_routable(address: IPAddress) -> bool:
    """Reject every special-use address class, including multicast.

    ``ipaddress.is_global`` alone intentionally treats multicast as global in some
    Python versions, so it is necessary but not sufficient for this boundary.
    """

    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not getattr(address, "is_site_local", False)
    )


def _is_ambiguous_numeric_host(labels: list[str]) -> bool:
    return bool(labels) and all(_AMBIGUOUS_NUMERIC_LABEL.fullmatch(label) for label in labels)


def _contains_control_character(value: str) -> bool:
    return any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)


def _validate_limits(*, max_url_length: int, max_dns_addresses: int) -> None:
    if not 1 <= max_url_length <= MAX_URL_LENGTH:
        raise ValueError(f"max_url_length must be between 1 and {MAX_URL_LENGTH}")
    if not 1 <= max_dns_addresses <= MAX_DNS_ADDRESSES:
        raise ValueError(f"max_dns_addresses must be between 1 and {MAX_DNS_ADDRESSES}")
