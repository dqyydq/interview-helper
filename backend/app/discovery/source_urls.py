"""Canonical URL selection for reviewed discovery-source evidence."""

from __future__ import annotations


def verified_source_url(*, normalized_url: str, final_url: str | None) -> str:
    """Prefer the connector-verified final URL, retaining a stable fallback.

    ``normalized_url`` is the policy-checked request URL.  A fetched source may
    resolve through a redirect, so evidence and durable provenance should point at
    the verified final URL whenever the connector supplied one.
    """

    final = final_url.strip() if final_url else ""
    return final or normalized_url
