from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata

from app.document_transform.quantity_notation import UNIT_POWER, UNIT_PRODUCT, UNIT_SYMBOL


_MISSING = frozenset({"", "-", "–", "—", "n/a", "na", "null", "…"})
_CHARACTER_WIDTH_TWIPS = 110  # Conservative 5.5 pt at the shared 10 pt table font size.
_INTEGER = r"(?:\d{1,3}(?:[,'’\u00a0\u2009\u202f ]\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)"
_COMMA_INTEGER = r"(?:\d{1,3}(?:[.\u00a0\u2009\u202f ]\d{3})+|\d+)"
_DOT_GROUPED_INTEGER = r"\d{1,3}(?:\.\d{3})+"
_ARABIC_NUMBER = r"(?:(?:\d{1,3}(?:٬\d{3})+|\d+)(?:٫\d+)?|٫\d+)"
_DECIMAL = (
    rf"(?:{_INTEGER}(?:\.\d+)?|{_COMMA_INTEGER},\d+|"
    rf"{_DOT_GROUPED_INTEGER}|{_ARABIC_NUMBER}|[.,]\d+)"
)
_POWER = UNIT_POWER
_SCIENTIFIC = rf"(?:{_DECIMAL}(?:\(\d+\))?(?:[eE][+−-]?\d+|\s*[×x·]\s*10{_POWER})?|10{_POWER})"
_VULGAR_FRACTION = r"[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]"
_FRACTION = rf"{_SCIENTIFIC}\s*[/⁄]\s*[+−-]?{_SCIENTIFIC}"
_MAGNITUDE = rf"(?:\d+\s+{_FRACTION}|(?:\d+\s*)?{_VULGAR_FRACTION}|{_FRACTION}|{_SCIENTIFIC})"
_CURRENCY_CODE = r"(?:USD|EUR|GBP|SAR|CHF)"
_CURRENCY = rf"(?:[$€£¥₹₩₽₺]|{_CURRENCY_CODE}(?=\s))"
_NUMBER = rf"(?:[+−-]?\s*(?:{_CURRENCY}\s*)?|{_CURRENCY}\s*[+−-]?)\s*{_MAGNITUDE}"
_UNIT = rf"{UNIT_SYMBOL}(?:{_POWER})?(?:{UNIT_PRODUCT}{UNIT_SYMBOL}(?:{_POWER})?)*"
_QUANTITY_TERM = rf"{_NUMBER}(?:\s*(?:%|‰|×|x|{_CURRENCY_CODE}|{_UNIT}))?"
_QUANTITY = re.compile(
    rf"(?:(?:approximately|about|roughly|approx\.)\s+)?"
    rf"(?:[<>]=?\s*|[≤≥~≈]\s*)?{_QUANTITY_TERM}(?:\s*:\s*{_QUANTITY_TERM})?"
    rf"(?:\s*(?:±|–|−|-|\bto\b)\s*{_QUANTITY_TERM})?",
    re.IGNORECASE,
)


def numeric_table_value(value: str) -> bool:
    # Classify presentation without rewriting measurements, precision, locale,
    # dates, identifiers, or the author's choice of fractions and decimals.
    text = value.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return _QUANTITY.fullmatch(text) is not None


def missing_table_value(value: str) -> bool:
    return value.strip().casefold() in _MISSING


def numeric_columns(rows: tuple[tuple[str, ...], ...], *, header_rows: int = 1) -> frozenset[int]:
    if not rows:
        return frozenset()
    result = set()
    for column in range(len(rows[0])):
        values = [
            row[column].strip()
            for row in rows[header_rows:]
            if not missing_table_value(row[column])
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
    rows: tuple[tuple[str, ...], ...], *, total: int, header_rows: int = 1
) -> tuple[int, ...]:
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        msg = "Table width planning requires a nonempty rectangular table"
        raise ValueError(msg)
    columns = len(rows[0])
    if total < columns:
        msg = "Table width must accommodate every column"
        raise ValueError(msg)
    numeric = numeric_columns(rows, header_rows=header_rows)
    demands = [
        _column_demand(
            [row[column] for row in rows], numeric=column in numeric, header_rows=header_rows
        )
        for column in range(columns)
    ]
    return _distribute_widths(demands, total=total)


@dataclass(frozen=True, slots=True)
class _ColumnDemand:
    minimum: float
    preferred: float


def _column_demand(values: list[str], *, numeric: bool, header_rows: int) -> _ColumnDemand:
    lengths = sorted(_text_width(value) for value in values)
    typical = lengths[math.ceil((len(lengths) - 1) * 0.8)]
    label = max((_text_width(value) for value in values[:header_rows]), default=0)
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
