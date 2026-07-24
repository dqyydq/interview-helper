"""Bounded public-web discovery primitives.

The discovery package is deliberately separate from ordinary question-bank code.
Anything crossing from a user-supplied URL into an external search/extract connector
must first pass the policy boundary in :mod:`app.discovery.url_policy`.
"""
