from pydantic import JsonValue

from app.document_transform.readability_json import (
    array,
    canonical_style,
    effective_text_style,
    integer,
    obj,
)
from app.document_transform.readability_types import ReadabilityParagraph


def verify_readable_terminator(
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    element: dict[str, JsonValue],
    *,
    target: ReadabilityParagraph,
    planned_style: dict[str, JsonValue],
) -> None:
    index = integer(element["endIndex"]) - 1
    paragraph_style = obj(obj(element["paragraph"])["paragraphStyle"])
    original = effective_text_style(
        document, tab, paragraph_style, _style_at(target.source, index=index)
    )
    observed = effective_text_style(document, tab, paragraph_style, _style_at(element, index=index))
    # Text-style ranges exclude terminators, but Docs may extend their style
    # to an adjacent newline. Permit only those two source-bound outcomes.
    # Preserve properties absent from the write mask, including newline links.
    extended = {**original, **planned_style}
    actual = canonical_style(observed)
    if actual != canonical_style(original) and actual != canonical_style(extended):
        msg = "Unplanned paragraph terminator formatting changed"
        raise ValueError(msg)


def _style_at(element: dict[str, JsonValue], *, index: int) -> dict[str, JsonValue]:
    for value in array(obj(element["paragraph"])["elements"]):
        run = obj(value)
        if integer(run["startIndex"]) <= index < integer(run["endIndex"]):
            text = obj(run["textRun"])
            return obj(text["textStyle"]) if "textStyle" in text else {}
    msg = "Paragraph terminator has no source text run"
    raise ValueError(msg)
