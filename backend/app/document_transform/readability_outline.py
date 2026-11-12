from pydantic import JsonValue

from app.document_transform.ir import BlockKind, DocumentIR, TextBlock
from app.document_transform.readability_json import array, bodies, content, integer, obj
from app.document_transform.readability_types import ReadabilityProposal
from app.identifiers import DocsTabId


_BODY_OBJECTS = frozenset({"inlineObjectElement", "equation", "person", "dateElement", "richLink"})


def validate_proposed_outline(
    document: dict[str, JsonValue], ir: DocumentIR, proposals: list[ReadabilityProposal]
) -> None:
    by_id = {proposal.block_id: proposal for proposal in proposals}
    positions = {
        (block.source_range.tab_id, block.source_range.start_index): block
        for block in ir.blocks
        if block.source_range is not None and block.source_range.segment_id is None
    }
    contentful = _contentful_authored_headings(document, positions)
    for tab_id, tab in bodies(document):
        pending: list[int] = []
        for value in content(tab):
            element = obj(value)
            if "table" in element or "tableOfContents" in element:
                pending.clear()
            elif "paragraph" in element:
                block = positions.get((tab_id, integer(element["startIndex"])))
                has_content = _paragraph_has_content(element)
                if block is not None:
                    _block_outline(
                        pending,
                        block,
                        by_id.get(block.id),
                        has_content=has_content,
                        authored_content=(tab_id, integer(element["startIndex"])) in contentful,
                    )
                elif has_content:
                    pending.clear()
        if pending:
            _orphan_heading()


def _block_outline(
    pending: list[int],
    block: TextBlock,
    proposal: ReadabilityProposal | None,
    *,
    has_content: bool,
    authored_content: bool,
) -> None:
    if proposal is None:
        _outline_part(
            pending,
            level=(block.level or 1) if block.kind is BlockKind.HEADING else None,
            proposed=authored_content,
            content=has_content,
        )
    else:
        for part in proposal.parts:
            level = {"heading2": 2, "heading3": 3}.get(part.role)
            _outline_part(pending, level=level, proposed=True, content=bool(part.text.strip()))


def _paragraph_has_content(element: dict[str, JsonValue]) -> bool:
    for value in array(obj(element["paragraph"])["elements"]):
        part = obj(value)
        if _BODY_OBJECTS.intersection(part):
            return True
        if "textRun" in part:
            text = obj(part["textRun"])["content"]
            if isinstance(text, str) and text.strip():
                return True
    return False


def _outline_part(pending: list[int], *, level: int | None, proposed: bool, content: bool) -> None:
    if level is None:
        if content:
            pending.clear()
    else:
        if any(previous >= level for previous in pending):
            _orphan_heading()
        if proposed:
            pending.append(level)


def _orphan_heading() -> None:
    msg = "New headings must introduce content in their own document tab"
    raise ValueError(msg)


def _contentful_authored_headings(
    document: dict[str, JsonValue], positions: dict[tuple[DocsTabId, int], TextBlock]
) -> set[tuple[DocsTabId, int]]:
    result: set[tuple[DocsTabId, int]] = set()
    for tab_id, tab in bodies(document):
        result.update(_contentful_headings_in_tab(tab_id, tab, positions))
    return result


def _contentful_headings_in_tab(
    tab_id: DocsTabId,
    tab: dict[str, JsonValue],
    positions: dict[tuple[DocsTabId, int], TextBlock],
) -> set[tuple[DocsTabId, int]]:
    result: set[tuple[DocsTabId, int]] = set()
    pending: dict[int, tuple[DocsTabId, int]] = {}
    for value in content(tab):
        element = obj(value)
        if "table" in element or "tableOfContents" in element:
            result.update(pending.values())
            pending.clear()
            continue
        if "paragraph" not in element:
            continue
        key = (tab_id, integer(element["startIndex"]))
        block = positions.get(key)
        if block is not None and block.kind is BlockKind.HEADING:
            level = block.level or 1
            while pending and next(reversed(pending)) >= level:
                pending.popitem()
            pending[level] = key
            continue
        if _paragraph_has_content(element):
            result.update(pending.values())
            pending.clear()
    return result
