from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from types import MappingProxyType
from typing import Final, Literal

from app.document_transform.inline_code import inline_code_spans, mask_inline_code
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock
from app.document_transform.measurement_actor import reassigned_measurement_action
from app.document_transform.quantity_notation import HORIZONTAL_SPACE, UNIT_POWER, UNIT_SYMBOL
from app.document_transform.quotations import QUOTED_SPAN_PATTERN, quoted_block_ids
from app.document_transform.recurring_scope import fronted_recurring_scope_expanded
from app.document_transform.revision_arguments import revision_arguments
from app.document_transform.schemas import TextPatch, TransformFinding, TransformPhase
from app.document_transform.unslop import (
    CUSTOM_URI_PATTERN,
    HTML_ENTITY_PATTERN,
    RFCISH_EMAIL_PATTERN,
    UnslopCandidate,
    ambiguous_reference_framing,
    editorial_quality,
    expanded_deletion_span,
    reassigned_document_writing_action,
    scan_unslop_candidates,
    without_editorial_framing,
)


_VULGAR_FRACTION = r"[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]"
# Preservation also retains unusual authored superscript sequences verbatim.
_NUMERIC_POWER = rf"(?:[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+|{UNIT_POWER})?"
_NUMERIC_MAGNITUDE = (
    rf"(?:(?:\d+[^\S\n]+)?\d+[^\S\n]*[/⁄][^\S\n]*\d+|"
    rf"(?:\d+[^\S\n]*)?{_VULGAR_FRACTION}|"
    r"\d+(?:[.,٫٬:/⁄-]\d+|[ \u00a0\u2009\u202f'’]\d{3})*|[.,٫]\d+)"
    rf"(?:\(\d+\))?(?:[eE][+−-]?\d+)?{_NUMERIC_POWER}"
)
_SIGNED_NUMBER = rf"[+−-]?(?:[$€£¥₹₩₽₺]\s*)?[+−-]?{_NUMERIC_MAGNITUDE}"
_NUMERIC_UNIT = (
    rf"(?:%|٪|‰|(?!(?i:and|or|but|nor|yet)\b|[xX]{HORIZONTAL_SPACE}*\d)"
    rf"[A-Za-zµμ°ΩÅ]{{1,5}}{_NUMERIC_POWER})"
    rf"(?:{HORIZONTAL_SPACE}*[/*·]{HORIZONTAL_SPACE}*[A-Za-zµμ°ΩÅ]{{1,5}}{_NUMERIC_POWER}"
    # Unmarked multiplication needs known unit symbols, not arbitrary prose words.
    rf"|{HORIZONTAL_SPACE}+{UNIT_SYMBOL}{_NUMERIC_POWER}(?![A-Za-zµμ°ΩÅ]))*"
)
_QUANTITY_TERM = rf"{_SIGNED_NUMBER}(?:{HORIZONTAL_SPACE}*{_NUMERIC_UNIT})?"
_QUANTITY_LITERAL = (
    rf"(?:(?:[<>]=?|[≤≥~≈≃])\s*)?{_QUANTITY_TERM}"
    rf"(?:{HORIZONTAL_SPACE}*(?:\+/-|[±∓×xX÷+−–*/⁄:^\-]){HORIZONTAL_SPACE}*{_QUANTITY_TERM})*"
)
_PROTECTED_CODE = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
_PROTECTED_TOKEN = re.compile(
    rf"{RFCISH_EMAIL_PATTERN}|{CUSTOM_URI_PATTERN}|"
    # Keep the full notation: signs, exponents, uncertainty, powers, and accounting
    # parentheses can change a value even when every decimal digit survives.
    # Keep grouped integers, mixed fractions, and parenthetical uncertainty
    # together so layout breaks cannot detach part of a quantity or its unit.
    # Compound units and powers may contain authored horizontal spacing.
    rf"(?<![\w.])(?:\({_QUANTITY_LITERAL}\)|{_QUANTITY_LITERAL})(?![\w])"
    r"|\b(?=\w*\d)\w+(?:[.:-]\w+)*\b"
    r"|[±∓×÷≤≥≠≈≃]"
    rf"|{HTML_ENTITY_PATTERN}"
    r"|\[[^\]\n]+\]\([^)\n]+\)|\[[0-9,\- ]+\]",
    re.MULTILINE,
)
_FORCE_WORD = re.compile(
    r"\b(?:[^\W\d_]+n['\u2018\u2019\u02bc\uff07]t|"
    r"intended\s+to|aims\s+to|opportunit(?:y|ies)\s+to|responsible\s+for|"
    r"must|shall|should|may|might|could|would|cannot|can|will|not|never|no|"
    r"each|every|all|only|both|neither|either|per)\b",
    re.IGNORECASE,
)
_CANONICAL_APOSTROPHES = str.maketrans("\u2018\u2019\u02bc\uff07", "''''")
_QUOTED_SPAN = re.compile(QUOTED_SPAN_PATTERN)
_NEGATED_TRANSFORMATION_PREFIX = (
    r"\b(?:not|never|cannot|[^\W\d_]+n't)\s+"
    r"(?:to\s+automatically\s+|automatically\s+to\s+|to\s+|automatically\s+)?"
)
_NEGATED_TRANSFORMATION = re.compile(
    _NEGATED_TRANSFORMATION_PREFIX + r"(?:turn|convert|transform)\b[^.!?\n]*?\binto\b",
    re.IGNORECASE,
)
_RETAINED_NEGATED_TRANSFORMATION = re.compile(
    _NEGATED_TRANSFORMATION_PREFIX + r"(?:turn|convert|transform|make|become)\b",
    re.IGNORECASE,
)
_LATENCY_COLUMN = re.compile(
    r"\b(?P<unit>nano|micro|milli)seconds?\s+(?:columns?|cells?)\b", re.IGNORECASE
)
_LATENCY_FIELD_ALIASES: Final[dict[str, str]] = {
    "nano": r"(?:(?i:nanoseconds?)|ns)",
    "micro": r"(?:(?i:microseconds?)|[µμ]s)",
    "milli": r"(?:(?i:milliseconds?)|ms)",
}
_INCLUDED_ENTRIES = re.compile(r"\bincluding\b[^.;!?\n]*\bentries\b", re.IGNORECASE)
_INCLUDED_BLANK_COLUMNS = re.compile(
    r"\bincluding\b[^.;!?\n]*\bblank\s+"
    r"(?:(?:(?:nano|micro|milli)seconds?|ns|[µμ]s|ms)\s+)?columns?\b",
    re.IGNORECASE,
)
_SCOPE_QUALIFIER = re.compile(
    r"\b(?:longest|shortest|largest|smallest|highest|lowest)\s+"
    r"(?:(?:[\w-]+(?:ing|ed)|upper|lower)\s+)?[\w-]+\b|"
    r"\bmore (?:often|likely|specific)\b|"
    r"\b(?:generally|usual|usually|typically|often|sometimes|rarely|mostly|mainly|rounded|"
    r"at least|at most|no more than|up to|same|helps?|appears?|suggests?|interpreted|evidence|"
    r"particular|specific|within|together|available|still|other|another|different|earlier|later|"
    r"successive|current|sample|useful|essential|necessary|shape|maintain|rare|young|older|younger|"
    r"observed|recorded|studied|measured|estimated|reported|inferred|concluded|proved|"
    r"national|confidence intervals?|as (?:clearly|often|likely|many|much) as)\b|"
    r"\b(?:about|around|roughly|approximately|nearly)\b(?=\s+\d)|"
    r"\b[^\W\d_]+(?:[-\u2011][^\W\d_]+)+\b|"
    r"\b(?:above|below)\b",
    re.IGNORECASE,
)
_RHETORICAL_COMPOUNDS = frozenset(
    {"ever-evolving", "fast-paced", "game-changing", "cutting-edge", "state-of-the-art"}
)
_PROPER_NOUN = re.compile(r"\b(?:[A-Z][a-z]+|[A-Z]{2,}|[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b")
_SENTENCE = re.compile(r".*?(?:[.!?]+(?=\s+|$)|$)", re.DOTALL)
_ISO_DATE = r"\b\d{4}-\d{2}-\d{2}\b"
_DATE = re.compile(_ISO_DATE)
_TEMPORAL_SCOPE = re.compile(
    rf"\b(?P<operator>on|by|before|after|since|until|as\s+of)\s+"
    rf"(?P<ticks>`*)(?P<date>{_ISO_DATE})(?P=ticks)",
    re.IGNORECASE,
)
_NON_TEMPORAL_ON = re.compile(r"\bbased\s+$", re.IGNORECASE)
_FRONTED_DATE = re.compile(
    rf"\s*(?:on|by|before|after|since|until|as of)\s+(?P<date>{_ISO_DATE})(?:\s*,|\s+)",
    re.IGNORECASE,
)
_MAX_CHANGE_RATIO = MappingProxyType({TransformPhase.UNSLOP: 0.35, TransformPhase.FORMAT: 0.70})
_CAPITALIZABLE_START_WORDS = frozenset(
    {"a", "an", "he", "it", "our", "she", "that", "the", "they", "this", "we", "you", "your"}
)
_EDITORIAL_OPENING_WORDS = _CAPITALIZABLE_START_WORDS | frozenset(
    {"as", "at", "by", "for", "from", "in", "on", "to", "with"}
)
REPAIRABLE_REWRITE_CODES = frozenset(
    {
        "protected_content_changed",
        "force_language_changed",
        "proper_noun_changed",
        "quoted_content_changed",
        "native_citation_changed",
        "concision_expanded",
        "concision_target_unmet",
        "empty_replacement",
        "semantic_review_rejected",
        "scope_qualifier_changed",
        "document_writing_actor_changed",
        "measurement_actor_changed",
        "document_reference_identity_ambiguous",
        "temporal_scope_changed",
        "negative_transformation_scope_changed",
        "unit_column_identity_changed",
        "source_entry_relationship_changed",
        "revision_argument_changed",
    }
)


@dataclass(frozen=True, slots=True)
class PatchDecision:
    accepted: dict[str, str]
    findings: tuple[TransformFinding, ...]


@dataclass(frozen=True, slots=True)
class RewriteConstraints:
    protected_tokens: tuple[str, ...]
    force_language: tuple[str, ...]
    quotations: tuple[str, ...]
    protected_names: tuple[str, ...]
    scope_qualifiers: tuple[str, ...] = ()
    revision_arguments: tuple[str, ...] = ()
    temporal_scopes: tuple[str, ...] = ()


def rewrite_constraints(text: str, *, framing_source: str | None = None) -> RewriteConstraints:
    return RewriteConstraints(
        protected_tokens=tuple(_protected_tokens(text)),
        force_language=tuple(
            _force_words(without_editorial_framing(text, framing_source=framing_source))
        ),
        quotations=_quoted_spans(text),
        protected_names=_protected_rewrite_names(text),
        scope_qualifiers=tuple(_scope_qualifiers(text, framing_source=framing_source)),
        revision_arguments=revision_arguments(text),
        temporal_scopes=_temporal_scopes(text),
    )


def validate_patches(
    document: DocumentIR,
    patches: list[TextPatch],
    *,
    phase: TransformPhase,
    aggressive_unslop: bool = False,
    framing_source: DocumentIR | None = None,
) -> PatchDecision:
    by_id = {block.id: block for block in document.blocks}
    framing_text = {
        block.id: block.text
        for block in (document if framing_source is None else framing_source).blocks
    }
    accepted: dict[str, str] = {}
    findings: list[TransformFinding] = []
    seen: set[str] = set()
    quoted = quoted_block_ids(document)
    for patch in patches:
        block = by_id.get(patch.block_id)
        code = _rejection_code(
            block,
            patch,
            phase=phase,
            duplicate=patch.block_id in seen,
            aggressive_unslop=aggressive_unslop,
            framing_source=framing_text.get(patch.block_id),
        )
        if (
            code is None
            and patch.block_id in quoted
            and block is not None
            and patch.replacement != block.text
        ):
            code = "quoted_content_changed"
        seen.add(patch.block_id)
        if code == "concision_not_improved":
            continue
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
    aggressive_unslop: bool = False,
    framing_source: str | None = None,
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
        return _empty_replacement_code(
            block,
            patch,
            phase=phase,
            aggressive=aggressive_unslop,
            framing_source=framing_source,
        )
    if block.protected_suffix is not None and (
        not patch.replacement.endswith(block.protected_suffix)
        or not patch.replacement.removesuffix(block.protected_suffix).strip()
    ):
        return "native_citation_changed"
    literal_code = _literal_change_code(
        before=block.text,
        after=patch.replacement,
        allow_reordering=aggressive_unslop and phase is TransformPhase.UNSLOP,
        framing_source=framing_source,
    )
    if literal_code is not None:
        return literal_code
    if phase is TransformPhase.UNSLOP:
        if aggressive_unslop:
            return _aggressive_unslop_code(block, patch.replacement, framing_source=framing_source)
        locality_code = _unslop_locality_code(block, patch.replacement)
        if locality_code is not None:
            return locality_code
        return None
    similarity = SequenceMatcher(None, block.text, patch.replacement, autojunk=False).ratio()
    if 1 - similarity > _MAX_CHANGE_RATIO[phase]:
        return "diff_budget_exceeded"
    return None


def _empty_replacement_code(
    block: TextBlock,
    patch: TextPatch,
    *,
    phase: TransformPhase,
    aggressive: bool,
    framing_source: str | None,
) -> str | None:
    # A deletion still needs independent, source-bound semantic review. Keep
    # literal and obligation guards before that review, including on short copy.
    if (
        not aggressive
        or phase is not TransformPhase.UNSLOP
        or patch.replacement
        or block.kind is not BlockKind.PARAGRAPH
    ):
        return "empty_replacement"
    if (
        block.protected_suffix is not None
        or block.native_content
        or re.search(r"[\r\n\v\f\u2028\u2029]", block.text)
    ):
        return "empty_replacement"
    constraints = rewrite_constraints(block.text, framing_source=framing_source)
    if any(
        (
            constraints.protected_tokens,
            constraints.quotations,
            constraints.protected_names,
            constraints.revision_arguments,
            constraints.temporal_scopes,
            set(constraints.force_language) - {"each", "every", "all", "per"},
        )
    ):
        return "empty_replacement"
    return None


def _literal_change_code(
    *, before: str, after: str, allow_reordering: bool, framing_source: str | None = None
) -> (
    Literal["protected_content_changed", "force_language_changed", "revision_argument_changed"]
    | None
):
    if not _same_retained_values(
        _protected_tokens(before), _protected_tokens(after), allow_reordering=allow_reordering
    ):
        return "protected_content_changed"
    if allow_reordering:
        origin = before if framing_source is None else framing_source
        before = without_editorial_framing(before, framing_source=origin)
        after = without_editorial_framing(after, framing_source=origin)
    if not _same_retained_values(
        _force_words(before), _force_words(after), allow_reordering=allow_reordering
    ):
        return "force_language_changed"
    if not _same_retained_values(
        list(revision_arguments(before)),
        list(revision_arguments(after)),
        allow_reordering=allow_reordering,
    ):
        return "revision_argument_changed"
    return None


def _same_retained_values(before: list[str], after: list[str], *, allow_reordering: bool) -> bool:
    if allow_reordering:
        return Counter(before) == Counter(after)
    return before == after


def _aggressive_unslop_code(
    block: TextBlock, replacement: str, *, framing_source: str | None
) -> str | None:
    if ambiguous_reference_framing(block.text, replacement, framing_source=framing_source):
        return "document_reference_identity_ambiguous"
    if reassigned_document_writing_action(block.text, replacement, framing_source=framing_source):
        return "document_writing_actor_changed"
    if reassigned_measurement_action(block.text, replacement):
        return "measurement_actor_changed"
    if (
        _fronted_date_scope_expanded(block.text, replacement)
        or fronted_recurring_scope_expanded(block.text, replacement)
        or Counter(_temporal_scopes(block.text)) != Counter(_temporal_scopes(replacement))
    ):
        return "temporal_scope_changed"
    if len(_NEGATED_TRANSFORMATION.findall(block.text.translate(_CANONICAL_APOSTROPHES))) > len(
        _RETAINED_NEGATED_TRANSFORMATION.findall(replacement.translate(_CANONICAL_APOSTROPHES))
    ):
        return "negative_transformation_scope_changed"
    if column_code := _latency_column_scope_code(block.text, replacement):
        return column_code
    if Counter(_scope_qualifiers(block.text, framing_source=framing_source)) != Counter(
        _scope_qualifiers(replacement, framing_source=framing_source)
    ):
        return "scope_qualifier_changed"
    if _quoted_spans(block.text) != _quoted_spans(replacement):
        return "quoted_content_changed"
    if re.findall(r"[\r\n\v\f\u2028\u2029]", block.text) != re.findall(
        r"[\r\n\v\f\u2028\u2029]", replacement
    ):
        return "paragraph_structure_changed"
    if len(replacement.split()) > len(block.text.split()):
        return "concision_expanded"
    words = frozenset(word.casefold() for word in re.findall(r"\b\w+\b", replacement))
    if any(name.casefold() not in words for name in _protected_rewrite_names(block.text)):
        return "proper_noun_changed"
    if replacement != block.text and editorial_quality(block, replacement) >= editorial_quality(
        block, block.text
    ):
        return "concision_not_improved"
    return None


def _latency_column_scope_code(source: str, replacement: str) -> str | None:
    columns = tuple(_LATENCY_COLUMN.finditer(source))
    for column in columns:
        unit = column["unit"].casefold()
        if unit not in _LATENCY_FIELD_ALIASES:
            return "unit_column_identity_changed"
        aliases = _LATENCY_FIELD_ALIASES[unit]
        field = (
            rf"(?<!\w)(?:{aliases}\s+(?i:cells?|columns?)|"
            rf"(?i:cells?|columns?)\s+(?i:in|of)\s+{aliases})(?!\w)"
        )
        if not re.search(field, replacement):
            return "unit_column_identity_changed"
    if (
        columns
        and _INCLUDED_ENTRIES.search(source)
        and (
            not _INCLUDED_ENTRIES.search(replacement)
            or (
                not _INCLUDED_BLANK_COLUMNS.search(source)
                and _INCLUDED_BLANK_COLUMNS.search(replacement)
            )
        )
    ):
        return "source_entry_relationship_changed"
    return None


def _fronted_date_scope_expanded(source: str, replacement: str) -> bool:
    # A fronted date governs the coordinated predicates that follow it. Do not
    # move an unrelated source measurement under that date while merging prose.
    dated: dict[str, set[str]] = {}
    for sentence in _SENTENCE.finditer(source):
        text = sentence.group()
        for date in _DATE.finditer(text):
            dated.setdefault(date.group(), set()).update(_protected_tokens(text))
    for sentence in _SENTENCE.finditer(replacement):
        text = sentence.group().split(";", maxsplit=1)[0]
        fronted = _FRONTED_DATE.match(text)
        if fronted is None or fronted["date"] not in dated:
            continue
        if any(
            token not in dated[fronted["date"]] and any(char.isdecimal() for char in token)
            for token in _protected_tokens(text)
        ):
            return True
    return False


def _temporal_scopes(text: str) -> tuple[str, ...]:
    return tuple(
        " ".join(match["operator"].casefold().split()) + " " + match["date"]
        for match in _TEMPORAL_SCOPE.finditer(text)
        if match["operator"].casefold() != "on"
        or _NON_TEMPORAL_ON.search(text[: match.start()]) is None
    )


def _scope_qualifiers(text: str, *, framing_source: str | None = None) -> list[str]:
    return [
        match.group(0).casefold()
        for match in _SCOPE_QUALIFIER.finditer(
            without_editorial_framing(text, framing_source=framing_source)
        )
        if match.group(0).casefold().replace("\u2011", "-") not in _RHETORICAL_COMPOUNDS
    ]


def _protected_rewrite_names(text: str) -> tuple[str, ...]:
    names: list[str] = []
    projected = without_editorial_framing(text)
    for match in _PROPER_NOUN.finditer(projected):
        prefix = text[: match.start()].rstrip()
        sentence_initial = not prefix or prefix[-1] in ".!?\n"
        multiple_capitals = sum(character.isupper() for character in match.group()) > 1
        editorial_opening = (
            match.group().casefold() in _EDITORIAL_OPENING_WORDS
            and not projected[: match.start()].strip(" ,;:")
            and not multiple_capitals
        )
        if (not sentence_initial or multiple_capitals) and not editorial_opening:
            names.append(match.group())
    return tuple(names)


def _protected_tokens(value: str) -> list[str]:
    spans = [(match.start(), match.end()) for match in _PROTECTED_TOKEN.finditer(value)]
    spans.extend(inline_code_spans(value))
    # Legacy protection still covers malformed and multiline code. Mask complete
    # inline spans first so their closing ticks cannot open a spurious match in
    # the prose between two independent literals.
    spans.extend(
        (match.start(), match.end()) for match in _PROTECTED_CODE.finditer(mask_inline_code(value))
    )
    # Preserve the union of overlapping literals. A URL or citation can start
    # before a code span yet end inside it; neither protected tail may disappear.
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return [value[start:end] for start, end in merged]


def _force_words(value: str) -> list[str]:
    result: list[str] = []
    modals = {"ca": "can", "wo": "will", "sha": "shall"}
    for match in _FORCE_WORD.finditer(value):
        word = " ".join(match.group(0).casefold().split())
        word = word.translate(_CANONICAL_APOSTROPHES)
        if word == "cannot":
            result.extend(("can", "not"))
        elif word.endswith("n't"):
            stem = word[:-3]
            modal = modals.get(stem, stem)
            if modal in {
                "can",
                "will",
                "shall",
                "could",
                "should",
                "would",
                "must",
                "might",
                "may",
            }:
                result.append(modal)
            result.append("not")
        else:
            result.append(word)
    return result


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
    comparison = _deletion_case_comparison(source, replacement, candidates)
    if comparison is None:
        return False
    return _matches_candidate_deletions(source, comparison, candidates)


def _deletion_case_comparison(
    source: str, replacement: str, candidates: tuple[UnslopCandidate, ...]
) -> str | None:
    comparison = replacement
    first_source = next((index for index, char in enumerate(source) if char.isalpha()), None)
    first_replacement = next(
        (index for index, char in enumerate(replacement) if char.isalpha()), None
    )
    if (
        first_source is None
        or first_replacement is None
        or not any(candidate.start <= first_source < candidate.end for candidate in candidates)
    ):
        return comparison
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
        source_survivor is None
        or source[source_survivor].casefold() != replacement[first_replacement].casefold()
        or source[source_survivor] == replacement[first_replacement]
    ):
        return comparison
    survivor_word = re.match(r"[A-Za-z]+", source[source_survivor:])
    if survivor_word is None or survivor_word.group(0).casefold() not in _CAPITALIZABLE_START_WORDS:
        return None
    return (
        replacement[:first_replacement]
        + source[source_survivor]
        + replacement[first_replacement + 1 :]
    )


def _matches_candidate_deletions(
    source: str, comparison: str, candidates: tuple[UnslopCandidate, ...]
) -> bool:

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
