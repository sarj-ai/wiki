"""Parser registry — import all parser modules to trigger registration."""

from __future__ import annotations

from app.imports.parsers.base import BaseParser, ParsedNode

_PARSERS: dict[str, type[BaseParser]] = {}


def register_parser(cls: type[BaseParser]) -> type[BaseParser]:
    """Class decorator to register a parser in the global registry."""
    _PARSERS[cls.format_id] = cls
    return cls


def get_parser(format_id: str) -> BaseParser:
    """Instantiate a parser by format_id. Raises KeyError if unknown."""
    return _PARSERS[format_id]()


def list_formats() -> list[dict[str, str]]:
    """Return all registered formats as [{id, name}]."""
    return [{"id": cls.format_id, "name": cls.display_name} for cls in _PARSERS.values()]


# Import parser modules so @register_parser decorators fire
from app.imports.parsers import notion  # noqa: E402, F401

__all__ = ["BaseParser", "ParsedNode", "get_parser", "list_formats", "register_parser"]
