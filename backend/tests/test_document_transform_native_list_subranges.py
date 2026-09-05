from dataclasses import replace
from typing import Any

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations, mutation_sha256
from app.document_transform.errors import SourceTooLargeError
from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, TextBlock, utf16_length
from app.identifiers import DocsTabId


def _block(text: str) -> TextBlock:
    block = TextBlock(
        id="list",
        kind=BlockKind.LIST_ITEM,
        text=text,
        source_range=SourceRange(11, 11 + utf16_length(text), DocsTabId("tab")),
        native_bullet_json='{"listId":"list","nestingLevel":0}',
        source_named_style="NORMAL_TEXT",
    )
    return replace(block, source_style_text_sha256=block.sha256)


def _requests(block: TextBlock, *, original: TextBlock | None = None) -> list[dict[str, Any]]:
    document = DocumentIR("Review", (block,), "google_doc")
    source = document if original is None else replace(document, blocks=(original,))
    return build_copy_mutations(
        source, document, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )


@pytest.mark.parametrize("text", ["ab", "😀x", "x😀", "e\u0301", "👩\u200d💻", "Review evidence"])
@pytest.mark.parametrize("kind", [BlockKind.LIST_ITEM, BlockKind.CHECKLIST])
def test_native_list_subranges_tile_text_without_splitting_surrogates(
    text: str, kind: BlockKind
) -> None:
    block = replace(_block(text), kind=kind)
    requests = _requests(block)
    styles = [request["updateTextStyle"] for request in requests if "updateTextStyle" in request]
    assert len(styles) == 2
    first, second = styles
    split = 11 + utf16_length(text[: len(text) // 2])
    assert first["range"] == {"startIndex": 11, "endIndex": split, "tabId": "tab"}
    assert second["range"] == {
        "startIndex": split,
        "endIndex": 11 + utf16_length(text),
        "tabId": "tab",
    }
    assert 11 < split < second["range"]["endIndex"]
    assert first["textStyle"] == second["textStyle"]
    assert first["fields"] == second["fields"] == "weightedFontFamily,fontSize,foregroundColor"
    encoded = text.encode("utf-16-le")
    boundary = (split - 11) * 2
    assert encoded[:boundary].decode("utf-16-le") + encoded[boundary:].decode("utf-16-le") == text
    assert not any(
        "insertText" in request or "deleteContentRange" in request for request in requests
    )
    assert _requests(block) == requests
    assert mutation_sha256(_requests(block)) == mutation_sha256(requests)


@pytest.mark.parametrize("text", ["a", "😀", "a\nb", "a\rb"])
def test_ineligible_text_keeps_full_style_request(text: str) -> None:
    styles = [request for request in _requests(_block(text)) if "updateTextStyle" in request]
    assert len(styles) == 1
    assert styles[0]["updateTextStyle"]["range"]["endIndex"] == 11 + utf16_length(text)


@pytest.mark.parametrize(
    "missing", ["source_named_style", "source_style_text_sha256", "native_bullet_json"]
)
def test_unknown_native_provenance_keeps_full_style_request(missing: str) -> None:
    block = replace(_block("Review"), **{missing: None})
    assert sum("updateTextStyle" in request for request in _requests(block)) == 1


def test_mismatched_provider_width_does_not_become_eligible_after_location_reconstruction() -> None:
    block = _block("Review")
    block = replace(block, source_range=SourceRange(11, 99, DocsTabId("tab")))
    assert sum("updateTextStyle" in request for request in _requests(block)) == 1


def test_changed_text_keeps_full_style_request() -> None:
    block = _block("Review")
    assert (
        sum(
            "updateTextStyle" in request
            for request in _requests(replace(block, text="Updated"), original=block)
        )
        == 1
    )


def test_uneditable_native_list_is_not_split() -> None:
    block = _block("Review")
    assert block.source_range is not None
    block = replace(block, editable=False, font_style_ranges=(block.source_range,))
    styles = [
        request["updateTextStyle"] for request in _requests(block) if "updateTextStyle" in request
    ]
    assert len(styles) == 1
    assert styles[0]["fields"] == "weightedFontFamily.fontFamily"


def test_subranges_follow_utf16_shift_from_preceding_edit() -> None:
    preceding = TextBlock(
        "before", BlockKind.PARAGRAPH, "a", source_range=SourceRange(1, 2, DocsTabId("tab"))
    )
    block = _block("😀x")
    original = DocumentIR("Review", (preceding, block), "google_doc")
    transformed = replace(original, blocks=(replace(preceding, text="😀abc"), block))
    requests = build_copy_mutations(
        original, transformed, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    styles = [
        request["updateTextStyle"]["range"] for request in requests if "updateTextStyle" in request
    ][-2:]
    assert styles == [
        {"startIndex": 15, "endIndex": 17, "tabId": "tab"},
        {"startIndex": 17, "endIndex": 18, "tabId": "tab"},
    ]


def test_extra_style_request_counts_toward_mutation_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _requests(_block("ab"))
    monkeypatch.setattr("app.document_transform.brand.MAX_DOCS_MUTATION_REQUESTS", len(requests))
    assert mutation_sha256(requests)
    monkeypatch.setattr(
        "app.document_transform.brand.MAX_DOCS_MUTATION_REQUESTS", len(requests) - 1
    )
    with pytest.raises(SourceTooLargeError):
        mutation_sha256(requests)
