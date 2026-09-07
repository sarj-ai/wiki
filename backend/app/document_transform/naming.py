# Deterministic, evidence-only names for polished documents and their links.

from __future__ import annotations

import hashlib
import re
from types import MappingProxyType
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
_MAX_ICON_JOINED_PARTS = 8
_TRAILING_DOCUMENT_DATE = re.compile(
    r"(?:\s*(?:[-\u2013\u2014|:]\s*)?(?:"
    r"(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])[-/.](?:\d{2}|\d{4})"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?(?:,)?\s+(?:19|20)\d{2}"
    r")\s*)$",
    re.IGNORECASE,
)
_REGIONAL_FLAG = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
_ICON_PART = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u27bf©®™ℹ][\ufe0e\ufe0f]?[\U0001F3FB-\U0001F3FF]?"
)
_SMALL_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
)
_PRODUCT_WORDS = MappingProxyType(
    {
        "github": "GitHub",
        "gitlab": "GitLab",
        "fastapi": "FastAPI",
        "openai": "OpenAI",
        "sarj": "Sarj",
    }
)
_ACRONYMS = frozenset(
    {"api", "qa", "mcp", "iso", "ui", "ux", "prd", "faq", "okr", "okrs", "sql", "http", "https"}
)
_EMOJI_CATEGORIES = (
    (frozenset({"incident", "incidents", "حوادث", "الحوادث"}), "🚨"),
    (frozenset({"backup", "backups", "restore", "recovery"}), "💾"),
    (frozenset({"cloud", "السحابة"}), "☁️"),
    (frozenset({"approval", "approvals", "اعتماد", "الموافقات"}), "✅"),
    (frozenset({"policy", "policies", "سياسة", "السياسات"}), "📜"),
    (frozenset({"monthly", "quarterly", "annual", "شهري", "سنوي"}), "📅"),
    (frozenset({"evidence", "audit", "audits", "أدلة", "التدقيق"}), "🔎"),
    (frozenset({"security", "أمن", "الأمن"}), "🔒"),
    (frozenset({"notes", "ملاحظات", "الملاحظات"}), "📝"),
    (frozenset({"context", "overview", "سياق", "السياق"}), "📋"),
    (frozenset({"goals", "objectives", "أهداف", "الأهداف"}), "🎯"),
    (frozenset({"actions", "tasks", "steps", "مهام", "المهام"}), "✅"),
    (frozenset({"decisions", "decision", "قرارات", "القرارات"}), "⚖️"),
    (frozenset({"budget", "finance", "costs", "ميزانية"}), "💰"),
    (frozenset({"risks", "risk", "مخاطر"}), "⚠️"),
    (frozenset({"timeline", "schedule", "milestones", "جدول"}), "📅"),
    (frozenset({"people", "team", "فريق", "الفريق"}), "👥"),
    (frozenset({"values", "principles", "قيم", "القيم"}), "💎"),
    (frozenset({"technical", "architecture", "api", "mcp"}), "⚙️"),
)


def title_case_document_name(text: str) -> str:
    words = list(re.finditer(r"\b[^\W\d_]+(?:['’][^\W\d_]+)?\b", text))
    replacements: list[str] = []
    previous = 0
    for index, match in enumerate(words):
        word = match.group()
        key = word.lower()
        if re.fullmatch(r"[A-Za-z]+(?:['’][A-Za-z]+)?", word) is None:
            pass
        elif key in _PRODUCT_WORDS:
            word = _PRODUCT_WORDS[key]
        elif key in _ACRONYMS:
            word = word.upper()
        elif word.isupper() or any(character.isupper() for character in word[1:]):
            pass
        elif (
            key in _SMALL_WORDS
            and 0 < index < len(words) - 1
            and not text[: match.start()].rstrip().endswith(":")
        ):
            word = key
        else:
            word = word[0].upper() + word[1:]
        replacements.extend((text[previous : match.start()], word))
        previous = match.end()
    return "".join((*replacements, text[previous:]))


def strip_trailing_document_date(text: str) -> str:
    normalized = " ".join(text.split())
    match = _TRAILING_DOCUMENT_DATE.search(normalized)
    if match is None:
        return normalized
    stripped = normalized[: match.start()].rstrip(" -\u2013\u2014|:,()[]")
    return stripped.strip()


def polished_document_name(text: str) -> str:
    return title_case_document_name(strip_trailing_document_date(text) or "Untitled")


def tab_icon_emoji(text: str) -> str | None:
    if not text.strip():
        return None
    first = text.lstrip()[0]
    if unicodedata.category(first) == "So" or "\u20e3" in text.lstrip()[:3]:
        return None
    words = set(re.findall(r"[^\W_]+", text.casefold()))
    return next((emoji for keywords, emoji in _EMOJI_CATEGORIES if words & keywords), "📄")


def supported_tab_icon(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if not value:
        return True
    if _REGIONAL_FLAG.fullmatch(value):
        return True
    if re.fullmatch(r"[0-9#*]\ufe0f?\u20e3", value):
        return True
    parts = value.split("\u200d")
    if len(parts) > _MAX_ICON_JOINED_PARTS:
        return False
    return all(_ICON_PART.fullmatch(part) for part in parts)


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
