from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations, paragraph_spacing
from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.ir import BlockKind, parse_google_document, utf16_length


def _native_list() -> dict[str, Any]:
    content = []
    index = 1
    for level, text in ((0, "Review evidence"), (1, "Confirm retries"), (0, "Record decision")):
        end = index + utf16_length(text) + 1
        content.append(
            {
                "startIndex": index,
                "endIndex": end,
                "paragraph": {
                    "bullet": {"listId": "numbered-list", "nestingLevel": level},
                    "paragraphStyle": {
                        "namedStyleType": "NORMAL_TEXT",
                        "indentStart": {"magnitude": 36 + level * 18, "unit": "PT"},
                        "indentEnd": {"magnitude": 0, "unit": "PT"},
                        "indentFirstLine": {"magnitude": 18 + level * 18, "unit": "PT"},
                    },
                    "elements": [
                        {"startIndex": index, "endIndex": end, "textRun": {"content": text + "\n"}}
                    ],
                },
            }
        )
        index = end
    return {
        "revisionId": "revision",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab"},
                "documentTab": {
                    "body": {"content": content},
                    "lists": {
                        "numbered-list": {
                            "listProperties": {
                                "nestingLevels": [
                                    {
                                        "glyphType": "DECIMAL",
                                        "glyphFormat": "%0.",
                                        "startNumber": 7,
                                    },
                                    {"glyphType": "ALPHA", "glyphFormat": "%1)", "startNumber": 1},
                                ]
                            }
                        }
                    },
                },
            }
        ],
    }


def _requests(raw: dict[str, Any]) -> list[dict[str, Any]]:
    document = parse_google_document(raw, title="Review")
    return build_copy_mutations(
        document, document, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )


def test_native_numbered_lists_get_compact_rhythm_without_recreating_bullets() -> None:
    raw = _native_list()
    before = deepcopy(raw)
    parsed = parse_google_document(raw, title="Review")
    assert all(block.kind == BlockKind.LIST_ITEM for block in parsed.blocks)
    assert all(block.native_bullet_json for block in parsed.blocks)
    requests = _requests(raw)
    assert not any("createParagraphBullets" in request for request in requests)
    updates = [
        request["updateParagraphStyle"] for request in requests if "updateParagraphStyle" in request
    ]
    assert len(updates) == 3
    for update in updates:
        assert update["paragraphStyle"]["spaceAbove"] == {"magnitude": 0, "unit": "PT"}
        assert update["paragraphStyle"]["spaceBelow"] == {"magnitude": 2, "unit": "PT"}
        assert not any("indent" in field for field in update["fields"].split(","))
    assert raw == before


def test_repeated_list_style_application_has_no_structural_or_visual_drift() -> None:
    raw = _native_list()
    parsed = parse_google_document(raw, title="Review")
    requests = _requests(raw)
    content = raw["tabs"][0]["documentTab"]["body"]["content"]
    for request in requests:
        for method, payload in request.items():
            if method not in {"updateParagraphStyle", "updateTextStyle"}:
                continue
            for item in content:
                if item["startIndex"] != payload["range"]["startIndex"]:
                    continue
                if method == "updateParagraphStyle":
                    item["paragraph"]["paragraphStyle"].update(payload["paragraphStyle"])
                else:
                    item["paragraph"]["elements"][0]["textRun"]["textStyle"] = payload["textStyle"]
    assert parse_google_document(raw, title="Review").semantic_sha256 == parsed.semantic_sha256
    assert _requests(raw) == requests
    assert [item["paragraph"]["bullet"]["nestingLevel"] for item in content] == [0, 1, 0]


@pytest.mark.parametrize(("field", "value"), [("listId", "other"), ("nestingLevel", 2)])
def test_native_list_identity_changes_fail_semantic_preservation(field: str, value: object) -> None:
    raw = _native_list()
    before = parse_google_document(raw, title="Review")
    raw["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["bullet"][field] = value
    assert parse_google_document(raw, title="Review").semantic_sha256 != before.semantic_sha256


def test_numbered_headings_keep_their_native_heading_role() -> None:
    raw = _native_list()
    raw["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["paragraphStyle"][
        "namedStyleType"
    ] = "HEADING_1"
    block = parse_google_document(raw, title="Review").blocks[0]
    assert block.kind == BlockKind.HEADING
    assert paragraph_spacing(block, SarjBrandProfile()).above == 14
    assert not any("createParagraphBullets" in request for request in _requests(raw))


def test_malformed_native_bullet_is_rejected() -> None:
    raw = _native_list()
    raw["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["bullet"] = "invalid"
    with pytest.raises(UnsupportedSourceError):
        parse_google_document(raw, title="Review")
