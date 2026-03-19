"""Shared constants for the docs_transformer package."""

from __future__ import annotations

_ORDERED_GLYPHS = frozenset({
    "DECIMAL",
    "ZERO_DECIMAL",
    "UPPER_ALPHA",
    "LOWER_ALPHA",
    "UPPER_ROMAN",
    "LOWER_ROMAN",
})

_MONOSPACE_FONTS = frozenset({
    "courier new", "courier", "consolas", "monaco", "menlo",
    "source code pro", "roboto mono", "fira code", "fira mono",
    "ibm plex mono", "jetbrains mono", "noto sans mono",
    "ubuntu mono", "dejavu sans mono", "liberation mono",
    "sf mono", "andale mono", "lucida console",
})

_ALIGNMENT_MAP = {
    "CENTER": "center",
    "END": "right",
    "JUSTIFIED": "justify",
}
