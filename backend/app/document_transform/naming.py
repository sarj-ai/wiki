# Deterministic, evidence-only names for polished documents and their links.

from __future__ import annotations

import hashlib
import re
import unicodedata

from app.document_transform.ir import BlockKind, DocumentIR
from app.identifiers import FileId


_GENERIC_TITLES = frozenset(
    {"", "untitled", "untitled document", "new document", "document", "overview", "introduction"}
)
_MAX_TITLE_LENGTH = 255
_MAX_HEADING_WORDS = 18
_ALIAS_WORD_LENGTH = 10
_ALIAS_SUFFIX_LENGTH = 10


def suggested_document_title(document: DocumentIR) -> str:
    # Never infer a title from an arbitrary body sentence or later section.
    # Applying this suggestion requires a revision-safe metadata write.
    current = " ".join(document.title.split())
    if current.casefold() not in _GENERIC_TITLES:
        return document.title
    first = next((block for block in document.blocks if block.text.strip()), None)
    if first is None or first.kind is not BlockKind.HEADING or first.level not in {0, 1}:
        return current or "Untitled document"
    candidate = " ".join(first.text.split())
    if (
        candidate.casefold() in _GENERIC_TITLES
        or len(candidate) > _MAX_TITLE_LENGTH
        or len(candidate.split()) > _MAX_HEADING_WORDS
        or any(unicodedata.category(char).startswith("C") for char in candidate)
    ):
        return current or "Untitled document"
    return candidate


def polish_alias_candidates(file_id: FileId, title: str) -> tuple[str, ...]:
    # Alphabetic suffixes satisfy the existing alias constraint. Existing aliases
    # take precedence; same-file retries have identical bounded candidates.
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    words = [word[:_ALIAS_WORD_LENGTH] for word in re.findall(r"[a-z]{2,}", normalized.lower())]
    stem = "-".join(words[:2]) if words else "sarj-doc"
    return tuple(f"{stem}-{_alias_suffix(file_id, attempt)}" for attempt in range(4))


def _alias_suffix(file_id: FileId, attempt: int) -> str:
    digest = hashlib.sha256(f"sarj-polish-alias-v1:{file_id}:{attempt}".encode()).digest()
    return "".join(chr(ord("a") + byte % 26) for byte in digest[:_ALIAS_SUFFIX_LENGTH])
