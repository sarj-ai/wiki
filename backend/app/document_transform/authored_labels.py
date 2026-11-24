from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.document_transform.errors import OutputConflictError
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock


_LABELS = frozenset(
    {
        "overview",
        "context",
        "background",
        "goals",
        "decisions",
        "next steps",
        "risks",
        "questions",
        "action items",
        "summary",
    }
)
_MIN_LABELS = 2
MAX_AUTHORED_LABELS = 20


class AuthoredLabelRole(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    source_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    level: Literal[1, 2] = 1


def plan_authored_labels(document: DocumentIR) -> tuple[AuthoredLabelRole, ...]:
    # A single native tab and entirely editable prose avoid ambiguous cell,
    # list, linked/chipped text, and existing authored heading hierarchies.
    if document.source_format != "google_doc" or len(document.tab_ids) != 1:
        return ()
    blocks = tuple(block for block in document.blocks if block.text.strip())
    if blocks and blocks[0].source_named_style == "HEADING_1":
        return _flat_sections(blocks)
    if not all(
        _supports_authored_labels(block, first=index == 0) for index, block in enumerate(blocks)
    ):
        return ()
    roles: list[AuthoredLabelRole] = []
    for index, block in enumerate(blocks):
        if (
            block.kind != BlockKind.PARAGRAPH
            or not block.is_authored_lead_in
            or not block.text.endswith(":")
        ):
            continue
        label = block.text[:-1].casefold()
        if label not in _LABELS:
            return ()
        if index + 1 == len(blocks) or blocks[index + 1].is_authored_lead_in:
            return ()
        roles.append(AuthoredLabelRole(block_id=block.id, source_sha256=block.sha256))
    if not _MIN_LABELS <= len(roles) <= MAX_AUTHORED_LABELS:
        return ()
    return tuple(roles) if _has_unique_authored_labels(blocks, roles) else ()


def _flat_sections(blocks: tuple[TextBlock, ...]) -> tuple[AuthoredLabelRole, ...]:
    first = blocks[0].source_range
    if first is None or first.start_index != 1:
        return ()
    headings = [block for block in blocks if block.kind is BlockKind.HEADING]
    if not headings or any(
        block.level != 1 or block.source_named_style != "HEADING_1" for block in headings
    ):
        return ()
    sections = headings[1:]
    if len(sections) > MAX_AUTHORED_LABELS or any(
        block.requires_font_only_format or block.preserved_visual_authority for block in sections
    ):
        return ()
    return tuple(
        AuthoredLabelRole(block_id=block.id, source_sha256=block.sha256, level=2)
        for block in sections
    )


def _supports_authored_labels(block: TextBlock, *, first: bool) -> bool:
    if not block.editable or block.preserved_visual_authority:
        return False
    if block.native_content or block.native_bullet_json is not None:
        return False
    if block.source_range is None or block.source_range.segment_id is not None:
        return False
    if block.kind == BlockKind.PARAGRAPH:
        return block.source_named_style in {None, "NORMAL_TEXT"}
    return first and block.kind == BlockKind.HEADING and block.source_named_style == "TITLE"


def _has_unique_authored_labels(
    blocks: tuple[TextBlock, ...], roles: Sequence[AuthoredLabelRole]
) -> bool:
    role_ids = {role.block_id for role in roles}
    labels = [block.text.casefold() for block in blocks if block.id in role_ids]
    return len(set(labels)) == len(labels)


def apply_authored_labels(document: DocumentIR, roles: tuple[AuthoredLabelRole, ...]) -> DocumentIR:
    if not roles:
        return document
    by_id = {role.block_id: role for role in roles}
    block_ids = {block.id for block in document.blocks}
    if len(by_id) != len(roles) or not by_id.keys() <= block_ids:
        raise OutputConflictError
    blocks = []
    for block in document.blocks:
        role = by_id.get(block.id)
        if role is not None:
            if role.level == 1:
                matches = block.kind == BlockKind.PARAGRAPH and block.sha256 == role.source_sha256
            else:
                matches = (
                    block.kind == BlockKind.HEADING
                    and block.level == 1
                    and block.source_named_style == "HEADING_1"
                    and block.source_style_text_sha256 == role.source_sha256
                )
            if not matches:
                raise OutputConflictError
            blocks.append(replace(block, kind=BlockKind.HEADING, level=role.level))
        else:
            blocks.append(block)
    return replace(document, blocks=tuple(blocks))
