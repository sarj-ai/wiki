"""Base class for import format parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class ParsedNode:
    """A single node in the parsed import tree."""

    title: str
    type: Literal["page", "section"]
    content_path: Path | None = None  # path to file on disk (pages only)
    mime_type: str = "text/html"
    children: list[ParsedNode] = field(default_factory=list)


class BaseParser(ABC):
    """Abstract base for import format parsers.

    Subclasses must set ``format_id`` and ``display_name`` as class attributes
    and implement ``parse`` and ``validate``.
    """

    format_id: str
    display_name: str

    @abstractmethod
    def parse(self, extract_dir: Path) -> list[ParsedNode]:
        """Parse extracted contents into a tree of ParsedNodes."""

    @abstractmethod
    def validate(self, extract_dir: Path) -> bool:
        """Quick check that the extracted directory matches this format."""
