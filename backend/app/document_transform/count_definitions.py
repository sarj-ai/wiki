from dataclasses import dataclass
import hashlib
import re

from app.document_transform.inline_code import inline_code_spans, mask_inline_code
from app.document_transform.quotations import QUOTED_SPAN_PATTERN, quotation_scan_sources


_FENCED_CODE = re.compile(r"```[\s\S]*?```")
_QUOTATION = re.compile(QUOTED_SPAN_PATTERN)
_SUBJECT = r"(?P<subject>[^,;:.!?\n]{1,120}?)"
_BOUNDARY = r"(?:^|[,;.!?\n])\s*"
_COPULA = r"(?:\s+(?:simply\s+)?(?:is|are|means|denotes|represents)\s+|\s*:\s*)"
_COUNT_NOUN = r"(?:the\s+|a\s+)?(?:number|count)\s+of\s+"
_ENTITIES = r"(?P<entities>[^,;:.!?\n]+)"
_DEFINITION = re.compile(_BOUNDARY + _SUBJECT + _COPULA + _COUNT_NOUN + _ENTITIES, re.IGNORECASE)
_RETAINED = (
    _DEFINITION,
    re.compile(_BOUNDARY + _SUBJECT + r"\s+counts?\s+" + _ENTITIES, re.IGNORECASE),
    re.compile(_BOUNDARY + _SUBJECT + _COPULA + r"how\s+many\s+" + _ENTITIES, re.IGNORECASE),
    re.compile(
        _BOUNDARY + _SUBJECT + _COPULA + _ENTITIES + r"\s+(?:count|number)\s*(?=[,;.!?\n]|$)",
        re.IGNORECASE,
    ),
)
_FUNCTION_WORDS = frozenset({"the", "a", "an", "is", "are", "was", "were"})


@dataclass(frozen=True, slots=True)
class _CountDefinition:
    subject: str
    entities: frozenset[str]


def count_definition_changed(source: str, replacement: str) -> bool:
    if source == replacement:
        return False
    before = _definitions(source, (_DEFINITION,))
    if not before:
        return False
    after = _definitions(replacement, _RETAINED)
    source_words = _entity_words(source)
    for original in sorted(before, key=lambda item: len(item.entities), reverse=True):
        retained = next(
            (
                index
                for index, proposed in enumerate(after)
                if original.subject == proposed.subject
                and _same_entities(original.entities, proposed.entities, source_words)
            ),
            None,
        )
        if retained is None:
            return True
        after.pop(retained)
    return False


def _definitions(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[_CountDefinition]:
    # Mask only syntax, retaining exact offsets and original literal identities.
    # An example cannot supply a predicate; a quoted/code term can label one.
    prose = _literal_projection(text, hashed=False)
    matches = {
        (
            _definition_context(text, prose, start=match.start("subject")),
            _CountDefinition(
                " ".join(
                    _literal_identities(text[slice(*match.span("subject"))]).split()
                ).removeprefix("the "),
                _entity_words(text[slice(*match.span("entities"))]),
            ),
        ): None
        for pattern in patterns
        for match in pattern.finditer(prose)
    }
    # Different source contexts need distinct count predicates. Repeated facts
    # in the same context may collapse, as may overlapping syntax alternatives.
    return [definition for _context, definition in matches]


def _definition_context(text: str, prose: str, *, start: int) -> str:
    beginning = max(prose.rfind(mark, 0, start) for mark in ".;!?\n") + 1
    return " ".join(_literal_identities(text[beginning:start]).split())


def _literal_identities(text: str) -> str:
    return _literal_projection(text, hashed=True).casefold()


def _literal_projection(text: str, *, hashed: bool) -> str:
    parts: list[str] = []
    cursor = 0
    for start, end in _literal_spans(text):
        replacement = (
            f"literal{hashlib.sha256(text[start:end].encode()).hexdigest()}"
            if hashed
            else "x" * (end - start)
        )
        parts.extend((text[cursor:start], replacement))
        cursor = end
    return "".join((*parts, text[cursor:]))


def _literal_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = list(inline_code_spans(text))
    inline_masked = mask_inline_code(text)
    spans.extend(match.span() for match in _FENCED_CODE.finditer(inline_masked))
    quoted_prose = _FENCED_CODE.sub(lambda match: " " * len(match.group()), inline_masked)
    spans.extend(
        match.span()
        for reading in quotation_scan_sources(quoted_prose)
        for match in _QUOTATION.finditer(reading)
    )
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            left, right = merged[-1]
            merged[-1] = left, max(right, end)
        else:
            merged.append((start, end))
    return tuple(merged)


def _entity_words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", _literal_identities(text))) - _FUNCTION_WORDS


def _same_entities(
    before: frozenset[str], after: frozenset[str], source_words: frozenset[str]
) -> bool:
    # Count compounds commonly use a singular noun (jobs -> job count). Keep
    # source words rather than guessing synonyms or dropping entity qualifiers.
    left, right = tuple(map(_noun_forms, before)), tuple(map(_noun_forms, after))
    available = tuple(map(_noun_forms, source_words))
    # Source context may move beside its entities ("in the service"). New
    # qualifiers still need the complete original-bound factual review.
    return all(any(word & other for other in right) for word in left) and all(
        any(word & other for other in available) for word in right
    )


def _noun_forms(word: str) -> frozenset[str]:
    forms = {word}
    if word.endswith("s") and not word.endswith("ss"):
        forms.add(word[:-1])
    if word.endswith("es"):
        forms.add(word[:-2])
    if word.endswith("ies"):
        forms.add(f"{word[:-3]}y")
    return frozenset(forms)
