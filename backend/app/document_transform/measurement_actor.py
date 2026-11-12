import re
from types import MappingProxyType

from app.document_transform.quantity_notation import HORIZONTAL_SPACE
from app.document_transform.quotations import QUOTED_SPAN_PATTERN


_ACTIONS = MappingProxyType(
    {
        "estimate": r"estimat(?:e|es|ed|ing)",
        "forecast": r"forecast(?:s|ed|ing)?",
        "prediction": r"predict(?:s|ed|ing)?",
        "projection": r"project(?:s|ed|ing)?",
        "measurement": r"measur(?:e|es|ed|ing)",
        "reading": r"read(?:s|ing)?",
    }
)
_NOMINAL = re.compile(
    rf"\b(?P<label>(?!(?:The|An|A)\b)[A-Z][\w-]*"
    rf"(?:{HORIZONTAL_SPACE}+[A-Z][\w-]*){{0,3}}){HORIZONTAL_SPACE}+"
    rf"(?P<noun>(?i:{'|'.join(_ACTIONS)}))\b"
)
_LITERAL = re.compile(rf"```[\s\S]*?```|`[^`\n]+`|{QUOTED_SPAN_PATTERN}|\[[^\]\n]+\]\([^)\n]+\)")
_QUANTIFIED_OBJECT = (
    rf"{HORIZONTAL_SPACE}+(?:that\b|"
    rf"(?:(?:approximately|about|roughly){HORIZONTAL_SPACE}+)?[+−\-\d$€£≤≥<>≈~`])"
)


def reassigned_measurement_action(source: str, replacement: str) -> bool:
    if source == replacement or _NOMINAL.search(source) is None:
        return False
    before = _LITERAL.sub(lambda match: " " * len(match.group()), source)
    after = _LITERAL.sub(lambda match: " " * len(match.group()), replacement)
    stated_source = _NOMINAL.sub(lambda match: " " * len(match.group()), before)
    labels = {
        (" ".join(match["label"].split()), match["noun"].casefold())
        for match in _NOMINAL.finditer(before)
    }
    for label, noun in labels:
        subject = re.escape(label).replace(r"\ ", f"{HORIZONTAL_SPACE}+")
        action = rf"\b{subject}{HORIZONTAL_SPACE}+{_ACTIONS[noun]}\b"
        # A name modifying "estimate" is not an explicit estimator. Preserve the
        # nominal attribution unless the source already states its action.
        # Literal examples cannot supply that source attribution.
        stated = re.search(
            rf"\b{subject}{HORIZONTAL_SPACE}+"
            rf"(?:(?:has|have|had|is|are|was|were|been){HORIZONTAL_SPACE}+)*"
            rf"{_ACTIONS[noun]}\b"
            rf"(?!{HORIZONTAL_SPACE}+(?:is|are|was|were|may|might|will)\b)"
            rf"(?={HORIZONTAL_SPACE}+\S)",
            stated_source,
            re.IGNORECASE,
        )
        if stated is None and re.search(action + _QUANTIFIED_OBJECT, after, re.IGNORECASE):
            return True
    return False
