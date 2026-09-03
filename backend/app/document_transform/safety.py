from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from types import MappingProxyType

from app.document_transform.ir import BlockKind, DocumentIR, TextBlock
from app.document_transform.schemas import TextPatch, TransformFinding, TransformPhase
from app.document_transform.unslop import UnslopCandidate, scan_unslop_candidates


_PROTECTED_TOKEN = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|https?://[^\s)>]+|\b\d+(?:[.,:/-]\d+)*(?:%|[A-Za-z]{1,5})?\b"
    r"|\[[^\]\n]+\]\([^)\n]+\)|\[[0-9,\- ]+\]",
    re.MULTILINE,
)
_FORCE_WORD = re.compile(
    r"\b(?:must|shall|should|may|might|cannot|can't|can|will|won't|not|never|no)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_QUOTED_SPAN = re.compile(r'"[^"\n]+"|“[^”\n]+”|‘[^’\n]+’|\'[^\'\n]+\'')
_PROPER_NOUN = re.compile(r"\b(?:[A-Z][a-z]+|[A-Z]{2,}|[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b")
_SENTENCE = re.compile(r".*?(?:[.!?]+(?=\s+|$)|$)", re.DOTALL)
_ALLOWED_GLUE_WORDS = frozenset({"a", "an", "and", "but", "for", "of", "or", "the", "to"})
_MAX_CHANGE_RATIO = MappingProxyType({TransformPhase.UNSLOP: 0.35, TransformPhase.FORMAT: 0.70})


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
    if not block.editable or block.kind in {BlockKind.CODE, BlockKind.TABLE, BlockKind.BLANK}:
        return "protected_block"
    if not patch.replacement.strip():
        return "empty_replacement"
    if Counter(_protected_tokens(block.text)) != Counter(_protected_tokens(patch.replacement)):
        return "protected_content_changed"
    if Counter(_force_words(block.text)) != Counter(_force_words(patch.replacement)):
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
    candidates = tuple(
        candidate
        for candidate in scan_unslop_candidates(
            DocumentIR(title="", blocks=(block,), source_format="safety")
        )
        if candidate.block_id == block.id
    )
    if not candidates:
        return "unconfirmed_unslop_edit"
    if _quoted_spans(block.text) != _quoted_spans(replacement):
        return "quoted_content_changed"
    protected_names = _proper_nouns_outside_candidates(block.text, candidates)
    if any(name not in replacement for name in protected_names):
        return "proper_noun_changed"
    if not _unchanged_sentences_are_exact(block.text, replacement, candidates):
        return "sentence_locality_changed"
    source_words = [match.group(0).casefold() for match in _WORD.finditer(block.text)]
    replacement_words = [match.group(0).casefold() for match in _WORD.finditer(replacement)]
    outside_words = [
        match.group(0).casefold()
        for match in _WORD.finditer(block.text)
        if not any(
            match.start() < candidate.end and match.end() > candidate.start
            for candidate in candidates
        )
    ]
    if not _is_subsequence(outside_words, replacement_words):
        return "noncandidate_content_changed"
    source_vocabulary = set(source_words)
    if any(
        word not in source_vocabulary and word not in _ALLOWED_GLUE_WORDS
        for word in replacement_words
    ):
        return "new_content_introduced"
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
        if (sentence_text := match.group(0))
    )
    if len(source_sentences) != len(replacement_sentences):
        return False
    for index, sentence in enumerate(source_sentences):
        contains_candidate = any(
            sentence.start() < candidate.end and sentence.end() > candidate.start
            for candidate in candidates
        )
        if not contains_candidate and sentence.group(0) != replacement_sentences[index]:
            return False
    return True


def _is_subsequence(required: list[str], observed: list[str]) -> bool:
    iterator = iter(observed)
    return all(any(candidate == word for candidate in iterator) for word in required)
