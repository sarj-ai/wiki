"""Internal helpers for the drive package."""

from __future__ import annotations


def _escape_drive_query(value: str) -> str:
    """Escape a value for use in a Drive API query string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")
