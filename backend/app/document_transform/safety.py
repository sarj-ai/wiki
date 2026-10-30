from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from types import MappingProxyType

from app.document_transform.ir import BlockKind, DocumentIR, TextBlock
from app.document_transform.schemas import TextPatch, TransformFinding, TransformPhase
from app.document_transform.unslop import (
    CUSTOM_URI_PATTERN,
    HTML_ENTITY_PATTERN,
    RFCISH_EMAIL_PATTERN,
    SINGLE_QUOTED_SPAN_PATTERN,
    UnslopCandidate,
    expanded_deletion_span,
    scan_unslop_candidates,
)


_PROTECTED_TOKEN = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|"
    rf"{RFCISH_EMAIL_PATTERN}|{CUSTOM_URI_PATTERN}|"
    r"(?<![\w])(?:[$€£¥]\s*)?\d+(?:[.,:/-]\d+)*(?:\s?(?:%|[A-Za-z]{1,5}))?(?![\w])"
    rf"|{HTML_ENTITY_PATTERN}"
    r"|\[[^\]\n]+\]\([^)\n]+\)|\[[0-9,\- ]+\]",
    re.MULTILINE,
)
_FORCE_WORD = re.compile(
    r"\b(?:must|shall|should|may|might|cannot|can't|can|will|won't|not|never|no)\b",
    re.IGNORECASE,
)
_QUOTED_SPAN = re.compile(rf'"[^"\n]+"|“[^”\n]+”|{SINGLE_QUOTED_SPAN_PATTERN}')
_PROPER_NOUN = re.compile(r"\b(?:[A-Z][a-z]+|[A-Z]{2,}|[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b")
_SENTENCE = re.compile(r".*?(?:[.!?]+(?=\s+|$)|$)", re.DOTALL)
_MAX_CHANGE_RATIO = MappingProxyType({TransformPhase.UNSLOP: 0.35, TransformPhase.FORMAT: 0.70})
_CAPITALIZABLE_START_WORDS = frozenset(
    {"a", "an", "he", "it", "our", "she", "that", "the", "they", "this", "we", "you", "your"}
)


@dataclass(frozen=True, slots=True)
class PatchDecision:
    accepted: dict[str, str]
    findings: tuple[TransformFinding, ...]


def validate_patches(
    document: DocumentIR,
    patches: list[TextPatch],
    *,
    phase: TransformPhase,
) -> PatchDecision:
    by_id = {block.id: block for block in document.blocks}
    accepted: dict[str, str] = {}
    findings: list[TransformFinding] = []
    seen: set[str] = set()
    for patch in patches:
        block = by_id.get(patch.block_id)
        code = _rejection_code(block, patch, phase=phase, duplicate=patch.block_id in seen)
        seen.add(patch.block_id)
        if code is not None:
            findings.append(
                TransformFinding(
                    code=code,
                    block_id=patch.block_id,
                    detail="The proposed edit was skipped by a deterministic safety gate",
                )
            )
            continue
        if block is not None and patch.replacement != block.text:
            accepted[block.id] = patch.replacement
    return PatchDecision(accepted=accepted, findings=tuple(findings))


def _rejection_code(
    block: TextBlock | None,
    patch: TextPatch,
    *,
    phase: TransformPhase,
    duplicate: bool,
) -> str | None:
    if duplicate:
        return "duplicate_patch"
    if block is None:
        return "unknown_block"
    if patch.expected_sha256 != block.sha256:
        return "stale_block"
    if not block.editable or block.kind in {
        BlockKind.BLANK,
        BlockKind.CODE,
        BlockKind.QUOTE,
        BlockKind.TABLE,
    }:
        return "protected_block"
    if phase is TransformPhase.FORMAT and patch.replacement != block.text:
        return "format_text_changed"
    if not patch.replacement.strip():
        return "empty_replacement"
    if _protected_tokens(block.text) != _protected_tokens(patch.replacement):
        return "protected_content_changed"
    if _force_words(block.text) != _force_words(patch.replacement):
        return "force_language_changed"
    if phase is TransformPhase.UNSLOP:
        locality_code = _unslop_locality_code(block, patch.replacement)
        if locality_code is not None:
            return locality_code
        return None
    similarity = SequenceMatcher(None, block.text, patch.replacement, autojunk=False).ratio()
    if 1 - similarity > _MAX_CHANGE_RATIO[phase]:
        return "diff_budget_exceeded"
    return None


def _protected_tokens(value: str) -> list[str]:
    return [match.group(0) for match in _PROTECTED_TOKEN.finditer(value)]


def _force_words(value: str) -> list[str]:
    return [match.group(0).casefold() for match in _FORCE_WORD.finditer(value)]


def _unslop_locality_code(block: TextBlock, replacement: str) -> str | None:
    scanned_candidates = tuple(
        candidate
        for candidate in scan_unslop_candidates(
            DocumentIR(title="", blocks=(block,), source_format="safety")
        )
        if candidate.block_id == block.id
    )
    if not scanned_candidates:
        return "unconfirmed_unslop_edit"
    candidates = tuple(
        candidate for candidate in scanned_candidates if candidate.has_fixed_deletion
    )
    if not candidates:
        return "contextual_unslop_review_required"
    if _quoted_spans(block.text) != _quoted_spans(replacement):
        return "quoted_content_changed"
    protected_names = _proper_nouns_outside_candidates(block.text, candidates)
    if any(name not in replacement for name in protected_names):
        return "proper_noun_changed"
    if not _unchanged_sentences_are_exact(block.text, replacement, candidates):
        return "sentence_locality_changed"
    if not _is_candidate_deletion_only(block.text, replacement, candidates):
        return "noncandidate_content_changed"
    return None


def _quoted_spans(value: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in _QUOTED_SPAN.finditer(value))


def _proper_nouns_outside_candidates(
    value: str,
    candidates: tuple[UnslopCandidate, ...],
) -> tuple[str, ...]:
    spans = tuple((candidate.start, candidate.end) for candidate in candidates)
    return tuple(
        match.group(0)
        for match in _PROPER_NOUN.finditer(value)
        if not any(match.start() < end and match.end() > start for start, end in spans)
    )


def _unchanged_sentences_are_exact(
    source: str,
    replacement: str,
    candidates: tuple[UnslopCandidate, ...],
) -> bool:
    source_sentences = tuple(match for match in _SENTENCE.finditer(source) if match.group(0))
    replacement_sentences = tuple(
        sentence_text
        for match in _SENTENCE.finditer(replacement)
        if (sentence_text := match.group(0).strip())
    )
    unchanged = tuple(
        sentence_text
        for sentence in source_sentences
        if (sentence_text := sentence.group(0).strip())
        and not any(
            sentence.start() < candidate.end and sentence.end() > candidate.start
            for candidate in candidates
        )
    )
    cursor = 0
    for sentence in unchanged:
        try:
            cursor = replacement_sentences.index(sentence, cursor) + 1
        except ValueError:
            return False
    return True


def _is_candidate_deletion_only(
    source: str,
    replacement: str,
    candidates: tuple[UnslopCandidate, ...],
) -> bool:
    comparison = replacement
    first_source = next((index for index, char in enumerate(source) if char.isalpha()), None)
    first_replacement = next(
        (index for index, char in enumerate(replacement) if char.isalpha()), None
    )
    if (
        first_source is not None
        and first_replacement is not None
        and any(candidate.start <= first_source < candidate.end for candidate in candidates)
    ):
        source_survivor = next(
            (
                index
                for index in range(first_source, len(source))
                if source[index].isalpha()
                and not any(candidate.start <= index < candidate.end for candidate in candidates)
            ),
            None,
        )
        if (
            source_survivor is not None
            and source[source_survivor].casefold() == replacement[first_replacement].casefold()
            and source[source_survivor] != replacement[first_replacement]
        ):
            survivor_word = re.match(r"[A-Za-z]+", source[source_survivor:])
            if (
                survivor_word is None
                or survivor_word.group(0).casefold() not in _CAPITALIZABLE_START_WORDS
            ):
                return False
            comparison = (
                replacement[:first_replacement]
                + source[source_survivor]
                + replacement[first_replacement + 1 :]
            )

    allowed_indexes = {
        index
        for span in (
            expanded_deletion_span(source, candidate.start, candidate.end)
            for candidate in candidates
        )
        for index in range(span.start, span.end)
    }
    source_index = len(source) - 1
    replacement_index = len(comparison) - 1
    deleted = False
    while source_index >= 0 and replacement_index >= 0:
        if source[source_index] == comparison[replacement_index]:
            source_index -= 1
            replacement_index -= 1
            continue
        if source_index not in allowed_indexes:
            return False
        source_index -= 1
        deleted = True
    if replacement_index >= 0:
        return False
    while source_index >= 0:
        if source_index not in allowed_indexes:
            return False
        source_index -= 1
        deleted = True
    return deleted
