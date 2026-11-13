from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
import re
from typing import ClassVar, Literal, NotRequired, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.document_transform.ir import utf16_length


_ADDRESS = re.compile(
    r"(?<!\w)\d{1,6}[A-Za-z]?\s+(?:[NSEW]\.?(?:\s+))?(?:[A-Za-z0-9][A-Za-z0-9’'-]*\.?\s+){0,5}?(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Place|Pl|Way|Broadway)\b\.?",
    re.IGNORECASE,
)
_MONTHS = "January February March April May June July August September October November December"
_MONTH = "(?:" + "|".join(_MONTHS.split()) + ")"
_RANGE_MONTH = (
    "(?:"
    + "|".join((*_MONTHS.split(), *(month[:3] for month in _MONTHS.split()), "Sept"))
    + r")\.?"
)
_DATE = re.compile(
    rf"(?<!\w)(?:\d{{4}}-\d{{2}}-\d{{2}}|{_MONTH}\s+\d{{1,2}}(?:,?\s+\d{{4}})?|\d{{1,2}}\s+{_MONTH}(?:\s+\d{{4}})?|\d{{1,2}}/\d{{1,2}}/\d{{4}})(?!\w)",
    re.IGNORECASE,
)
_CLOCK = r"(?P<{p}hour>\d{{1,2}})(?::(?P<{p}minute>\d{{2}}))?\s*(?P<{p}period>[ap]\.?m)?"
_TIME_RANGE = re.compile(
    r"^[,\s]+"
    + _CLOCK.format(p="a")
    + r"(?:\s*(?:–|—|-|to)\s*"
    + _CLOCK.format(p="b")
    + r")?(?!\w|:)",
    re.IGNORECASE,
)
_ANCHORED_CLOCK = re.compile(
    r"(?<![\w:])(?:\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m)?\s*[–—-]\s*\d{1,2}(?::\d{2})?\s*[ap]\.?m|\d{1,2}(?::\d{2})?\s*[ap]\.?m)(?!\w)",
    re.IGNORECASE,
)
_RECURRING = re.compile(
    r"\b(?:every|daily|weekly|opening hours|business hours|hours:)|\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\s*[–-]\s*(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b",
    re.IGNORECASE,
)
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WEEKDAY_PREFIX = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r"),?\s*$", re.IGNORECASE)
_NYC = re.compile(
    r"\b(?:NYC|New York(?: City)?|Manhattan|Brooklyn|Queens|Bronx|Staten Island)\b", re.IGNORECASE
)
_OTHER_CITY = re.compile(
    r"\b(?:London|Paris|Los Angeles|San Francisco|Chicago|Boston|Riyadh|Dubai)\b", re.IGNORECASE
)
_HALF_DAY_HOURS = 12
_MONTH_COUNT = 12
_RANGE_ENDPOINTS = 2


@dataclass(frozen=True, slots=True)
class NativeChipContext:
    city: str | None = None
    time_zone: str | None = None
    year: int | None = None
    locale: Literal["en-US", "en-GB"] | None = None
    conflicting_city: bool = False
    conflicting_time_zone: bool = False
    anchored_date: date | None = None


class NativeDateProperties(TypedDict):
    timestamp: str
    timeZoneId: NotRequired[str]
    locale: Literal["en"]
    dateFormat: Literal["DATE_FORMAT_ISO8601"]
    timeFormat: Literal["TIME_FORMAT_HOUR_MINUTE_TIMEZONE", "TIME_FORMAT_DISABLED"]


@dataclass(frozen=True, slots=True)
class NativeDateValue:
    timestamp: str
    time_zone: str
    has_time: bool

    def properties(self) -> NativeDateProperties:
        timestamp = self.timestamp
        if not self.has_time:
            local_date = (
                datetime.fromisoformat(timestamp).astimezone(ZoneInfo(self.time_zone)).date()
            )
            timestamp = local_date.isoformat() + "T00:00:00Z"
        properties: NativeDateProperties = {
            "timestamp": timestamp,
            "locale": "en",
            "dateFormat": "DATE_FORMAT_ISO8601",
            "timeFormat": "TIME_FORMAT_HOUR_MINUTE_TIMEZONE"
            if self.has_time
            else "TIME_FORMAT_DISABLED",
        }
        if self.has_time:
            properties["timeZoneId"] = self.time_zone
        return properties


@dataclass(frozen=True, slots=True)
class _NativeSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class ResolvedNativeDateSpan(_NativeSpan):
    resolution: ClassVar[Literal["resolved-date"]] = "resolved-date"
    kind: ClassVar[Literal["date"]] = "date"
    skip_reason: ClassVar[None] = None
    date_value: NativeDateValue


@dataclass(frozen=True, slots=True)
class LocatedNativePlaceSpan(_NativeSpan):
    resolution: ClassVar[Literal["located-place"]] = "located-place"
    kind: ClassVar[Literal["place"]] = "place"
    skip_reason: ClassVar[None] = None
    date_value: ClassVar[None] = None


@dataclass(frozen=True, slots=True)
class BlockedNativeSpan(_NativeSpan):
    resolution: ClassVar[Literal["blocked"]] = "blocked"
    date_value: ClassVar[None] = None
    kind: Literal["date", "place"]
    skip_reason: str


DetectedNativeSpan = ResolvedNativeDateSpan | LocatedNativePlaceSpan | BlockedNativeSpan


def native_chip_context(title: str, context_text: str) -> NativeChipContext:
    context = title + "\n" + context_text
    nyc = bool(_NYC.search(context))
    zones = set(
        re.findall(
            r"\b(?:America|Europe|Asia|Africa|Australia|Pacific|Atlantic|Indian|Etc)/(?:[A-Za-z_]+/)?[A-Za-z_]+\b",
            context,
        )
    )
    explicit_zone = next(iter(zones)) if len(zones) == 1 else None
    years = set(re.findall(r"\b20\d{2}\b", context))
    conflict = nyc and bool(_OTHER_CITY.search(context))
    result = NativeChipContext(
        city="New York City" if nyc and not conflict else None,
        time_zone=explicit_zone if not conflict else None,
        year=int(next(iter(years))) if len(years) == 1 else None,
        conflicting_city=conflict,
        conflicting_time_zone=len(zones) > 1,
    )
    return replace(result, anchored_date=_title_date(title, context=result))


def _title_date(title: str, *, context: NativeChipContext) -> date | None:
    matches = list(_DATE.finditer(title))
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.search(
        rf"(?<!\w)(?:{_RANGE_MONTH}\s*)?\d{{1,2}}(?:\s*{_RANGE_MONTH})?\s*(?:[–—-]|to|through|until|and|or|&)\s*$",
        title[: match.start()],
        re.IGNORECASE,
    ) or re.match(
        rf"\s*(?:[–—-]|to|through|until|and|or|&)\s*(?:{_RANGE_MONTH}\s*)?\d",
        title[match.end() :],
        re.IGNORECASE,
    ):
        return None
    value = _parse_date(match[0], context=context)
    weekday = _WEEKDAY_PREFIX.search(title[: match.start()])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", match[0]) and not (
        weekday or re.search(r"\bDate:\s*$", title[: match.start()], re.IGNORECASE)
    ):
        return None
    if value is None or (weekday and weekday[1].lower() != _WEEKDAYS[value.weekday()].lower()):
        return None
    return value


def detect_address_spans(
    text: str, *, context: NativeChipContext
) -> tuple[DetectedNativeSpan, ...]:
    reason = (
        "conflicting_city_context"
        if context.conflicting_city
        else "missing_city_context"
        if context.city is None
        else None
    )
    return tuple(
        BlockedNativeSpan(
            utf16_length(text[: match.start()]),
            utf16_length(text[: match.end()]),
            match[0],
            "place",
            reason,
        )
        if reason is not None
        else LocatedNativePlaceSpan(
            utf16_length(text[: match.start()]),
            utf16_length(text[: match.end()]),
            match[0],
        )
        for match in _ADDRESS.finditer(text)
    )


def detect_date_spans(text: str, *, context: NativeChipContext) -> tuple[DetectedNativeSpan, ...]:
    spans = tuple(
        span
        for match in _DATE.finditer(text)
        for span in _detected_date_spans(text, match, context=context)
    )
    # An explicit paragraph date owns its local scope. A title date must never
    # assign a different day to a remaining clock, including an unresolved date.
    if spans or context.anchored_date is None or _RECURRING.search(text):
        return spans
    anchored: list[DetectedNativeSpan] = []
    for match in _ANCHORED_CLOCK.finditer(text):
        start = utf16_length(text[: match.start()])
        end = utf16_length(text[: match.end()])
        if not any(start < span.end and span.start < end for span in spans):
            anchored.extend(_anchored_time_spans(text, match, context=context))
    return tuple(sorted((*spans, *anchored), key=lambda span: span.start))


def _detected_date_spans(
    text: str, match: re.Match[str], *, context: NativeChipContext
) -> tuple[DetectedNativeSpan, ...]:
    value = _parse_date(match[0], context=context)
    if value is None:
        return (
            _blocked_date_span(
                text, match.start(), match.end(), reason="ambiguous_or_invalid_date"
            ),
        )
    weekday = _WEEKDAY_PREFIX.search(text[: match.start()])
    if weekday and weekday[1].lower() != _WEEKDAYS[value.weekday()].lower():
        return (
            _blocked_date_span(text, match.start(), match.end(), reason="weekday_date_conflict"),
        )
    times = _TIME_RANGE.match(text[match.end() :])
    if times is None or _RECURRING.search(text):
        return (_date_span(text, match.start(), match.end(), value, context=context),)
    return _timed_date_spans(text, match, times, value, context=context)


def _timed_date_spans(
    text: str,
    match: re.Match[str],
    times: re.Match[str],
    value: date,
    *,
    context: NativeChipContext,
) -> tuple[DetectedNativeSpan, ...]:
    try:
        values = _zoned_time_values(value, times, context=context)
    except ValueError as error:
        return (
            _blocked_date_span(text, match.start(), match.end() + times.end(), reason=str(error)),
        )
    first = _resolved_date_span(
        text,
        match.start(),
        match.end() + _time_endpoint_end(times, "a"),
        value=values[0],
    )
    if len(values) == 1:
        return (first,)
    second = _resolved_date_span(
        text,
        match.end() + times.start("bhour"),
        match.end() + _time_endpoint_end(times, "b"),
        value=values[1],
    )
    return first, second


def _anchored_time_spans(
    text: str, match: re.Match[str], *, context: NativeChipContext
) -> tuple[DetectedNativeSpan, ...]:
    times = _TIME_RANGE.match(" " + match[0])
    if times is None or context.anchored_date is None:
        return ()
    try:
        values = _zoned_time_values(context.anchored_date, times, context=context)
    except ValueError as error:
        return (_blocked_date_span(text, match.start(), match.end(), reason=str(error)),)
    offset = match.start() - 1
    first = _resolved_date_span(
        text, match.start(), offset + _time_endpoint_end(times, "a"), value=values[0]
    )
    if len(values) == 1:
        return (first,)
    second = _resolved_date_span(
        text,
        offset + times.start("bhour"),
        offset + _time_endpoint_end(times, "b"),
        value=values[1],
    )
    return first, second


def _zoned_time_values(
    day: date, times: re.Match[str], *, context: NativeChipContext
) -> tuple[NativeDateValue, ...]:
    if context.conflicting_time_zone:
        msg = "conflicting_time_zone_context"
        raise ValueError(msg)
    zone = context.time_zone or ("America/New_York" if context.city == "New York City" else None)
    if zone is None:
        msg = "missing_time_zone"
        raise ValueError(msg)
    clocks = _range_clocks(times)
    first = NativeDateValue(_local_instant(day, clocks[0], zone), zone, has_time=True)
    if len(clocks) == 1:
        return (first,)
    end_day = day + timedelta(days=1) if clocks[1] <= clocks[0] else day
    return first, NativeDateValue(_local_instant(end_day, clocks[1], zone), zone, has_time=True)


def _time_endpoint_end(match: re.Match[str], prefix: Literal["a", "b"]) -> int:
    for field in ("period", "minute", "hour"):
        if match[prefix + field]:
            return match.end(prefix + field)
    msg = "Time endpoint has no hour"
    raise ValueError(msg)


def _blocked_date_span(text: str, start: int, end: int, *, reason: str) -> BlockedNativeSpan:
    return BlockedNativeSpan(
        utf16_length(text[:start]), utf16_length(text[:end]), text[start:end], "date", reason
    )


def _resolved_date_span(
    text: str, start: int, end: int, *, value: NativeDateValue
) -> ResolvedNativeDateSpan:
    return ResolvedNativeDateSpan(
        utf16_length(text[:start]), utf16_length(text[:end]), text[start:end], value
    )


def _date_span(
    text: str, start: int, end: int, value: date, *, context: NativeChipContext
) -> DetectedNativeSpan:
    if context.conflicting_time_zone:
        return _blocked_date_span(text, start, end, reason="conflicting_time_zone_context")
    zone = context.time_zone or (
        "America/New_York" if context.city == "New York City" else "Etc/UTC"
    )
    try:
        timestamp = _local_instant(value, time(0), zone)
    except ValueError as error:
        return _blocked_date_span(text, start, end, reason=str(error))
    return _resolved_date_span(
        text,
        start,
        end,
        value=NativeDateValue(timestamp, zone, has_time=False),
    )


def _parse_date(text: str, *, context: NativeChipContext) -> date | None:
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text):
        first, second, year = map(int, text.split("/"))
        if first <= _MONTH_COUNT and second <= _MONTH_COUNT and context.locale is None:
            return None
        month, day = (
            (second, first)
            if context.locale == "en-GB" or first > _MONTH_COUNT
            else (first, second)
        )
        try:
            return date(year, month, day)
        except ValueError:
            return None
    has_year = re.search(r"\b\d{4}\b", text)
    if not has_year:
        if context.year is None:
            return None
        text = f"{text} {context.year}"
    for format_value in ("%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, format_value).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _range_clocks(match: re.Match[str]) -> tuple[time, ...]:
    first_period = match["aperiod"]
    second_period = match["bperiod"]
    if (
        not first_period
        and not second_period
        and int(match["ahour"]) <= _HALF_DAY_HOURS
        and (not match["bhour"] or int(match["bhour"]) <= _HALF_DAY_HOURS)
    ):
        msg = "ambiguous_meridiem"
        raise ValueError(msg)
    if not first_period and second_period and int(match["bhour"]) < int(match["ahour"]):
        msg = "ambiguous_range_meridiem"
        raise ValueError(msg)
    first = _clock(match["ahour"], match["aminute"], first_period or second_period)
    if not match["bhour"]:
        return (first,)
    return first, _clock(match["bhour"], match["bminute"], second_period or first_period)


def _clock(hour: str, minute: str | None, period: str | None) -> time:
    number = int(hour)
    if period:
        if not 1 <= number <= _HALF_DAY_HOURS:
            msg = "invalid_clock_time"
            raise ValueError(msg)
        number = number % _HALF_DAY_HOURS + (
            _HALF_DAY_HOURS if period.lower().startswith("p") else 0
        )
    try:
        return time(number, int(minute or 0))
    except ValueError as error:
        msg = "invalid_clock_time"
        raise ValueError(msg) from error


def _local_instant(day: date, clock: time, zone: str) -> str:
    local = datetime.combine(day, clock)
    try:
        timezone = ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        msg = "unsupported_time_zone"
        raise ValueError(msg) from error
    candidates = set()
    for fold in (0, 1):
        instant = local.replace(tzinfo=timezone, fold=fold).astimezone(UTC)
        if instant.astimezone(timezone).replace(tzinfo=None) == local:
            candidates.add(instant)
    if not candidates:
        msg = "nonexistent_local_time"
        raise ValueError(msg)
    if len(candidates) != 1:
        msg = "ambiguous_local_time"
        raise ValueError(msg)
    return next(iter(candidates)).isoformat(timespec="seconds").replace("+00:00", "Z")
