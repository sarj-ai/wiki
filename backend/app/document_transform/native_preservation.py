from typing import Any

from app.document_transform.ir import DocumentIR, TextBlock
from app.document_transform.schemas import FindingSeverity, TransformFinding, TransformMode


def preserved_native_findings(
    *,
    original: DocumentIR,
    transformed: DocumentIR,
    findings: tuple[TransformFinding, ...],
    native_document: dict[str, Any] | None,
    mode: TransformMode,
) -> tuple[TransformFinding, ...] | None:
    if not findings:
        return ()
    if (
        mode not in {TransformMode.FORMAT, TransformMode.BOTH}
        or original.source_format != "google_doc"
        or native_document is None
        or len(original.blocks) != len(transformed.blocks)
    ):
        return None
    unchanged = {
        before.id: before
        for before, after in zip(original.blocks, transformed.blocks, strict=True)
        if before == after and not before.editable
    }
    result: list[TransformFinding] = []
    for finding in findings:
        block = unchanged.get(finding.block_id or "")
        if (
            finding.code != "google_doc_text_target_unsupported"
            or block is None
            or not _preservation_is_verified(block, native_document)
        ):
            return None
        result.append(
            TransformFinding(
                code="native_rich_text_preserved",
                block_id=block.id,
                detail=(
                    "Rich text wording and native structure were preserved; supported font formatting was checked."
                    if block.font_style_ranges and not block.preserved_font_family
                    else "Text and protected native elements in this paragraph were preserved without modification"
                ),
                severity=FindingSeverity.INFORMATIONAL,
            )
        )
    return tuple(result)


def _preservation_is_verified(block: TextBlock, document: dict[str, Any]) -> bool:
    source = block.source_range
    if source is None or source.segment_id is not None:
        return False
    tab_id, start_index, end_index = source.tab_id, source.start_index, source.end_index
    if (
        type(start_index) is not int
        or type(end_index) is not int
        or not 0 <= start_index < end_index
    ):
        return False
    paragraphs: list[dict[str, Any]] = []

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                paragraphs.extend(
                    element
                    for element in tab.get("documentTab", {}).get("body", {}).get("content", [])
                    if isinstance(element, dict)
                    and element.get("startIndex") == start_index
                    and element.get("endIndex") == end_index + 1
                )
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    if len(paragraphs) != 1 or not isinstance(paragraphs[0].get("paragraph"), dict):
        return False
    parts = paragraphs[0]["paragraph"].get("elements", [])
    if not isinstance(parts, list):
        return False
    plain = all(isinstance(part, dict) and isinstance(part.get("textRun"), dict) for part in parts)
    if not block.font_style_ranges:
        # This is exactly the non-plain paragraph branch of the existing protected
        # inventory: its complete native payload is hashed before and after.
        return not plain
    if not plain:
        return False
    cursor = source.start_index
    for span in block.font_style_ranges:
        if type(span.start_index) is not int or type(span.end_index) is not int:
            return False
        if (
            span.tab_id != source.tab_id
            or span.segment_id is not None
            or span.start_index != cursor
            or span.end_index <= span.start_index
            or span.end_index > source.end_index
        ):
            return False
        cursor = span.end_index
    return cursor == source.end_index
