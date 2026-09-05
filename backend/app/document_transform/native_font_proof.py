from copy import deepcopy
import hashlib
import json
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.ir import (
    MAX_DOCUMENT_BLOCKS,
    DocumentIR,
    parse_google_document,
    utf16_length,
)
from app.identifiers import DocsTabId


class NativeFontProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    tab_id: DocsTabId
    body_ordinal: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    named_styles_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _bodies(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            tab_id = tab["tabProperties"]["tabId"]
            if tab_id in result:
                raise ValueError
            result[tab_id] = tab["documentTab"].get("body", {}).get("content", [])
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    return result


def native_paragraph_font_sha256(
    element: dict[str, Any], *, preserve_font_family: bool = False
) -> str:
    payload = deepcopy(element)
    start, end = payload.pop("startIndex"), payload.pop("endIndex")
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError
    paragraph = payload["paragraph"]
    parts = paragraph["elements"]
    if not isinstance(parts, list) or not parts:
        raise ValueError
    canonical: list[dict[str, Any]] = []
    cursor = start
    for part in parts:
        run_start, run_end = part.pop("startIndex"), part.pop("endIndex")
        run = part["textRun"]
        content = run.pop("content")
        if not isinstance(content, str) or not content:
            raise ValueError
        if (
            type(run_start) is not int
            or type(run_end) is not int
            or run_start != cursor
            or run_end != cursor + utf16_length(content)
        ):
            raise ValueError
        cursor = run_end
        suffix = deepcopy(part) if run_end == end else None
        style = run.get("textStyle", {})
        if not isinstance(style, dict):
            raise TypeError
        family = style.get("weightedFontFamily", {})
        if not isinstance(family, dict):
            raise TypeError
        if "fontFamily" in family and (
            not isinstance(family["fontFamily"], str) or not family["fontFamily"]
        ):
            raise ValueError
        if not preserve_font_family:
            family.pop("fontFamily", None)
            if not family:
                style.pop("weightedFontFamily", None)
            if not style:
                run.pop("textStyle", None)
        if suffix is not None:
            if not content.endswith("\n"):
                raise ValueError
            _append_run(canonical, content[:-1], part)
            # The paragraph terminator is outside the authorized font ranges.
            _append_run(canonical, "\n", suffix)
        else:
            _append_run(canonical, content, part)
    if cursor != end:
        raise ValueError
    paragraph["elements"] = canonical
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _append_run(canonical: list[dict[str, Any]], content: str, properties: dict[str, Any]) -> None:
    if not content:
        return
    # Only exactly equal residual payloads may be coalesced. Unknown fields,
    # links, suggestions, weights and all non-family styles remain bound.
    if canonical and canonical[-1]["properties"] == properties:
        canonical[-1]["content"] += content
    else:
        canonical.append({"content": content, "properties": properties})


def _named_styles_digest(document: dict[str, Any], tab_id: str) -> str:
    matches = []

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            if tab["tabProperties"]["tabId"] == tab_id:
                matches.append(tab["documentTab"].get("namedStyles"))
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    if len(matches) != 1:
        raise ValueError
    return hashlib.sha256(
        json.dumps(
            [document.get("namedStyles"), matches[0]],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def capture_native_font_proof(
    document: dict[str, Any] | None, original: DocumentIR
) -> tuple[NativeFontProof, ...]:
    if document is None:
        raise ValueError
    bodies = _bodies(document)
    result = []
    for block in original.blocks:
        if block.editable or not block.font_style_ranges or block.source_range is None:
            continue
        source = block.source_range
        matches = [
            (ordinal, element)
            for ordinal, element in enumerate(bodies[source.tab_id])
            if element.get("startIndex") == source.start_index
            and element.get("endIndex") == source.end_index + 1
        ]
        if len(matches) != 1:
            raise ValueError
        ordinal, element = matches[0]
        result.append(
            NativeFontProof(
                tab_id=source.tab_id,
                body_ordinal=ordinal,
                sha256=native_paragraph_font_sha256(element),
                named_styles_sha256=_named_styles_digest(document, source.tab_id),
            )
        )
    return tuple(result)


def verify_native_font_proof(
    document: dict[str, Any] | None,
    proofs: tuple[NativeFontProof, ...],
    *,
    preserved_block_ids: tuple[str, ...] = (),
    diagnostic_stage: Literal["resume", "headers", "content"] | None = None,
) -> bool:
    checks: dict[str, str | int | bool] = {
        "font_proof_count": min(len(proofs), MAX_DOCUMENT_BLOCKS),
        "font_proof_verified_count": 0,
        "font_proof_reason": "document_missing",
    }
    valid = _verify_native_font_proof(document, proofs, preserved_block_ids, checks)
    if isinstance(diagnostic_stage, str) and diagnostic_stage in {"resume", "headers", "content"}:
        logger.info(
            "document native font proof checks={checks}",
            checks={**checks, "font_proof_stage": diagnostic_stage, "font_proof_valid": valid},
        )
    return valid


def _verify_native_font_proof(
    document: dict[str, Any] | None,
    proofs: tuple[NativeFontProof, ...],
    preserved_block_ids: tuple[str, ...],
    checks: dict[str, str | int | bool],
) -> bool:
    if document is None:
        return False
    try:
        return _native_font_proof_matches(document, proofs, preserved_block_ids, checks)
    except KeyError, IndexError, TypeError, ValueError, AttributeError, UnsupportedSourceError:
        checks["font_proof_reason"] = "malformed"
        return False


def _native_font_proof_matches(
    document: dict[str, Any],
    proofs: tuple[NativeFontProof, ...],
    preserved_block_ids: tuple[str, ...],
    checks: dict[str, str | int | bool],
) -> bool:
    bodies = _bodies(document)
    identities = {(proof.tab_id, proof.body_ordinal) for proof in proofs}
    if not _coverage_is_complete(document, bodies, identities, preserved_block_ids):
        checks["font_proof_reason"] = "coverage"
        return False
    if len(identities) != len(proofs):
        checks["font_proof_reason"] = "duplicate_binding"
        return False
    for ordinal, proof in enumerate(proofs):
        if native_paragraph_font_sha256(bodies[proof.tab_id][proof.body_ordinal]) != proof.sha256:
            checks["font_proof_reason"] = "paragraph_payload"
            return False
        if _named_styles_digest(document, proof.tab_id) != proof.named_styles_sha256:
            checks["font_proof_reason"] = "parent_styles"
            return False
        checks["font_proof_verified_count"] = min(ordinal + 1, MAX_DOCUMENT_BLOCKS)
    checks["font_proof_reason"] = "matched"
    return True


def _coverage_is_complete(
    document: dict[str, Any],
    bodies: dict[str, list[dict[str, Any]]],
    identities: set[tuple[DocsTabId, int]],
    block_ids: tuple[str, ...],
) -> bool:
    blocks = {block.id: block for block in parse_google_document(document, title="").blocks}
    for block_id in block_ids:
        source = blocks[block_id].source_range
        if source is None:
            return False
        matches = [
            (ordinal, element)
            for ordinal, element in enumerate(bodies[source.tab_id])
            if element.get("startIndex") == source.start_index
            and element.get("endIndex") == source.end_index + 1
        ]
        if len(matches) != 1:
            return False
        ordinal, element = matches[0]
        parts = element["paragraph"]["elements"]
        plain = all(
            isinstance(part, dict) and isinstance(part.get("textRun"), dict) for part in parts
        )
        if plain and (source.tab_id, ordinal) not in identities:
            return False
    return True


def verify_native_unstyled_text(
    source: dict[str, Any], actual: dict[str, Any], original: DocumentIR
) -> bool:
    # COPY-only exact style proof where no font override was authorized.
    try:
        return _verify_native_unstyled_text(source, actual, original)
    except KeyError, IndexError, TypeError, ValueError, AttributeError:
        return False


def _verify_native_unstyled_text(
    source: dict[str, Any], actual: dict[str, Any], original: DocumentIR
) -> bool:
    before_bodies, after_bodies = _bodies(source), _bodies(actual)
    for block in original.blocks:
        if (
            block.editable
            or block.font_style_ranges
            or not block.text
            or block.source_range is None
        ):
            continue
        location = block.source_range
        matches = [
            (ordinal, element)
            for ordinal, element in enumerate(before_bodies[location.tab_id])
            if element.get("startIndex") == location.start_index
            and element.get("endIndex") == location.end_index + 1
        ]
        if len(matches) != 1:
            return False
        ordinal, element = matches[0]
        parts = element.get("paragraph", {}).get("elements", [])
        if not all(
            isinstance(part, dict) and isinstance(part.get("textRun"), dict) for part in parts
        ):
            # Existing protected inventory binds the entire nonplain payload.
            continue
        if native_paragraph_font_sha256(
            element, preserve_font_family=True
        ) != native_paragraph_font_sha256(
            after_bodies[location.tab_id][ordinal], preserve_font_family=True
        ) or _named_styles_digest(source, location.tab_id) != _named_styles_digest(
            actual, location.tab_id
        ):
            return False
    return True
