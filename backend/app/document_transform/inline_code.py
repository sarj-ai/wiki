import re


INLINE_CODE_POLICY_VERSION = "equal-backtick-runs-v1"
_DELIMITER = re.compile(r"`+|\n")


def inline_code_spans(source: str) -> tuple[tuple[int, int], ...]:
    tokens = list(_DELIMITER.finditer(source))
    next_delimiter: dict[int, int] = {}
    partners: dict[int, int] = {}
    # Match whole, equal-length runs. Shorter or longer runs inside code are
    # literal content. Unmatched openers must not repeatedly rescan the suffix.
    for index in range(len(tokens) - 1, -1, -1):
        delimiter = tokens[index].group()
        if delimiter == "\n":
            next_delimiter.clear()
        else:
            length = len(delimiter)
            if length in next_delimiter:
                partners[index] = next_delimiter[length]
            next_delimiter[length] = index
    spans = []
    index = 0
    while index < len(tokens):
        partner = partners.get(index)
        if partner is None:
            index += 1
        else:
            spans.append((tokens[index].start(), tokens[partner].end()))
            index = partner + 1
    return tuple(spans)


def mask_inline_code(source: str) -> str:
    parts: list[str] = []
    cursor = 0
    for start, end in inline_code_spans(source):
        parts.extend((source[cursor:start], " " * (end - start)))
        cursor = end
    return "".join((*parts, source[cursor:]))
