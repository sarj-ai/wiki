from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import override

from pydantic import JsonValue
import pytest

from app.document_transform import google_gateway as native_gateway
from app.document_transform.brand import (
    SarjBrandProfile,
    build_copy_mutations,
    build_copy_text_mutations,
)
from app.document_transform.gateway import OutputDraft, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_font_proof import (
    NativeFontVerificationPolicy,
    capture_native_font_proof,
    verify_native_font_proof,
)
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import DocumentOutput, TextPatch, TransformPhase
from app.identifiers import DocsRevisionId, DocsTabId, FileId
from tests.support import dependency


PREFIX = "The hidden bird can sing from dense scrub while observers hear it from the path. "
SUFFIX = "RSPB species account"
URL = "https://www.rspb.org.uk/birds-and-wildlife/nightingale"


def _document(
    prefix: str = PREFIX, url: str = URL, *, suggested: bool = False
) -> dict[str, JsonValue]:
    cursor = 1
    parts: list[JsonValue] = []
    styled_runs: list[tuple[str, JsonValue]] = [
        (prefix, {"bold": False}),
        (SUFFIX, {"link": {"url": url}}),
        ("\n", {}),
    ]
    for text, style in styled_runs:
        end = cursor + utf16_length(text)
        parts.append(
            {
                "startIndex": cursor,
                "endIndex": end,
                "textRun": {"content": text, "textStyle": style},
            }
        )
        cursor = end
    if suggested:
        first = parts[0]
        assert isinstance(first, dict)
        first["suggestedInsertionIds"] = ["suggestion"]
    return {
        "documentId": "doc",
        "revisionId": "revision",
        "title": "Bird",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": cursor,
                                "paragraph": {
                                    "elements": parts,
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                },
                            }
                        ]
                    }
                },
            }
        ],
    }


def test_linked_paragraph_exposes_prose_but_locks_citation() -> None:
    parsed = parse_google_document(_document(), title="Bird")
    block = parsed.blocks[0]
    assert block.editable
    assert block.protected_suffix == SUFFIX
    replacement = "The hidden bird can sing from scrub. " + SUFFIX
    decision = validate_patches(
        parsed,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert decision.accepted == {block.id: replacement}
    requests = build_copy_text_mutations(parsed, parsed.replace_blocks(decision.accepted))
    assert requests[0]["deleteContentRange"]["range"]["endIndex"] == 1 + utf16_length(PREFIX)
    assert requests[1]["insertText"]["text"] == replacement.removesuffix(SUFFIX)
    assert requests[2]["updateTextStyle"]["textStyle"] == {"bold": False}
    assert "link" in requests[2]["updateTextStyle"]["fields"].split(",")


@pytest.mark.parametrize("replacement", ["The bird can sing. Changed citation", SUFFIX])
def test_citation_tail_cannot_be_rewritten_or_left_without_prose(replacement: str) -> None:
    parsed = parse_google_document(_document(), title="Bird")
    block = parsed.blocks[0]
    decision = validate_patches(
        parsed,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not decision.accepted
    assert decision.findings[0].code == "native_citation_changed"


class _Gateway(GoogleDocumentGateway):
    def __init__(self, document: dict[str, JsonValue]) -> None:
        super().__init__(user=dependency(object()), files=dependency(object()), logo_uri="")
        self.document = document

    @override
    def _get_document_sync(self, file_id: FileId) -> dict[str, JsonValue]:
        assert file_id == "doc"
        return self.document


@pytest.mark.parametrize("url", [URL, "https://example.com/wrong"])
@pytest.mark.parametrize("verify_brand", [False, True])
async def test_native_readback_accepts_rewritten_prose_but_requires_original_links(
    url: str,
    verify_brand: bool,
) -> None:
    original = parse_google_document(_document(), title="Bird")
    prefix = "The hidden bird can sing from scrub. "
    expected = original.replace_blocks({original.blocks[0].id: prefix + SUFFIX})
    source = SourceSnapshot(
        title="Bird",
        parent_folder_id=FileId("folder"),
        modified_time=None,
        revision_id=DocsRevisionId("revision"),
        media_type="application/vnd.google-apps.document",
        google_document=_document(),
    )
    draft = OutputDraft(
        output=DocumentOutput(
            file_id=FileId("doc"), name="Bird", parent_folder_id=FileId("folder")
        ),
        document_id=FileId("doc"),
        tab_id=DocsTabId("tab"),
        revision_id=DocsRevisionId("revision"),
        source_sha256="source",
    )
    verification = await _Gateway(_document(prefix, url)).verify_output(
        draft=draft, source=source, expected=expected, verify_brand=verify_brand
    )
    assert verification.content_preserved is (url == URL)
    assert verification.structure_preserved is (url == URL)


def test_native_link_write_ranges_use_utf16_before_astral_characters() -> None:
    original = parse_google_document(
        _document("A 🐦 bird can sing from dense scrub beside the path. "), title="Bird"
    )
    changed = original.replace_blocks(
        {original.blocks[0].id: "A 🐦 bird can sing from scrub. " + SUFFIX}
    )
    requests = build_copy_text_mutations(original, changed)
    assert requests[0]["deleteContentRange"]["range"]["endIndex"] == 1 + utf16_length(
        original.blocks[0].text.removesuffix(SUFFIX)
    )


def test_both_formats_shortened_prefix_and_leaves_citation_at_its_new_location() -> None:
    original = parse_google_document(_document(), title="Bird")
    prefix = "The hidden bird can sing from scrub. "
    transformed = original.replace_blocks({original.blocks[0].id: prefix + SUFFIX})
    assert transformed.blocks[0].editable
    shortened = parse_google_document(_document(prefix), title="Bird").blocks[0]
    assert shortened.editable
    assert shortened.protected_suffix == SUFFIX
    assert (
        shortened.protected_suffix_links_sha256 == original.blocks[0].protected_suffix_links_sha256
    )
    assert (
        capture_native_font_proof(
            _document(), original, transformed=transformed, target_font_family="Nunito"
        )
        == ()
    )
    requests = build_copy_mutations(
        original, transformed, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    updates = [item["updateTextStyle"] for item in requests if "updateTextStyle" in item]
    font_updates = [item for item in updates if item["fields"] == "weightedFontFamily.fontFamily"]
    assert [item["range"] for item in font_updates] == [
        {"startIndex": 1, "endIndex": 1 + utf16_length(prefix), "tabId": "tab"},
        {
            "startIndex": 1 + utf16_length(prefix),
            "endIndex": 1 + utf16_length(prefix + SUFFIX),
            "tabId": "tab",
        },
    ]
    assert all("fontSize" not in item["textStyle"] for item in updates)


def test_suggestions_keep_rich_paragraph_protected() -> None:
    source = _document(suggested=True)
    assert not parse_google_document(source, title="Bird").blocks[0].editable


def _captured_citation_document() -> dict[str, JsonValue]:
    return json.loads(
        (Path(__file__).parent / "data/native_chips/table_style_after.json").read_text()
    )


def test_preserved_native_citation_receives_the_planned_font_only_brand_check() -> None:
    document = _captured_citation_document()
    parsed = parse_google_document(document, title=str(document["title"]))
    block = next(block for block in parsed.blocks if block.editable and block.protected_suffix)
    assert block.source_range is not None
    source = block.source_range
    paragraph = native_gateway._paragraphs_by_range(tuple(native_gateway._document_tabs(document)))[
        native_gateway.ParagraphRangeKey(
            tab_id=source.tab_id, start_index=source.start_index, end_index=source.end_index
        )
    ]
    checks = native_gateway._paragraph_brand_diagnostics(paragraph, block)
    assert checks["text_style"]


@pytest.mark.parametrize("change", ["none", "color", "size", "bold", "link", "family"])
def test_unchanged_citation_font_proof_keeps_nonfamily_styles_and_link_targets(change: str) -> None:
    before = _captured_citation_document()
    parsed = parse_google_document(before, title=str(before["title"]))
    block = next(block for block in parsed.blocks if block.editable and block.protected_suffix)
    proof = capture_native_font_proof(
        before, parsed, transformed=parsed, target_font_family="Nunito"
    )
    assert any(item.block_id == block.id for item in proof)
    source = block.source_range
    assert source is not None
    after = deepcopy(before)
    tab = next(iter(native_gateway._document_tabs(after)))[1]
    paragraph = next(
        node["paragraph"]
        for node in tab["body"]["content"]
        if node.get("startIndex") == source.start_index
    )
    linked = next(
        part["textRun"]["textStyle"]
        for part in paragraph["elements"]
        if part.get("textRun", {}).get("textStyle", {}).get("link")
    )
    match change:
        case "color":
            linked["foregroundColor"] = {"color": {"rgbColor": {"red": 1}}}
        case "size":
            linked["fontSize"] = {"magnitude": 14, "unit": "PT"}
        case "bold":
            linked["bold"] = True
        case "link":
            linked["link"] = {"url": "https://example.test/changed"}
        case "family":
            linked["weightedFontFamily"] = {"fontFamily": "Arial", "weight": 400}
        case "none":
            pass
        case _:
            raise AssertionError(change)
    assert verify_native_font_proof(
        after, proof, font_policy=NativeFontVerificationPolicy(require_target_family=True)
    ) is (change == "none")
