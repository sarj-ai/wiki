"""Notion export zip parser.

Notion's "HTML" export produces a zip with:
- Folders → sections
- .html files → pages
- File/folder names have a trailing hex ID appended by Notion,
  e.g. "My Page 1a2b3c4d5e6f.html" — we strip it for clean titles.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.imports.parsers import register_parser
from app.imports.parsers.base import BaseParser, ParsedNode

# Notion appends a 32-char hex ID (or shorter) before the extension
_NOTION_ID_RE = re.compile(r"\s+[0-9a-f]{8,32}$", re.IGNORECASE)


_TITLE_TAG_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _clean_title(name: str) -> str:
    """Strip Notion's trailing hex ID from a file/folder name."""
    stem = Path(name).stem if "." in name else name
    return _NOTION_ID_RE.sub("", stem).strip() or stem


def _extract_title_from_html(path: Path) -> str | None:
    """Extract the <title> from an HTML file for a more accurate page title."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2048]
        match = _TITLE_TAG_RE.search(head)
        if match:
            title = match.group(1).strip()
            # Clean Notion's empty emoji placeholders like {} or {🏠}
            title = re.sub(r"\{[^}]*\}\s*:?\s*", "", title).strip()
            if title:
                return title
    except Exception:
        pass
    return None


@register_parser
class NotionParser(BaseParser):
    format_id = "notion"
    display_name = "Notion Export"

    def validate(self, extract_dir: Path) -> bool:
        """Check that the directory contains at least one .html or .md file."""
        for p in extract_dir.rglob("*"):
            if p.is_file() and p.suffix in (".html", ".md"):
                return True
        return False

    def parse(self, extract_dir: Path) -> list[ParsedNode]:
        """Walk the extracted directory and build a ParsedNode tree."""
        return self._walk(extract_dir)

    def _walk(self, directory: Path) -> list[ParsedNode]:
        nodes: list[ParsedNode] = []

        # Sort for deterministic ordering
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())

        for entry in entries:
            if entry.name.startswith(".") or entry.name == "__MACOSX":
                continue

            if entry.is_dir():
                children = self._walk(entry)
                if children:
                    nodes.append(ParsedNode(
                        title=_clean_title(entry.name),
                        type="section",
                        children=children,
                    ))
            elif entry.is_file() and entry.suffix in (".html", ".md"):
                mime = "text/html" if entry.suffix == ".html" else "text/markdown"
                # Prefer the <title> from HTML — Notion truncates long filenames
                title = (
                    _extract_title_from_html(entry) if entry.suffix == ".html" else None
                ) or _clean_title(entry.name)
                nodes.append(ParsedNode(
                    title=title,
                    type="page",
                    content_path=entry,
                    mime_type=mime,
                ))

        return nodes
