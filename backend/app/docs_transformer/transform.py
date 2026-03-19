"""Core document transformation: transform_doc and _flush_list."""

from __future__ import annotations

from app.docs_transformer.code_blocks import _group_code_blocks
from app.docs_transformer.footnotes import _build_footnote_section
from app.docs_transformer.headings import _assign_heading_ids_and_resolve_toc
from app.docs_transformer.objects import _extract_positioned_object
from app.docs_transformer.paragraph import _transform_paragraph
from app.docs_transformer.table import _transform_table
from app.docs_transformer.toc import _transform_toc


def transform_doc(doc: dict) -> list[dict]:
    """Transform a Google Docs API document into a list of content blocks."""
    body = doc.get("body", {})
    content = body.get("content", [])
    inline_objects = doc.get("inlineObjects", {})
    positioned_objects = doc.get("positionedObjects", {})
    lists_map = doc.get("lists", {})
    footnotes = doc.get("footnotes", {})

    blocks: list[dict] = []
    current_list: dict | None = None
    footnote_index: dict[str, int] = {}  # footnoteId -> number

    for element in content:
        if "paragraph" in element:
            para = element["paragraph"]

            # Collect positioned (floating) objects anchored to this paragraph
            for obj_id in para.get("positionedObjectIds", []):
                img_block = _extract_positioned_object(
                    obj_id, positioned_objects
                )
                if img_block:
                    _flush_list(blocks, current_list)
                    current_list = None
                    blocks.append(img_block)

            block = _transform_paragraph(
                para, inline_objects, lists_map, footnotes, footnote_index
            )
            if block is None:
                continue

            # Group consecutive list items
            if block.get("_list_item"):
                list_id = block.pop("_list_id", None)
                ordered = block.pop("_list_ordered", False)
                nesting = block.pop("_list_nesting", 0)
                checklist = block.pop("_list_checklist", False)
                del block["_list_item"]

                item = {"runs": block["runs"], "nesting": nesting}

                if current_list and current_list.get("_list_id") == list_id:
                    current_list["items"].append(item)
                else:
                    _flush_list(blocks, current_list)
                    current_list = {
                        "type": "list",
                        "ordered": ordered,
                        "items": [item],
                        "_list_id": list_id,
                    }
                    if checklist:
                        current_list["checklist"] = True
            else:
                _flush_list(blocks, current_list)
                current_list = None
                blocks.append(block)

        elif "table" in element:
            _flush_list(blocks, current_list)
            current_list = None
            blocks.append(
                _transform_table(element["table"], inline_objects, lists_map)
            )

        elif "tableOfContents" in element:
            _flush_list(blocks, current_list)
            current_list = None
            blocks.append(_transform_toc(element["tableOfContents"]))

        elif "sectionBreak" in element:
            pass  # skip

    _flush_list(blocks, current_list)

    # Post-process: detect monospace paragraphs and group into code blocks
    blocks = _group_code_blocks(blocks)

    # Post-process: assign heading IDs and resolve TOC links
    blocks = _assign_heading_ids_and_resolve_toc(blocks)

    # Append footnote definitions if any were referenced
    if footnote_index:
        fn_blocks = _build_footnote_section(
            footnotes, footnote_index, inline_objects, lists_map
        )
        if fn_blocks:
            blocks.append({"type": "horizontal_rule"})
            blocks.extend(fn_blocks)

    return blocks


def _flush_list(blocks: list[dict], current_list: dict | None) -> None:
    if current_list:
        clean = {k: v for k, v in current_list.items() if not k.startswith("_")}
        blocks.append(clean)
