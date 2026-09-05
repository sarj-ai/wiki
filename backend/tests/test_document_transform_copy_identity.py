from copy import deepcopy
from typing import Any

import pytest

from app.document_transform.copy_identity import normalize_copy_list_identity
from app.document_transform.gateway import OutputDraft, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import DocumentOutput
from app.drive.schemas import MIME_DOC
from app.identifiers import DocsRevisionId, DocsTabId, FileId


def _document(first: str = "original-a", second: str = "original-b") -> dict[str, Any]:
    return {
        "documentId": "original",
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t1"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": index * 5 + 1,
                                "endIndex": index * 5 + 6,
                                "paragraph": {
                                    "bullet": {"listId": identifier, "nestingLevel": level},
                                    "elements": [
                                        {
                                            "textRun": {
                                                "content": "Body\n",
                                                "textStyle": {
                                                    "link": {"url": "https://example.test/original"}
                                                },
                                            }
                                        }
                                    ],
                                },
                            }
                            for index, (identifier, level) in enumerate(
                                [(first, 0), (first, 1), (second, 0), (first, 0)]
                            )
                        ]
                    },
                    "lists": {
                        identifier: {
                            "listId": identifier,
                            "listProperties": {
                                "nestingLevels": [
                                    {
                                        "glyphType": "DECIMAL",
                                        "glyphFormat": "%0.",
                                        "startNumber": 1,
                                    },
                                    {"glyphType": "ALPHA", "glyphFormat": "%1.", "startNumber": 1},
                                ]
                            },
                        }
                        for identifier in (first, second)
                    },
                },
            }
        ],
    }


def test_list_projection_is_bijective_idempotent_and_non_mutating() -> None:
    original = _document()
    snapshot = deepcopy(original)
    copied = _document("copy-list-1", "copy-list-0")
    normalized = normalize_copy_list_identity(original)
    assert normalized is not None
    assert normalized == normalize_copy_list_identity(copied)
    assert normalize_copy_list_identity(normalized) == normalized
    assert original == snapshot


def _mutate(tab: dict[str, Any], mutation: str) -> None:
    paragraphs = tab["body"]["content"]
    match mutation:
        case "nesting":
            paragraphs[1]["paragraph"]["bullet"]["nestingLevel"] = 0
        case "restart":
            tab["lists"]["copy-a"]["listProperties"]["nestingLevels"][0]["startNumber"] = 7
        case "glyph":
            tab["lists"]["copy-a"]["listProperties"]["nestingLevels"][0]["glyphType"] = "ROMAN"
        case "regroup":
            paragraphs[1]["paragraph"]["bullet"]["listId"] = "copy-b"
        case "merge":
            paragraphs[2]["paragraph"]["bullet"]["listId"] = "copy-a"
            del tab["lists"]["copy-b"]
        case "split":
            paragraphs[1]["paragraph"]["bullet"]["listId"] = "copy-c"
            tab["lists"]["copy-c"] = deepcopy(tab["lists"]["copy-a"])
            tab["lists"]["copy-c"]["listId"] = "copy-c"
        case "dangling":
            del tab["lists"]["copy-b"]
        case "unknown_definition":
            tab["lists"]["copy-b"]["unknown"] = "must-preserve"
        case "wrong_definition_id":
            tab["lists"]["copy-b"]["listId"] = "copy-a"
        case "link":
            paragraphs[0]["paragraph"]["elements"][0]["textRun"]["textStyle"]["link"]["url"] = (
                "https://example.test/changed"
            )
        case "extra_definition":
            tab["lists"]["orphan"] = deepcopy(tab["lists"]["copy-a"])
        case "object_source":
            tab["inlineObjects"]["image"]["inlineObjectProperties"]["embeddedObject"][
                "imageProperties"
            ]["sourceUri"] = "https://example.test/changed.png"
        case "object_identity":
            paragraphs[0]["paragraph"]["elements"][-1]["inlineObjectElement"]["inlineObjectId"] = (
                "renamed-image"
            )
            tab["inlineObjects"]["renamed-image"] = tab["inlineObjects"].pop("image")
        case "unchanged":
            pass
        case _:
            raise AssertionError(mutation)


@pytest.mark.parametrize(
    "mutation",
    [
        "unchanged",
        "nesting",
        "restart",
        "glyph",
        "regroup",
        "merge",
        "split",
        "dangling",
        "unknown_definition",
        "wrong_definition_id",
        "link",
        "extra_definition",
        "object_source",
        "object_identity",
    ],
)
async def test_copy_list_alpha_equivalence_preserves_exact_semantics(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    original = _document()
    copied = _document("copy-a", "copy-b")
    if mutation.startswith("object_"):
        for document in (original, copied):
            tab = document["tabs"][0]["documentTab"]
            tab["body"]["content"][0]["paragraph"]["elements"].append(
                {"inlineObjectElement": {"inlineObjectId": "image"}}
            )
            tab["inlineObjects"] = {
                "image": {
                    "inlineObjectProperties": {
                        "embeddedObject": {
                            "imageProperties": {"sourceUri": "https://example.test/original.png"}
                        }
                    }
                }
            }
    _mutate(copied["tabs"][0]["documentTab"], mutation)
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _: copied)
    verification = await gateway.verify_output(
        draft=OutputDraft(
            output=DocumentOutput(
                file_id=FileId("copy"), name="Copy", parent_folder_id=FileId("folder")
            ),
            document_id=FileId("copy"),
            tab_id=DocsTabId("t1"),
            revision_id=DocsRevisionId("r1"),
            source_sha256="source",
        ),
        source=SourceSnapshot(
            title="Original",
            parent_folder_id=FileId("folder"),
            modified_time="now",
            revision_id=DocsRevisionId("r1"),
            media_type=MIME_DOC,
            google_document=original,
        ),
        expected=parse_google_document(original, title="Original"),
        verify_brand=False,
    )
    assert verification.structure_preserved is (mutation == "unchanged")
    if mutation == "unchanged":
        assert verification.content_preserved


async def test_in_place_list_identity_remains_strict() -> None:
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    digests = []
    for document in (_document(), _document("copy-a", "copy-b")):
        inspection = await gateway.inspect_in_place(
            snapshot=SourceSnapshot(
                title="Original",
                parent_folder_id=FileId("folder"),
                modified_time="now",
                revision_id=DocsRevisionId("r1"),
                media_type=MIME_DOC,
                google_document=document,
                owned_by_me=True,
                can_edit=True,
                is_personal_drive=True,
            ),
            title="Original",
        )
        digests.append(inspection.protected_structure_sha256)
    assert digests[0] != digests[1]
