# Only whole plain-text paragraphs qualify. Existing chips, mixed content, code,
# quotes and ambiguous dates remain untouched. The caller must bind plans to a
# source revision and execute an atomic, marker-guarded stage after text edits.

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, utf16_length


if TYPE_CHECKING:
    from app.document_transform.brand import DocsRequest


MAX_NATIVE_ELEMENTS = 100


class NativeElementKind(StrEnum):
    DATE = "date"
    RESOURCE = "resource"
    PERSON = "person"


@dataclass(frozen=True, slots=True)
class NativeElementPolicy:
    # Access/identity must already have been resolved as the requesting user.
    verified_resource_uris: frozenset[str] = frozenset()
    verified_person_emails: frozenset[str] = frozenset()
    max_elements: int = MAX_NATIVE_ELEMENTS

    def __post_init__(self) -> None:
        if not 1 <= self.max_elements <= MAX_NATIVE_ELEMENTS:
            msg = "Native element limit must be between 1 and 100"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class NativeElementProposal:
    block_id: str
    source_sha256: str
    source_range: SourceRange
    original_text: str
    kind: NativeElementKind
    normalized_value: str

    def requests(self) -> list[DocsRequest]:
        # Never send a replacement independently of revision guards.
        source = self.source_range
        location = {"index": source.start_index, "tabId": source.tab_id}
        deletion = {
            "deleteContentRange": {
                "range": {
                    "startIndex": source.start_index,
                    "endIndex": source.end_index,
                    "tabId": source.tab_id,
                },
            },
        }
        if self.kind is NativeElementKind.DATE:
            insertion = {
                "insertDate": {
                    "location": location,
                    "dateElementProperties": {
                        "timestamp": f"{self.normalized_value}T00:00:00Z",
                        "timeZoneId": "Etc/UTC",
                        "locale": "en",
                        "dateFormat": "DATE_FORMAT_ISO8601",
                        "timeFormat": "TIME_FORMAT_DISABLED",
                    },
                },
            }
        elif self.kind is NativeElementKind.PERSON:
            insertion = {
                "insertPerson": {
                    "location": location,
                    "personProperties": {"email": self.original_text},
                },
            }
        else:
            insertion = {
                "insertRichLink": {
                    "location": location,
                    "richLinkProperties": {"uri": self.original_text},
                },
            }
        return [deletion, insertion]


DEFAULT_NATIVE_ELEMENT_POLICY = NativeElementPolicy()


def plan_native_elements(
    document: DocumentIR,
    *,
    policy: NativeElementPolicy = DEFAULT_NATIVE_ELEMENT_POLICY,
) -> tuple[NativeElementProposal, ...]:
    # Bound proposals in descending source order. Execution additionally requires
    # native-content semantic verification and a durable guarded checkpoint.
    proposals = []
    for block in document.blocks:
        source = block.source_range
        if not block.editable or block.kind is not BlockKind.PARAGRAPH:
            continue
        if (
            source is None
            or source.segment_id is not None
            or source.start_index < 1
            or source.end_index - source.start_index != utf16_length(block.text)
        ):
            continue
        kind = _native_kind(block.text, policy)
        if kind is not None:
            proposals.append(
                NativeElementProposal(
                    block.id,
                    block.sha256,
                    source,
                    block.text,
                    kind,
                    _native_value(block.text, kind),
                ),
            )
        if len(proposals) >= policy.max_elements:
            break
    return tuple(
        sorted(
            proposals,
            key=lambda proposal: (
                proposal.source_range.tab_id,
                -proposal.source_range.start_index,
            ),
        ),
    )


def _native_kind(text: str, policy: NativeElementPolicy) -> NativeElementKind | None:
    if _unambiguous_date(text) is not None:
        return NativeElementKind.DATE
    if text in policy.verified_person_emails and re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        text,
    ):
        return NativeElementKind.PERSON
    if text not in policy.verified_resource_uris:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if (
        parsed.scheme == "https"
        and parsed.netloc in {"docs.google.com", "drive.google.com"}
        and parsed.path.startswith(
            ("/document/d/", "/spreadsheets/d/", "/presentation/d/", "/file/d/")
        )
        and not any(character.isspace() for character in text)
    ):
        return NativeElementKind.RESOURCE
    return None


def _native_value(text: str, kind: NativeElementKind) -> str:
    if kind is NativeElementKind.DATE:
        value = _unambiguous_date(text)
        if value is None:
            msg = "Date proposal lost its normalized value"
            raise ValueError(msg)
        return value.isoformat()
    return text


def _unambiguous_date(text: str) -> date | None:
    formats = (
        "%Y-%m-%d",
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%d %B %Y",
        "%d %b %Y",
    )
    for format_value in formats:
        try:
            return datetime.strptime(text, format_value).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None
