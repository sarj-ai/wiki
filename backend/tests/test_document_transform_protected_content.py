from copy import deepcopy
from typing import Any
import uuid

import pytest

from app.document_transform.gateway import OutputDraft, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.schemas import DocumentOutput
from app.drive.schemas import MIME_DOC
from app.identifiers import DocsRevisionId, DocsTabId, FileId


def _document() -> dict[str, Any]:
    return {
        "documentId": "source",
        "revisionId": "revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-a"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 10,
                                "paragraph": {
                                    "elements": [
                                        {
                                            "textRun": {
                                                "content": "Read this",
                                                "textStyle": {
                                                    "link": {"url": "https://example.test/a"}
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                },
            }
        ],
    }


async def _protected_digest(document: dict[str, Any]) -> str:
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    inspection = await gateway.inspect_in_place(
        snapshot=SourceSnapshot(
            title="Document",
            parent_folder_id=FileId("folder"),
            modified_time="now",
            revision_id=DocsRevisionId("revision-1"),
            media_type=MIME_DOC,
            google_document=document,
            owned_by_me=True,
            can_edit=True,
            is_personal_drive=True,
        ),
        title="Document",
    )
    return inspection.protected_structure_sha256


async def test_link_destination_change_is_protected_but_brand_styles_are_not() -> None:
    document = _document()
    changed = deepcopy(document)
    run = changed["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0][
        "textRun"
    ]
    run["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    assert await _protected_digest(document) == await _protected_digest(changed)
    run["textStyle"]["link"]["url"] = "https://example.test/b"
    assert await _protected_digest(document) != await _protected_digest(changed)


async def test_equivalent_link_run_splitting_does_not_change_inventory() -> None:
    document = _document()
    changed = deepcopy(document)
    paragraph = changed["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    second = deepcopy(paragraph["elements"][0])
    paragraph["elements"][0]["textRun"]["content"] = "Read "
    second["textRun"]["content"] = "this"
    second["textRun"]["textStyle"]["bold"] = True
    paragraph["elements"].append(second)
    assert await _protected_digest(document) == await _protected_digest(changed)


async def test_bullet_list_identity_and_definition_are_protected() -> None:
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab["body"]["content"][0]["paragraph"]["bullet"] = {"listId": "list-a", "nestingLevel": 0}
    tab["lists"] = {"list-a": {"listProperties": {"nestingLevels": [{"glyphSymbol": "○"}]}}}
    changed = deepcopy(document)
    changed["tabs"][0]["documentTab"]["lists"]["list-a"]["listProperties"]["nestingLevels"][0][
        "glyphSymbol"
    ] = "✓"
    assert await _protected_digest(document) != await _protected_digest(changed)


async def test_native_page_break_terminal_style_normalization_is_not_protected() -> None:
    document = _document()
    paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["elements"] = [
        {
            "startIndex": 1,
            "endIndex": 2,
            "pageBreak": {"textStyle": {"weightedFontFamily": {"fontFamily": "Arial"}}},
        },
        {
            "startIndex": 2,
            "endIndex": 3,
            "textRun": {
                "content": "\n",
                "textStyle": {
                    "bold": False,
                    "weightedFontFamily": {"fontFamily": "DIN NEXT ARABIC"},
                },
            },
        },
    ]
    changed = deepcopy(document)
    changed_parts = changed["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]
    changed_parts[-1]["textRun"]["textStyle"] = {"weightedFontFamily": {"fontFamily": "Nunito"}}

    assert await _protected_digest(document) == await _protected_digest(changed)

    changed_parts[0]["pageBreak"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Comic Sans"
    assert await _protected_digest(document) != await _protected_digest(changed)


async def test_inline_object_terminal_style_remains_protected() -> None:
    document = _document()
    paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["elements"] = [
        {"inlineObjectElement": {"inlineObjectId": "image-a"}},
        {"textRun": {"content": "\n", "textStyle": {"bold": False}}},
    ]
    changed = deepcopy(document)
    changed["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][-1]["textRun"][
        "textStyle"
    ] = {"bold": True}

    assert await _protected_digest(document) != await _protected_digest(changed)


async def test_mixed_page_break_terminal_style_remains_protected() -> None:
    document = _document()
    paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["elements"] = [
        {"pageBreak": {}},
        {"inlineObjectElement": {"inlineObjectId": "image-a"}},
        {"textRun": {"content": "\n", "textStyle": {"bold": False}}},
    ]
    changed = deepcopy(document)
    changed["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][-1]["textRun"][
        "textStyle"
    ] = {"bold": True}

    assert await _protected_digest(document) != await _protected_digest(changed)


@pytest.mark.parametrize("segment", ["footnotes", "footers"], ids=["footnotes", "footers"])
async def test_protected_segment_content_cannot_change(segment: str) -> None:
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab[segment] = {
        "segment-a": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Original"}},
                        ]
                    }
                }
            ]
        }
    }
    changed = deepcopy(document)
    changed["tabs"][0]["documentTab"][segment]["segment-a"]["content"][0]["paragraph"]["elements"][
        0
    ]["textRun"]["content"] = "Changed"
    assert await _protected_digest(document) != await _protected_digest(changed)


async def test_referenced_image_source_is_protected_but_signed_uri_can_refresh() -> None:
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab["body"]["content"].append(
        {
            "startIndex": 10,
            "endIndex": 12,
            "paragraph": {
                "elements": [
                    {"inlineObjectElement": {"inlineObjectId": "image-a"}},
                ]
            },
        }
    )
    tab["inlineObjects"] = {
        "image-a": {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "imageProperties": {
                        "sourceUri": "https://example.test/image",
                        "contentUri": "signed-old",
                    },
                }
            }
        }
    }
    changed = deepcopy(document)
    properties = changed["tabs"][0]["documentTab"]["inlineObjects"]["image-a"][
        "inlineObjectProperties"
    ]["embeddedObject"]["imageProperties"]
    properties["contentUri"] = "signed-new"
    assert await _protected_digest(document) == await _protected_digest(changed)
    properties["sourceUri"] = "https://example.test/other-image"
    assert await _protected_digest(document) != await _protected_digest(changed)


@pytest.mark.parametrize(
    "occupied",
    [
        {"paragraph": {"elements": [{"inlineObjectElement": {"inlineObjectId": "image-a"}}]}},
        {"table": {"rows": 1, "columns": 1}},
    ],
    ids=["image", "table"],
)
def test_copy_does_not_brand_nontext_occupied_header(
    monkeypatch: pytest.MonkeyPatch,
    occupied: dict[str, Any],
) -> None:
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab["documentStyle"] = {"defaultHeaderId": "header-a"}
    tab["headers"] = {"header-a": {"content": [occupied]}}
    original = deepcopy(document)
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _document_id: document)
    draft = OutputDraft(
        output=DocumentOutput(
            file_id=FileId("output"), name="Output", parent_folder_id=FileId("parent")
        ),
        document_id=FileId("output"),
        tab_id=DocsTabId("tab-a"),
        revision_id=DocsRevisionId("revision-1"),
        source_sha256="source",
    )
    prepared = gateway._prepare_brand_shell_sync(uuid.uuid7(), draft)
    assert prepared.headers == ()
    assert prepared.brand_warnings == ("header_conflict:tab-a",)
    assert document == original
