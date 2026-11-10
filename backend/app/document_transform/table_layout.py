from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata


_MISSING = frozenset({"", "-", "–", "—", "n/a", "na", "null", "…"})
_CHARACTER_WIDTH_TWIPS = 110  # Conservative 5.5 pt at the shared 10 pt table font size.
_NUMBER = r"[+−-]?(?:\d[\d,\u2009\u202f ]*(?:\.\d+)?|\.\d+)(?:[eE][+−-]?\d+)?"
_QUANTITY = re.compile(
    rf"(?:[<>≤≥~≈]\s*)?[$€£¥]?\s*(?:{_NUMBER}(?:\s*[/⁄:]\s*{_NUMBER})?|"
    r"[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])"
    rf"(?:\s*(?:±|–|−|-)\s*{_NUMBER})?"
    r"(?:\s*(?:%|‰|ns|[µμu]s|ms|s|min|h|d|B|[KMGTPE]i?B|[kKMGT]bps|"
    r"Hz|[kMGT]Hz|mm|cm|m|km|mg|g|kg|mL|L|°[CF]|×|x|USD|EUR|GBP|SAR))?",
    re.IGNORECASE,
)


def numeric_table_value(value: str) -> bool:
    # Classify presentation without rewriting measurements, precision, locale,
    # dates, identifiers, or the author's choice of fractions and decimals.
    text = value.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return _QUANTITY.fullmatch(text) is not None


def numeric_columns(rows: tuple[tuple[str, ...], ...], *, header: bool = True) -> frozenset[int]:
    if not rows:
        return frozenset()
    result = set()
    for column in range(len(rows[0])):
        values = [
            row[column].strip()
            for row in rows[int(header) :]
            if row[column].strip().casefold() not in _MISSING
        ]
        if values and all(numeric_table_value(value) for value in values):
            result.add(column)
    return frozenset(result)


def _text_width(value: str) -> float:
    width = 0.0
    for char in value:
        if unicodedata.combining(char):
            continue
        if unicodedata.east_asian_width(char) in {"W", "F"}:
            width += 1.8
        elif char in "ilI.,:;|'! ":
            width += 0.45
        elif char in "MW@%":
            width += 1.35
        else:
            width += 1
    return width


def table_column_widths(
    rows: tuple[tuple[str, ...], ...], *, total: int, header: bool = True
) -> tuple[int, ...]:
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        msg = "Table width planning requires a nonempty rectangular table"
        raise ValueError(msg)
    columns = len(rows[0])
    if total < columns:
        msg = "Table width must accommodate every column"
        raise ValueError(msg)
    numeric = numeric_columns(rows, header=header)
    demands = [
        _column_demand([row[column] for row in rows], numeric=column in numeric, header=header)
        for column in range(columns)
    ]
    return _distribute_widths(demands, total=total)


@dataclass(frozen=True, slots=True)
class _ColumnDemand:
    minimum: float
    preferred: float


def _column_demand(values: list[str], *, numeric: bool, header: bool) -> _ColumnDemand:
    lengths = sorted(_text_width(value) for value in values)
    typical = lengths[math.ceil((len(lengths) - 1) * 0.8)]
    label = _text_width(values[0]) if header else 0
    longest_word = max((_text_width(word) for value in values for word in value.split()), default=0)
    if numeric:
        minimum = min(max(lengths) + 3, 24)
        preferred = max(minimum, min(label + 3, 28))
    else:
        minimum = max(9, min(longest_word + 3, 24))
        preferred = max(minimum, min(max(typical, label) + 3, 64))
    return _ColumnDemand(minimum, preferred)


def _distribute_widths(demands: list[_ColumnDemand], *, total: int) -> tuple[int, ...]:
    # Allocate a readable minimum before distributing spare room. Numeric
    # columns need enough room for quantities; descriptions receive the rest.
    floors = [item.minimum * _CHARACTER_WIDTH_TWIPS for item in demands]
    if sum(floors) >= total:
        raw = [total * value / sum(floors) for value in floors]
    else:
        spare = total - sum(floors)
        weights = [max(1, item.preferred - item.minimum) for item in demands]
        raw = [
            floor + spare * weight / sum(weights)
            for floor, weight in zip(floors, weights, strict=True)
        ]
    widths = [max(1, int(value)) for value in raw]
    widths[-1] += total - sum(widths)
    return tuple(widths)
