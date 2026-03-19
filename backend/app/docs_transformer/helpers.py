"""Small helper functions used across the docs_transformer package."""

from __future__ import annotations


def _dimension_pt(dim: dict | None) -> float | None:
    if not dim:
        return None
    return dim.get("magnitude")
