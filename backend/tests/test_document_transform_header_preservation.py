from copy import deepcopy
from typing import Any

import pytest

from app.document_transform.gateway import OutputDraft, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import DocumentOutput
from app.drive.schemas import MIME_DOC
from app.identifiers import DocsRevisionId, DocsTabId, FileId


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "preserved"),
    [
        ("delete", False),
        ("text", False),
        ("binding", False),
        ("image", False),
        ("signed_uri", True),
        ("unchanged", True),
    ],
)
async def test_copy_verifies_custom_headers(
    monkeypatch: pytest.MonkeyPatch, mutation: str, preserved: bool
) -> None:
    original: dict[str, Any] = {
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t1"},
                "documentTab": {
                    "documentStyle": {"defaultHeaderId": "custom"},
                    "headers": {
                        "custom": {
                            "content": [
                                {
                                    "paragraph": {
                                        "elements": [
                                            {"textRun": {"content": "Confidential contract\n"}}
                                        ]
                                    }
                                }
                            ]
                        }
                    },
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 6,
                                "paragraph": {
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 6,
                                            "textRun": {"content": "Body\n"},
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
    source_tab = original["tabs"][0]["documentTab"]
    source_tab["headers"]["custom"]["content"][0]["paragraph"]["elements"].append(
        {"inlineObjectElement": {"inlineObjectId": "image"}}
    )
    source_tab["inlineObjects"] = {
        "image": {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "imageProperties": {
                        "sourceUri": "https://example.test/original.png",
                        "contentUri": "signed-old",
                    }
                }
            }
        }
    }
    actual = deepcopy(original)
    tab = actual["tabs"][0]["documentTab"]
    if mutation == "delete":
        del tab["headers"]
    elif mutation == "binding":
        del tab["documentStyle"]["defaultHeaderId"]
    elif mutation == "text":
        tab["headers"]["custom"]["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] = (
            "Changed\n"
        )
    elif mutation in {"image", "signed_uri"}:
        field = "sourceUri" if mutation == "image" else "contentUri"
        tab["inlineObjects"]["image"]["inlineObjectProperties"]["embeddedObject"][
            "imageProperties"
        ][field] = "changed"
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _: actual)
    result = await gateway.verify_output(
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
    assert result.structure_preserved is preserved
