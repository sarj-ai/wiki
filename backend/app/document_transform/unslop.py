from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from app.document_transform.ir import BlockKind, DocumentIR


UNSLOP_UPSTREAM_COMMIT = "d81f5196167ded24f46fced04958c0c12d681798"
UNSLOP_UPSTREAM_VERSION = "2.3.0"
MAX_MODEL_EDITABLE_BLOCK_CHARS = 32_768
_CAPITALIZABLE_START_WORDS = frozenset(
    {"a", "an", "he", "it", "our", "she", "that", "the", "they", "this", "we", "you", "your"}
)
_SENTENCE_PUNCTUATION = ".!?…"
RFCISH_EMAIL_PATTERN = (
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}"
)
SINGLE_QUOTED_SPAN_PATTERN = r"(?<!\w)'[^'\n]+'(?!\w)|‘[^’\n]+’"
CUSTOM_URI_PATTERN = r"[A-Za-z][A-Za-z0-9+.-]*:(?://)?[^\s)>]+"
HTML_ENTITY_PATTERN = r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);"

_CANDIDATE_PATTERNS = (
    (
        "canned_intro",
        re.compile(
            r"\b(?:in today['’]s fast[\u2011 -]paced|in the ever[\u2011 -]evolving)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "meta_note",
        re.compile(
            r"^(?:it is important to note|it is worth noting)(?:\s+that)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "canned_transition",
        re.compile(
            r"^(?:moreover|furthermore|in conclusion)\b,?",
            re.IGNORECASE,
        ),
    ),
    (
        "inflated_phrase",
        re.compile(
            r"\b(?:delv(?:e|es|ed|ing) into (?:the )?(?:topic|topics|issue|issues|"
            r"implication|implications|question|questions|subject|details?|nuance|nuances|"
            r"meaning|argument|claim|concept|matter|problem|analysis|research|data|strategy|"
            r"history|world|conversation|evidence|complexit(?:y|ies))|"
            r"(?:acts|serves|stands|stood) as (?:a|an|the) (?:testament|reminder|symbol|"
            r"beacon|foundation|cornerstone|gateway|catalyst|bridge|hub|springboard|window|"
            r"monument|hallmark|blueprint|cautionary|stark|powerful|shining|prime example|"
            r"case study|model for))\b",
            re.IGNORECASE,
        ),
    ),
    ("throat_clearing", re.compile(r"^here['’]s the thing:\s*", re.IGNORECASE)),
    (
        "significance_inflation",
        re.compile(r"\bunderscore(?:s|d)? the importance(?: of)?\b", re.IGNORECASE),
    ),
    ("ai_vocabulary", re.compile(r"\btreasure trove\b", re.IGNORECASE)),
    ("false_agency", re.compile(r"\bspeak for themselves\b", re.IGNORECASE)),
    (
        "assistant_artifact",
        re.compile(r"\b(?:as an ai language model|i hope this helps)\b", re.IGNORECASE),
    ),
    ("jargon", re.compile(r"\b(?:game[\u2011 -]changer|synergy)\b", re.IGNORECASE)),
    ("filler", re.compile(r"\bat the end of the day\b", re.IGNORECASE)),
    ("knowledge_cutoff", re.compile(r"\bas of my last\b", re.IGNORECASE)),
    ("rhetorical_question", re.compile(r"\bwhy should you care\??", re.IGNORECASE)),
)

_AUTO_DELETE_CODES = frozenset(
    {
        "canned_transition",
        "meta_note",
        "throat_clearing",
    }
)

_IGNORED_SPAN = re.compile(
    r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`|\"[^\"]*\"|“[^”]*”|"
    rf"{SINGLE_QUOTED_SPAN_PATTERN}|^>.*$",
    re.MULTILINE,
)
_RECAP_PROTECTED_SPAN = re.compile(
    rf"`[^`\n]+`|\"[^\"\n]*\"|“[^”\n]*”|{SINGLE_QUOTED_SPAN_PATTERN}|"
    rf"\[[^\]\n]+\]\([^\n]+\)|{RFCISH_EMAIL_PATTERN}|{CUSTOM_URI_PATTERN}|"
    rf"(?:[\w.-]+)?{HTML_ENTITY_PATTERN}"
    r"(?:[\w.-]+)?"
)
_RECAP_PROTECTED_LEADING = frozenset("@/#_`'\"‘“[")

UNSLOP_CATALOG_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "auto_delete_codes": sorted(_AUTO_DELETE_CODES),
            "capitalizable_start_words": sorted(_CAPITALIZABLE_START_WORDS),
            "candidate_patterns": [
                (code, pattern.pattern, pattern.flags) for code, pattern in _CANDIDATE_PATTERNS
            ],
            "ignored_span": (_IGNORED_SPAN.pattern, _IGNORED_SPAN.flags),
            "recap_protected_leading": sorted(_RECAP_PROTECTED_LEADING),
            "recap_protected_span": (
                _RECAP_PROTECTED_SPAN.pattern,
                _RECAP_PROTECTED_SPAN.flags,
            ),
            "sentence_punctuation": _SENTENCE_PUNCTUATION,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
).hexdigest()


@dataclass(frozen=True, slots=True)
class UnslopCandidate:
    block_id: str
    code: str
    start: int
    end: int
    matched_text: str

    @property
    def id(self) -> str:
        payload = f"{self.block_id}\0{self.code}\0{self.start}\0{self.end}\0{self.matched_text}"
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def has_fixed_deletion(self) -> bool:
        return self.code in _AUTO_DELETE_CODES


@dataclass(frozen=True, slots=True)
class DeletionSpan:
    start: int
    end: int


def scan_unslop_candidates(document: DocumentIR) -> tuple[UnslopCandidate, ...]:
    candidates: list[UnslopCandidate] = []
    for block in document.blocks:
        if (
            len(block.text) > MAX_MODEL_EDITABLE_BLOCK_CHARS
            or not block.editable
            or block.kind
            in {
                BlockKind.BLANK,
                BlockKind.CODE,
                BlockKind.QUOTE,
                BlockKind.TABLE,
            }
        ):
            continue
        masked = _mask_ignored_spans(block.text)
        for code, pattern in _CANDIDATE_PATTERNS:
            candidates.extend(
                UnslopCandidate(
                    block_id=block.id,
                    code=code,
                    start=match.start(),
                    end=match.end(),
                    matched_text=match.group(0),
                )
                for match in pattern.finditer(masked)
            )
    return tuple(
        sorted(
            _remove_contained_candidates(candidates),
            key=lambda candidate: (
                candidate.block_id,
                candidate.start,
                candidate.end,
                candidate.code,
            ),
        )
    )


def deterministic_unslop_replacement(text: str, candidates: tuple[UnslopCandidate, ...]) -> str:
    eligible = tuple(candidate for candidate in candidates if candidate.has_fixed_deletion)
    if not eligible or not _candidate_offsets_match(text, eligible):
        return text
    trimmed = _delete_candidates(text, eligible)
    if not trimmed.strip():
        return text
    if min(candidate.start for candidate in eligible) == 0:
        recapped = _recap_after_leading_deletion(trimmed)
        if recapped is None:
            return text
        return recapped
    return trimmed


def _candidate_offsets_match(text: str, candidates: tuple[UnslopCandidate, ...]) -> bool:
    # Offsets belong to the exact scanned text. A stale patch must not delete
    # a coincidentally similar span from a newer revision.
    return not any(
        candidate.start < 0
        or candidate.end > len(text)
        or candidate.start >= candidate.end
        or text[candidate.start : candidate.end] != candidate.matched_text
        for candidate in candidates
    )


def _delete_candidates(text: str, eligible: tuple[UnslopCandidate, ...]) -> str:
    result = text
    for candidate in sorted(eligible, key=lambda item: (item.start, item.end), reverse=True):
        span = expanded_deletion_span(text, candidate.start, candidate.end)
        result = result[: span.start] + result[span.end :]
    if min(candidate.start for candidate in eligible) == 0:
        result = result.lstrip()
    if max(candidate.end for candidate in eligible) == len(text):
        result = result.rstrip()
    return result


def _recap_after_leading_deletion(value: str) -> str | None:
    first_nonspace = next((char for char in value if not char.isspace()), None)
    if first_nonspace is not None and first_nonspace in _SENTENCE_PUNCTUATION:
        return None
    first_alpha = next((index for index, char in enumerate(value) if char.isalpha()), None)
    if first_alpha is None or not value[first_alpha].islower():
        return value
    word = re.match(r"[A-Za-z]+", value[first_alpha:])
    if word is None or word.group(0).casefold() not in _CAPITALIZABLE_START_WORDS:
        return None
    if _recapitalization_is_protected(value, first_alpha):
        return None
    return value[:first_alpha] + value[first_alpha].upper() + value[first_alpha + 1 :]


def _recapitalization_is_protected(value: str, first_alpha: int) -> bool:
    if any(char in _RECAP_PROTECTED_LEADING for char in value[:first_alpha]):
        return True
    return any(
        match.start() <= first_alpha < match.end()
        for match in _RECAP_PROTECTED_SPAN.finditer(value)
    )


def expanded_deletion_span(text: str, start: int, end: int) -> DeletionSpan:
    while end < len(text) and text[end] in " \t,:;—–-":
        end += 1
    punctuation_end = end
    while punctuation_end < len(text) and text[punctuation_end] in _SENTENCE_PUNCTUATION:
        punctuation_end += 1
    if punctuation_end > end and (
        punctuation_end == len(text) or text[punctuation_end] in " \t\r\n"
    ):
        end = punctuation_end
        while end < len(text) and text[end] in " \t":
            end += 1
    if start > 0 and end == len(text):
        while start > 0 and text[start - 1] in " \t,:;—–-":
            start -= 1
    return DeletionSpan(start, end)


def _remove_contained_candidates(
    candidates: list[UnslopCandidate],
) -> tuple[UnslopCandidate, ...]:
    ordered = sorted(candidates, key=lambda item: (item.start, -(item.end - item.start), item.code))
    kept: list[UnslopCandidate] = []
    for candidate in ordered:
        if any(
            other.block_id == candidate.block_id
            and other.start <= candidate.start
            and candidate.end <= other.end
            for other in kept
        ):
            continue
        kept.append(candidate)
    return tuple(kept)


def _mask_ignored_spans(value: str) -> str:
    return _IGNORED_SPAN.sub(
        lambda match: re.sub(r"[^\n]", " ", match.group(0)),
        value,
    )
