"""Typed errors raised by ``ExtrasService``.

The router translates these to HTTP responses; the service stays free
of any ``fastapi`` import so it can be exercised in unit tests without
a request scope.
"""

from __future__ import annotations


class ExtrasError(Exception):
    pass


class DocIconNotFoundError(ExtrasError):
    """Lookup returned no row for the given drive id."""
