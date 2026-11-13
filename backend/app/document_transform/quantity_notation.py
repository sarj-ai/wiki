import re


HORIZONTAL_SPACE = r"[^\S\r\n\v\f\u0085\u2028\u2029]"
TEMPERATURE_UNIT = rf"(?:°{HORIZONTAL_SPACE}*[CFcf]|[℃℉KK])"
UNIT_SYMBOL = (
    r"(?:[\"'′″]|ns|[µμu]s|ms|s|min|h|d|bit|[kKMGTPE]i?bit|B|[KMGTPE]i?B|"
    r"[kKMGT]bps|Hz|[kMGT]Hz|"
    rf"mm|cm|m|km|mg|g|kg|mL|L|{TEMPERATURE_UNIT}|mol|mmol|Pa|[kMGT]Pa|W|[kMGT]W|"
    r"J|[kMGT]J|V|mV|A|mA|Ω|kΩ|N)"
)
UNIT_POWER = rf"(?:{HORIZONTAL_SPACE}*(?:\^|\*\*){HORIZONTAL_SPACE}*[+−-]?\d+|[⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)"
UNIT_PRODUCT = rf"(?:{HORIZONTAL_SPACE}*[/*·]{HORIZONTAL_SPACE}*|{HORIZONTAL_SPACE}+)"
UNIT_ATOM = rf"{UNIT_SYMBOL}(?:{UNIT_POWER})?"
# Parentheses bind the complete denominator, not just the first unit.
UNIT_GROUP = (
    rf"\({HORIZONTAL_SPACE}*{UNIT_ATOM}(?:{UNIT_PRODUCT}{UNIT_ATOM})*"
    rf"{HORIZONTAL_SPACE}*\)(?:{UNIT_POWER})?"
)
UNIT_EXPRESSION = rf"(?:{UNIT_ATOM}|{UNIT_GROUP})(?:{UNIT_PRODUCT}(?:{UNIT_ATOM}|{UNIT_GROUP}))*"

UNIT_ANNOTATION_POLICY_VERSION = "explicit-imperial-annotation-v1"
_ANNOTATION_NUMBER = (
    rf"[+−-]?(?:\d+(?:{HORIZONTAL_SPACE}+\d+)?{HORIZONTAL_SPACE}*/"
    rf"{HORIZONTAL_SPACE}*\d+|\d+(?:\.\d+)?)"
)
UNIT_ANNOTATION_PATTERN = (
    rf"\b(?:inch(?:es)?{HORIZONTAL_SPACE}*\({HORIZONTAL_SPACE}*{_ANNOTATION_NUMBER}"
    rf"{HORIZONTAL_SPACE}*(?P<inch_mark>[\"″])|"
    rf"(?:foot|feet){HORIZONTAL_SPACE}*\({HORIZONTAL_SPACE}*{_ANNOTATION_NUMBER}"
    rf"{HORIZONTAL_SPACE}*(?P<foot_mark>['′])){HORIZONTAL_SPACE}*\)"
)
_UNIT_ANNOTATION = re.compile(UNIT_ANNOTATION_PATTERN, re.IGNORECASE)


def mask_unit_annotation_marks(source: str) -> str:
    # Explicit unit words disambiguate their parenthetical abbreviation. Do not
    # guess whether an arbitrary quote after a digit closes quoted prose.
    characters = list(source)
    for match in _UNIT_ANNOTATION.finditer(source):
        mark = "inch_mark" if match["inch_mark"] is not None else "foot_mark"
        characters[match.start(mark)] = " "
    return "".join(characters)
