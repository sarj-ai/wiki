from dataclasses import replace
import hashlib
import json
from typing import Any, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field

from app.document_transform.ir import MAX_DOC_TABS, DocumentIR, utf16_length
from app.identifiers import DocsTabId


_MAX_PARAGRAPH_STYLE_BYTES = 8192
ParagraphLayoutBlockId = NewType("ParagraphLayoutBlockId", str)


class NativeParagraphLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    block_id: ParagraphLayoutBlockId = Field(pattern=r"^b[0-9]+$", max_length=12)
    tab_id: DocsTabId
    body_ordinal: int = Field(ge=0, strict=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    expected_start: int = Field(ge=0, strict=True)
    expected_end: int = Field(gt=0, strict=True)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    style_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parents_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def capture_paragraph_layout(
    document: dict[str, Any] | None, original: DocumentIR, transformed: DocumentIR
) -> tuple[NativeParagraphLayout, ...]:
    if document is None:
        return ()
    tabs = _tabs(document)
    deltas: dict[DocsTabId, int] = {}
    result = []
    if len(original.blocks) != len(transformed.blocks) or original.tab_ids != transformed.tab_ids:
        raise ValueError
    for before, after in zip(original.blocks, transformed.blocks, strict=True):
        source = before.source_range
        if source is None:
            continue
        delta = deltas.get(source.tab_id, 0)
        deltas[source.tab_id] = delta + utf16_length(after.text) - utf16_length(before.text)
        if not before.editable:
            continue
        matches = [
            (ordinal, element)
            for ordinal, element in enumerate(
                tabs[source.tab_id].get("body", {}).get("content", [])
            )
            if element.get("startIndex") == source.start_index
            and element.get("endIndex") == source.end_index + 1
        ]
        if len(matches) != 1:
            raise ValueError
        ordinal, element = matches[0]
        style = element.get("paragraph", {}).get("paragraphStyle", {})
        if not isinstance(style, dict) or style.get("alignment") not in {
            "CENTER",
            "END",
            "JUSTIFIED",
        }:
            continue
        if (
            source.segment_id is not None
            or before.id != after.id
            or before.source_range != after.source_range
            or source.end_index - source.start_index != utf16_length(before.text)
        ):
            raise ValueError
        result.append(
            NativeParagraphLayout(
                block_id=ParagraphLayoutBlockId(before.id),
                tab_id=source.tab_id,
                body_ordinal=ordinal,
                start=source.start_index,
                end=source.end_index,
                expected_start=source.start_index + delta,
                expected_end=source.start_index + delta + utf16_length(after.text),
                text_sha256=before.sha256,
                expected_text_sha256=after.sha256,
                style_sha256=paragraph_layout_sha256(style),
                parents_sha256=_parents(document, tabs[source.tab_id]),
            )
        )
    return tuple(result)


def apply_paragraph_layout(
    document: DocumentIR, proof: tuple[NativeParagraphLayout, ...]
) -> DocumentIR:
    identities: dict[str, NativeParagraphLayout] = {item.block_id: item for item in proof}
    matches = {
        block.id
        for block in document.blocks
        if block.id in identities
        and block.source_range is not None
        and block.source_range.tab_id == identities[block.id].tab_id
        and block.sha256
        in {identities[block.id].text_sha256, identities[block.id].expected_text_sha256}
    }
    if len(identities) != len(proof) or matches != identities.keys():
        raise ValueError
    return replace(
        document,
        blocks=tuple(
            replace(block, preserved_paragraph_style_sha256=identities[block.id].style_sha256)
            if block.id in identities
            and block.sha256
            in {
                identities[block.id].text_sha256,
                identities[block.id].expected_text_sha256,
            }
            else block
            for block in document.blocks
        ),
    )


def verify_paragraph_layout(
    document: dict[str, Any] | None,
    proof: tuple[NativeParagraphLayout, ...],
    *,
    applied: bool | None = None,
) -> bool:
    if not proof:
        return True
    try:
        if document is None:
            return False
        tabs = _tabs(document)
        if len({(item.tab_id, item.body_ordinal) for item in proof}) != len(proof):
            return False
        phases = (False, True) if applied is None else (applied,)
        return any(
            all(_matches(document, tabs, item, after=phase) for item in proof) for phase in phases
        )
    except KeyError, IndexError, TypeError, ValueError, AttributeError:
        return False


def paragraph_layout_requests_safe(
    requests: list[dict[str, Any]], proof: tuple[NativeParagraphLayout, ...]
) -> bool:
    if not proof:
        return True
    for request in requests:
        if "updateParagraphStyle" not in request:
            continue
        update = request["updateParagraphStyle"]
        if not isinstance(update, dict):
            return False
        target = update.get("range")
        if not isinstance(target, dict):
            return False
        if target.get("segmentId"):
            if not isinstance(target["segmentId"], str):
                return False
            continue
        start, end = target.get("startIndex"), target.get("endIndex")
        if type(start) is not int or type(end) is not int or start >= end:
            return False
        if any(
            (target.get("tabId") is None or target["tabId"] == item.tab_id)
            and start < item.expected_end + 1
            and end > item.expected_start
            for item in proof
        ):
            return False
    return True


def paragraph_layout_sha256(style: dict[str, Any]) -> str:
    encoded = json.dumps(style, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > _MAX_PARAGRAPH_STYLE_BYTES:
        raise ValueError
    return hashlib.sha256(encoded).hexdigest()


def _tabs(document: dict[str, Any]) -> dict[DocsTabId, dict[str, Any]]:
    result: dict[DocsTabId, dict[str, Any]] = {}

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            raw_id = tab["tabProperties"]["tabId"]
            if not isinstance(raw_id, str):
                raise TypeError
            tab_id = DocsTabId(raw_id)
            if not tab_id or tab_id in result or len(result) >= MAX_DOC_TABS:
                raise ValueError
            result[tab_id] = tab["documentTab"]
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    return result


def _parents(document: dict[str, Any], tab: dict[str, Any]) -> str:
    encoded = json.dumps(
        [document.get("namedStyles"), tab.get("namedStyles")],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _matches(
    document: dict[str, Any],
    tabs: dict[DocsTabId, dict[str, Any]],
    proof: NativeParagraphLayout,
    *,
    after: bool,
) -> bool:
    tab = tabs[proof.tab_id]
    element = tab["body"]["content"][proof.body_ordinal]
    paragraph = element["paragraph"]
    text = "".join(part["textRun"]["content"] for part in paragraph["elements"])
    return (
        type(element.get("startIndex")) is int
        and type(element.get("endIndex")) is int
        and element["startIndex"] == (proof.expected_start if after else proof.start)
        and element["endIndex"] == (proof.expected_end if after else proof.end) + 1
        and text.endswith("\n")
        and hashlib.sha256(text[:-1].encode()).hexdigest()
        == (proof.expected_text_sha256 if after else proof.text_sha256)
        and paragraph_layout_sha256(paragraph["paragraphStyle"]) == proof.style_sha256
        and _parents(document, tab) == proof.parents_sha256
    )
